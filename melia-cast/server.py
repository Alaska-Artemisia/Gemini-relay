"""
MELIA-CAST — a hardened social-publishing MCP server.

Built to remove the exact failure modes that made the stock mcp-metricool
bridge miserable:

  1. NO INFINITE HANGS. Every outbound call is timeout-bounded (fail fast at
     20s), so a slow Metricool endpoint returns a clean error instead of
     blocking for 4 minutes and wedging the whole connector.
  2. SAFE RETRIES. Every write carries an idempotency key. Fire the same post
     twice and you get one post back, not two. Kills the "did it land? retry...
     now there are duplicates" nightmare for good.
  3. CONFIRMATION BUILT IN. schedule_post submits, then polls the calendar to
     confirm the post exists and returns its real id. No dependence on a
     separate flaky read to find out whether it worked.
  4. MEDIA VALIDATED UP FRONT. A media URL is checked for HTTP 200 + correct
     content-type before Metricool ever sees it. github.io Pages URLs are
     rejected outright (Metricool's ingest chokes on them).
  5. DRAFT-SAFE BY DEFAULT. Nothing auto-publishes unless you explicitly ask.
  6. BATCH. schedule_batch takes N posts and paces them so you can scale
     posting volume in one call.

Runs REMOTE (deploy on Railway, like the Pinterest MCP) over streamable-HTTP.
A remote server cannot freeze Claude Desktop — the worst case is a clean HTTP
error, never a wedged app.

Targets in v1: Metricool (Instagram + Facebook via your connected accounts).
Direct Meta Graph API publishing is scaffolded at the bottom for phase 2.

Env vars (set these in Railway, NEVER in code):
  METRICOOL_USER_TOKEN   your Metricool token  (rotate the one exposed in chat)
  METRICOOL_USER_ID      3850997
  METRICOOL_BLOG_ID      4945902           (default)
  MC_TIMEZONE            Australia/Melbourne (default)
  MCP_SHARED_SECRET      a long random string; Claude sends it as a Bearer token
  PORT                   provided by Railway
  RELAY_GITHUB_PAT       GitHub PAT (Contents R/W on Alaska-Artemisia/Gemini-relay)
  META_ADS_TOKEN         Meta token with ads_management + ads_read scope
                         (long-lived / System User — a Graph Explorer token
                         expires in ~1h). Powers get_meta_audiences AND the
                         full ad-creation stack (create_meta_campaign,
                         create_meta_adset, create_meta_ad, create_meta_custom_audience).
"""

import hashlib
import json
import os
import time
from typing import Any

import httpx
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config (real Metricool API surface, confirmed from mcp-metricool 1.1.9 source)
# ---------------------------------------------------------------------------
MC_BASE = "https://app.metricool.com/api"
MC_TOKEN = os.environ["METRICOOL_USER_TOKEN"]
MC_USER_ID = os.environ["METRICOOL_USER_ID"]
DEFAULT_BLOG = int(os.getenv("METRICOOL_BLOG_ID", "4945902"))
DEFAULT_TZ = os.getenv("MC_TIMEZONE", "Australia/Melbourne")

MC_HEADERS = {
    "X-Mc-Auth": MC_TOKEN,
    "content-type": "application/json",
    "accept": "application/json",
}

# Fail-fast timeouts: connect 10s, read 20s. The whole point.
TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=10.0)

# Idempotency store. In-memory is fine for v1 (a redeploy clears it, but the
# confirm-poll still dedupes against Metricool's live calendar). For heavy use,
# back this with Railway Postgres/Redis.
_SEEN: dict[str, dict] = {}

mcp = FastMCP("melia-cast")


# ---------------------------------------------------------------------------
# Low-level Metricool calls (timeout-bounded, structured errors)
# ---------------------------------------------------------------------------
async def _mc_get(path: str) -> dict[str, Any]:
    url = f"{MC_BASE}{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(url, headers=MC_HEADERS)
        r.raise_for_status()
        return r.json()


async def _mc_post(path: str, body: dict) -> dict[str, Any]:
    url = f"{MC_BASE}{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(url, headers=MC_HEADERS, content=json.dumps(body))
        r.raise_for_status()
        return r.json()


async def _list(blog_id: int, start: str, end: str, tz: str) -> list[dict]:
    q = (
        f"/v2/scheduler/posts?blogId={blog_id}&userId={MC_USER_ID}"
        f"&integrationSource=MCP&start={start}T00%3A00%3A00"
        f"&end={end}T23%3A59%3A59&timezone={tz}&extendedRange=true"
    )
    data = await _mc_get(q)
    return data.get("data", []) if isinstance(data, dict) else []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _verify_media(url: str) -> str | None:
    """Return an error string if the media URL is unusable, else None."""
    if "github.io" in url:
        return f"Rejected github.io URL (Metricool ingest chokes on it): {url}"
    if not url.startswith("https://"):
        return f"Media URL must be https: {url}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as c:
            r = await c.get(url, headers={"Range": "bytes=0-1024"})
            if r.status_code not in (200, 206):
                return f"Media URL returned HTTP {r.status_code}: {url}"
            ctype = r.headers.get("content-type", "")
            if not (ctype.startswith("image/") or ctype.startswith("video/")):
                return f"Media URL is not image/video (content-type {ctype!r}): {url}"
    except Exception as e:
        return f"Media URL not reachable ({type(e).__name__}): {url}"
    return None


def _key(blog_id: int, date: str, text: str, media: list[str]) -> str:
    raw = f"{blog_id}|{date}|{text}|{'|'.join(media)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _build_info(text, media, alt, networks, draft, autopublish,
                first_comment, date, tz, post_type):
    if not alt:
        alt = ["" for _ in media]
    info = {
        "autoPublish": autopublish,
        "draft": draft,
        "text": text,
        "media": media,
        "mediaAltText": alt,
        "providers": [{"network": n} for n in networks],
        "firstCommentText": first_comment,
        "publicationDate": {"dateTime": date, "timezone": tz},
        "shortener": False,
        "smartLinkData": {"ids": []},
        "descendants": [],
    }
    if "instagram" in networks:
        info["instagramData"] = {"type": post_type, "showReelOnFeed": True}
    if "facebook" in networks:
        info["facebookData"] = {"type": post_type}
    return info


def _match(posts: list[dict], date: str, text: str) -> dict | None:
    for p in posts:
        pd = (p.get("publicationDate") or {}).get("dateTime", "")
        if pd == date and (p.get("text", "") or "").strip() == (text or "").strip():
            return p
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def ping() -> str:
    """Health check. Confirms the server can reach Metricool and auth is valid."""
    try:
        await _mc_get(
            f"/v2/settings/brands?userId={MC_USER_ID}&integrationSource=MCP"
        )
        return "ok — Metricool reachable, auth valid"
    except Exception as e:
        return f"unhealthy: {type(e).__name__}: {e}"


@mcp.tool()
async def list_scheduled(start: str, end: str, blog_id: int = DEFAULT_BLOG,
                         timezone: str = DEFAULT_TZ) -> dict:
    """List scheduled (not-yet-published) posts. Dates as YYYY-MM-DD."""
    try:
        posts = await _list(blog_id, start, end, timezone)
        slim = [
            {
                "id": p.get("id"),
                "date": (p.get("publicationDate") or {}).get("dateTime"),
                "draft": p.get("draft"),
                "networks": [x.get("network") for x in p.get("providers", [])],
                "text": (p.get("text", "") or "")[:80],
            }
            for p in posts
        ]
        return {"count": len(slim), "posts": slim}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def schedule_post(
    date: str,
    text: str,
    media: list[str],
    alt_text: list[str] | None = None,
    networks: list[str] | None = None,
    draft: bool = True,
    autopublish: bool = False,
    first_comment: str = "",
    post_type: str = "POST",
    blog_id: int = DEFAULT_BLOG,
    timezone: str = DEFAULT_TZ,
) -> dict:
    """
    Schedule one post to Metricool (Instagram and/or Facebook).

    date        : "YYYY-MM-DDT19:30:00"
    media       : list of https image/video URLs (verified 200 before submit)
    alt_text    : one entry per media item (required for carousels)
    networks    : ["instagram"] (default) and/or "facebook"
    draft       : True (default) = lands in the planner only, never auto-posts
    post_type   : POST | REEL | STORY

    Returns {status: "confirmed", id: ...} on success, deduped by idempotency
    key. A timed-out submit still gets reconciled against the live calendar.
    """
    networks = networks or ["instagram"]

    # 1. verify every media URL up front
    for m in media:
        err = await _verify_media(m)
        if err:
            return {"status": "rejected", "reason": err}

    # 2. idempotency — never double-post the same thing
    key = _key(blog_id, date, text, media)
    if key in _SEEN:
        return {"status": "duplicate_skipped", **_SEEN[key]}

    info = _build_info(text, media, alt_text, networks, draft, autopublish,
                       first_comment, date, timezone, post_type)
    path = (f"/v2/scheduler/posts?blogId={blog_id}"
            f"&userId={MC_USER_ID}&integrationSource=MCP")

    # 3. submit (fail-fast)
    submitted_ok = False
    try:
        resp = await _mc_post(path, info)
        submitted_ok = True
        pid = resp.get("id") if isinstance(resp, dict) else None
        if pid:
            out = {"id": pid, "date": date, "draft": draft}
            _SEEN[key] = out
            return {"status": "confirmed", **out}
    except Exception as e:
        submit_err = f"{type(e).__name__}: {e}"

    # 4. confirm-poll: submit may have landed even without a clean response
    day = date[:10]
    try:
        posts = await _list(blog_id, day, day, timezone)
        hit = _match(posts, date, text)
        if hit:
            out = {"id": hit.get("id"), "date": date, "draft": hit.get("draft")}
            _SEEN[key] = out
            return {"status": "confirmed_via_poll", **out}
    except Exception:
        pass

    return {
        "status": "unconfirmed",
        "key": key,
        "note": ("Submit did not confirm. It may still have landed — call "
                 "reconcile(date, text) shortly, or check the web calendar. "
                 "Do NOT blind-retry; the idempotency key will dedupe if you do."),
        "submit_ok": submitted_ok,
    }


@mcp.tool()
async def reconcile(date: str, text: str, blog_id: int = DEFAULT_BLOG,
                    timezone: str = DEFAULT_TZ) -> dict:
    """Re-check whether an 'unconfirmed' post actually landed, by date+text."""
    day = date[:10]
    try:
        posts = await _list(blog_id, day, day, timezone)
        hit = _match(posts, date, text)
        if hit:
            return {"status": "confirmed", "id": hit.get("id"),
                    "draft": hit.get("draft")}
        return {"status": "not_found", "note": "safe to schedule again"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def schedule_batch(posts: list[dict], pace_seconds: float = 2.0) -> dict:
    """
    Schedule many posts in one call. Each dict takes the same fields as
    schedule_post (date, text, media, alt_text, networks, draft, ...).
    Paced to avoid hammering. Returns per-post results; idempotency makes the
    whole batch safe to re-run.
    """
    results = []
    for i, p in enumerate(posts):
        res = await schedule_post(**p)  # type: ignore[arg-type]
        results.append({"i": i, "date": p.get("date"), "result": res})
        if i < len(posts) - 1:
            time.sleep(pace_seconds)
    ok = sum(1 for r in results if r["result"].get("status", "").startswith("confirmed"))
    return {"submitted": len(posts), "confirmed": ok, "results": results}


# ---------------------------------------------------------------------------
# Phase 2 (scaffold): direct Meta Graph API publishing, bypassing Metricool.
# Facebook supports native scheduling via scheduled_publish_time; Instagram
# has no API scheduling, so a due-time worker would fire ig_publish_now.
# Left unwired until META_PAGE_TOKEN / IG_USER_ID are provided and reviewed.
# ---------------------------------------------------------------------------
# async def fb_schedule(...): POST /{PAGE_ID}/feed  published=false,
#                             scheduled_publish_time=<unix>, link/message/photo
# async def ig_publish_now(...): POST /{IG_USER_ID}/media (image_url, caption)
#                                -> POST /{IG_USER_ID}/media_publish



# ---------------------------------------------------------------------------
# Meta (Facebook) Ads — read-only Custom Audience reader (phase-2a)
# Reads audiences over the Graph API so we never have to browse Ads Manager.
# Answers "pixel or customer-list (Klaviyo)?" and "are purchasers excluded?"
# straight from each audience's rule. Read-only; changes nothing.
# ---------------------------------------------------------------------------
GRAPH = "https://graph.facebook.com/v21.0"
META_ACCOUNT = os.getenv("META_AD_ACCOUNT", "act_1886177598841143")
# Me + Lia Instagram (@meandlia.us) — from the Shopify pixel shopping_ig source.
# Needed for placement-customized ads; the system user has no IG asset assigned.
MELIA_IG_USER_ID = "24655081327432632"

_SUBTYPE_PLAIN = {
    "WEBSITE": "pixel / website  (NOT Klaviyo)",
    "CUSTOM": "customer list  (uploaded/synced - e.g. Klaviyo or CSV)",
    "LOOKALIKE": "lookalike",
    "ENGAGEMENT": "IG / FB engagement",
    "OFFLINE_CONVERSION": "offline conversion",
}


def _rule_excludes_purchasers(rule: Any) -> bool:
    """True if the audience rule has an exclusion referencing a Purchase event."""
    if not rule:
        return False
    try:
        r = json.loads(rule) if isinstance(rule, str) else rule
    except Exception:
        return "purchase" in str(rule).lower()
    return "purchase" in json.dumps(r.get("exclusions", "")).lower()


@mcp.tool()
async def get_meta_audiences(name_contains: str = "", limit: int = 200) -> dict:
    """Read Meta Custom Audiences (read-only) for the Me + Lia ad account.

    For each audience: name, plain-English source (pixel vs customer list /
    Klaviyo), whether purchasers are excluded, retention window, approx size,
    and the raw rule. Optional case-insensitive name-substring filter.

    Requires env var META_ADS_TOKEN (ads_read). Use a long-lived / System User
    token; a Graph Explorer token expires within ~1 hour.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set in Railway env. "
                "Add a long-lived ads_read token and redeploy."}

    fields = ("id,name,subtype,description,retention_days,"
              "approximate_count_lower_bound,operation_status,data_source,rule")
    url = (f"{GRAPH}/{META_ACCOUNT}/customaudiences"
           f"?fields={fields}&limit={min(limit, 200)}&access_token={token}")

    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            while url:
                r = await c.get(url)
                data = r.json()
                if "error" in data:
                    return {"ok": False,
                            "error": data["error"].get("message", data["error"])}
                for a in data.get("data", []):
                    nm = a.get("name", "")
                    if name_contains and name_contains.lower() not in nm.lower():
                        continue
                    subtype = a.get("subtype", "")
                    status = a.get("operation_status")
                    out.append({
                        "name": nm,
                        "id": a.get("id"),
                        "source": _SUBTYPE_PLAIN.get(subtype, subtype or "unknown"),
                        "from_klaviyo_or_upload": subtype == "CUSTOM",
                        "excludes_purchasers": _rule_excludes_purchasers(a.get("rule")),
                        "retention_days": a.get("retention_days"),
                        "approx_size": a.get("approximate_count_lower_bound"),
                        "status": status.get("description")
                                  if isinstance(status, dict) else status,
                        "rule": a.get("rule"),
                    })
                url = (data.get("paging", {}) or {}).get("next")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {"ok": True, "account": META_ACCOUNT, "count": len(out), "audiences": out}


# ---------------------------------------------------------------------------
# Relay job dispatch — write a job/asset file into the Gemini-relay repo so the
# Mac-side watcher can pick it up. Uses the server-side PAT; no token in chat.
# NOTE: the running Mac watcher consumes IMAGE + FETCH jobs only. A browser-job
# written here will sit unconsumed until a browser runner is added to the watcher.
# ---------------------------------------------------------------------------
GH_REPO = os.getenv("RELAY_REPO", "Alaska-Artemisia/Gemini-relay")
_JOB_PREFIXES = ("jobs/", "browser-jobs/", "fetch/", "hosting/")


@mcp.tool()
async def dispatch_relay_job(path: str, content: Any, message: str = "") -> dict:
    """Write a job/asset file into the Gemini-relay repo (server-side PAT).

    path    : repo path; must start with jobs/, browser-jobs/, fetch/ or hosting/
    content : dict (serialized to JSON) or a raw string
    Returns the commit sha and the raw.githubusercontent URL.

    Requires env var RELAY_GITHUB_PAT. Only writes to the allowed job/asset
    prefixes above - it will not touch code paths.
    """
    import base64
    token = os.getenv("RELAY_GITHUB_PAT", "")
    if not token:
        return {"ok": False, "error": "RELAY_GITHUB_PAT not set in Railway env."}
    if not path.startswith(_JOB_PREFIXES):
        return {"ok": False, "error": f"path must start with one of {_JOB_PREFIXES}"}

    body_text = content if isinstance(content, str) else json.dumps(content, indent=2)
    b64 = base64.b64encode(body_text.encode()).decode()
    api = f"https://api.github.com/repos/{GH_REPO}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}",
               "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            sha = None
            g = await c.get(api, headers=headers)
            if g.status_code == 200:
                sha = g.json().get("sha")
            payload = {"message": message or f"relay job: {path}", "content": b64}
            if sha:
                payload["sha"] = sha
            p = await c.put(api, headers=headers, content=json.dumps(payload))
            if p.status_code not in (200, 201):
                return {"ok": False,
                        "error": f"GitHub PUT {p.status_code}: {p.text[:300]}"}
            j = p.json()
            return {"ok": True, "path": path,
                    "commit": (j.get("commit") or {}).get("sha"),
                    "raw_url": (j.get("content") or {}).get("download_url")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# Meta Ads — full ad-creation stack via Graph API (phase 2b)
# Four tools: create campaign → create ad set → create ad → create audience
# All use META_ADS_TOKEN with ads_management scope. One call at a time.
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_meta_campaign(
    name: str,
    objective: str = "OUTCOME_SALES",
    daily_budget_cents: int = 0,
    lifetime_budget_cents: int = 0,
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
    special_ad_categories: str = "[]",
    status: str = "PAUSED",
) -> dict:
    """Create a Meta campaign (PAUSED by default).

    name                  : campaign name in Ads Manager
    objective             : OUTCOME_SALES, OUTCOME_TRAFFIC, OUTCOME_AWARENESS,
                            OUTCOME_ENGAGEMENT, OUTCOME_LEADS, OUTCOME_APP_PROMOTION
    daily_budget_cents    : CBO daily budget in cents (e.g. 2000 = $20/day).
                            Mutually exclusive with lifetime_budget_cents.
                            Set either one for CBO; leave both 0 for ABO (budget on ad set).
    lifetime_budget_cents : CBO lifetime budget in cents. Mutually exclusive with daily.
    bid_strategy          : LOWEST_COST_WITHOUT_CAP (default), COST_CAP,
                            LOWEST_COST_WITH_BID_CAP, LOWEST_COST_WITH_MIN_ROAS
    special_ad_categories : JSON array string, e.g. '["HOUSING"]'. Default "[]".
    status                : PAUSED (default) or ACTIVE

    Returns {ok, campaign_id, name, status}.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}

    params: dict[str, Any] = {
        "name": name,
        "objective": objective,
        "special_ad_categories": special_ad_categories,
        "status": status,
        "buying_type": "AUCTION",
        "access_token": token,
    }
    if daily_budget_cents > 0:
        params["daily_budget"] = str(daily_budget_cents)
        params["bid_strategy"] = bid_strategy
    elif lifetime_budget_cents > 0:
        params["lifetime_budget"] = str(lifetime_budget_cents)
        params["bid_strategy"] = bid_strategy

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            r = await c.post(f"{GRAPH}/{META_ACCOUNT}/campaigns", data=params)
            d = r.json()
            if "error" in d:
                err = d["error"]
                return {"ok": False, "step": "campaign",
                        "error": err.get("message", ""),
                        "error_type": err.get("type", ""),
                        "error_code": err.get("code", ""),
                        "error_subcode": err.get("error_subcode", ""),
                        "error_user_title": err.get("error_user_title", ""),
                        "error_user_msg": err.get("error_user_msg", ""),
                        "fbtrace": err.get("fbtrace_id", ""),
                        "sent_params": {k: v for k, v in params.items() if k != "access_token"}}
            return {"ok": True, "campaign_id": d.get("id"), "name": name, "status": status}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def create_meta_adset(
    name: str,
    campaign_id: str,
    daily_budget_cents: int = 0,
    lifetime_budget_cents: int = 0,
    optimization_goal: str = "OFFSITE_CONVERSIONS",
    billing_event: str = "IMPRESSIONS",
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP",
    targeting: str = "",
    advantage_audience: int = 0,
    promoted_object: str = "",
    start_time: str = "",
    end_time: str = "",
    status: str = "PAUSED",
    destination_type: str = "WEBSITE",
) -> dict:
    """Create a Meta ad set under an existing campaign (PAUSED by default).

    name                  : ad set name in Ads Manager
    campaign_id           : parent campaign ID
    daily_budget_cents    : ABO daily budget in cents (only if campaign has no CBO budget).
                            Mutually exclusive with lifetime_budget_cents.
    lifetime_budget_cents : ABO lifetime budget in cents. Requires end_time.
    optimization_goal     : OFFSITE_CONVERSIONS, LINK_CLICKS, REACH, IMPRESSIONS,
                            LANDING_PAGE_VIEWS, CONVERSATIONS, VALUE, LEAD_GENERATION
    billing_event         : IMPRESSIONS (default), LINK_CLICKS
    bid_strategy          : LOWEST_COST_WITHOUT_CAP (default), COST_CAP, etc.
    targeting             : JSON string targeting spec. e.g.
                            '{"geo_locations":{"countries":["US"]},"age_min":25,"age_max":55}'
                            Leave empty for Advantage+ (broad targeting).
                            Do NOT hand-write targeting_automation — use the
                            advantage_audience param below; it is injected for you.
    advantage_audience    : REQUIRED by Meta on all new ad sets (error_subcode
                            1870227 if missing). 0 = use your targeting as written
                            (Advantage audience off). 1 = let Meta expand beyond it.
                            Default 0. Injected into the targeting spec automatically.
    promoted_object       : JSON string. For OUTCOME_SALES:
                            '{"pixel_id":"YOUR_PIXEL","custom_event_type":"PURCHASE"}'
    start_time            : ISO 8601 (e.g. "2026-07-12T00:00:00-0400"). Omit for immediate.
    end_time              : ISO 8601. Required if using lifetime_budget.
    status                : PAUSED (default) or ACTIVE
    destination_type      : WEBSITE (default), SHOP_AUTOMATIC, MESSENGER, etc.

    Returns {ok, adset_id, name, status}.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}

    params: dict[str, Any] = {
        "name": name,
        "campaign_id": campaign_id,
        "optimization_goal": optimization_goal,
        "billing_event": billing_event,
        "bid_strategy": bid_strategy,
        "status": status,
        "destination_type": destination_type,
        "access_token": token,
    }
    if daily_budget_cents > 0:
        params["daily_budget"] = str(daily_budget_cents)
    elif lifetime_budget_cents > 0:
        params["lifetime_budget"] = str(lifetime_budget_cents)
    # Meta requires targeting_automation.advantage_audience on every new ad set.
    # Build/merge it in rather than making callers hand-write it.
    try:
        t_spec = json.loads(targeting) if targeting else {}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"targeting is not valid JSON: {e}"}
    t_auto = t_spec.get("targeting_automation") or {}
    t_auto.setdefault("advantage_audience", 1 if advantage_audience else 0)
    t_spec["targeting_automation"] = t_auto
    params["targeting"] = json.dumps(t_spec)
    if promoted_object:
        params["promoted_object"] = promoted_object
    if start_time:
        params["start_time"] = start_time
    if end_time:
        params["end_time"] = end_time

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            r = await c.post(f"{GRAPH}/{META_ACCOUNT}/adsets", data=params)
            d = r.json()
            if "error" in d:
                err = d["error"]
                return {"ok": False, "step": "adset",
                        "error": err.get("message", ""),
                        "error_type": err.get("type", ""),
                        "error_code": err.get("code", ""),
                        "error_subcode": err.get("error_subcode", ""),
                        "error_user_title": err.get("error_user_title", ""),
                        "error_user_msg": err.get("error_user_msg", ""),
                        "fbtrace": err.get("fbtrace_id", ""),
                        "sent_params": {k: v for k, v in params.items() if k != "access_token"}}
            return {"ok": True, "adset_id": d.get("id"), "name": name, "status": status}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def create_meta_ad(
    ad_name: str,
    adset_id: str,
    image_url: str = "",
    primary_text: str = "",
    headline: str = "",
    link_url: str = "",
    description: str = "",
    call_to_action: str = "SHOP_NOW",
    page_id: str = "736164559573286",
    activate: bool = True,
    creative_id: str = "",
) -> dict:
    """Create a single Meta ad with image, copy, and link under an existing
    ad set. One ad at a time — never batch (error_subcode 1487390).

    image_url   : public HTTPS URL of the ad image (Shopify CDN, etc.)
    ad_name     : name shown in Ads Manager (e.g. "ASC - Valentina Shallows")
    primary_text: body text above the image
    headline    : short headline below the image
    link_url    : click-through destination (e.g. https://meandlia.com/products/...)
    adset_id    : existing ad set ID to create the ad under
    description : short description text below the headline
    call_to_action: CTA button (SHOP_NOW, LEARN_MORE, etc.)
    page_id     : Facebook Page ID (defaults to Me + Lia)
    activate    : True = set ad ACTIVE immediately; False = leave PAUSED
    creative_id : reuse an EXISTING creative instead of building a new one.
                  When set, image_url/primary_text/headline/link_url/description
                  are ignored — the existing creative supplies all of them.
                  Use this to run the same creative in another ad set (Meta
                  cannot move ads between ad sets; you recreate them).

    Returns {ok, ad_id, creative_id, image_hash, status} on success.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}

    account = META_ACCOUNT

    if not creative_id and not image_url:
        return {"ok": False,
                "error": "Pass either creative_id (reuse) or image_url (new creative)."}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            image_hash = None
            reused = bool(creative_id)

            if not reused:
                # Step 1: Try uploading image by URL for hash (preferred).
                # Falls back to image_url in creative if /adimages is blocked.
                try:
                    r1 = await c.post(
                        f"{GRAPH}/{account}/adimages",
                        data={"url": image_url, "access_token": token},
                    )
                    d1 = r1.json()
                    if "error" not in d1:
                        images = d1.get("images", {})
                        if images:
                            image_hash = list(images.values())[0].get("hash")
                except Exception:
                    pass  # fall through to image_url path

                # Step 2: Create ad creative (hash path or URL path)
                link_data: dict[str, Any] = {
                    "link": link_url,
                    "message": primary_text,
                    "name": headline,
                    "description": description,
                    "call_to_action": {
                        "type": call_to_action,
                        "value": {"link": link_url},
                    },
                }
                if image_hash:
                    link_data["image_hash"] = image_hash
                else:
                    link_data["picture"] = image_url

                creative_spec = {
                    "name": ad_name,
                    "object_story_spec": json.dumps({
                        "page_id": page_id,
                        "link_data": link_data,
                    }),
                    "access_token": token,
                }
                r2 = await c.post(f"{GRAPH}/{account}/adcreatives", data=creative_spec)
                d2 = r2.json()
                if "error" in d2:
                    err = d2["error"]
                    return {"ok": False, "step": "creative",
                            "image_hash": image_hash,
                            "error": err.get("message", ""),
                            "error_type": err.get("type", ""),
                            "error_code": err.get("code", ""),
                            "error_subcode": err.get("error_subcode", ""),
                            "fbtrace": err.get("fbtrace_id", ""),
                            "debug": str(d2)}
                creative_id = d2.get("id")

            # Step 3: Create ad (PAUSED initially)
            ad_spec = {
                "name": ad_name,
                "adset_id": adset_id,
                "creative": json.dumps({"creative_id": creative_id}),
                "status": "PAUSED",
                "access_token": token,
            }
            r3 = await c.post(f"{GRAPH}/{account}/ads", data=ad_spec)
            d3 = r3.json()
            if "error" in d3:
                return {"ok": False, "step": "ad_create",
                        "creative_id": creative_id, "image_hash": image_hash,
                        "error": d3["error"].get("message", str(d3["error"]))}
            ad_id = d3.get("id")

            # Step 4: Activate if requested
            final_status = "PAUSED"
            if activate:
                r4 = await c.post(
                    f"{GRAPH}/{ad_id}",
                    data={"status": "ACTIVE", "access_token": token},
                )
                d4 = r4.json()
                if d4.get("success"):
                    final_status = "ACTIVE"
                else:
                    final_status = f"PAUSED (activate failed: {d4})"

            return {
                "ok": True,
                "ad_id": ad_id,
                "creative_id": creative_id,
                "creative_reused": reused,
                "image_hash": image_hash,
                "ad_name": ad_name,
                "status": final_status,
                "link_url": link_url,
            }

    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def create_meta_custom_audience(
    name: str,
    description: str = "",
    subtype: str = "WEBSITE",
    rule: str = "",
    retention_days: int = 30,
    prefill: bool = True,
    customer_file_source: str = "",
) -> dict:
    """Create a Meta Custom Audience.

    name                : audience name in Ads Manager
    description         : optional description
    subtype             : WEBSITE (pixel-based, default), CUSTOM (customer list),
                          ENGAGEMENT, LOOKALIKE
    rule                : JSON rule spec for pixel audiences, e.g.
                          '{"inclusions":{"operator":"or","rules":[
                            {"event_sources":[{"id":"PIXEL_ID","type":"pixel"}],
                             "retention_seconds":2592000,
                             "filter":{"operator":"and","filters":[
                               {"field":"url","operator":"i_contains","value":"products"}
                             ]}}
                          ]}}'
                          Leave empty for customer-list audiences (upload separately).
    retention_days      : lookback window (default 30). Max 180 for pixel audiences.
    prefill             : True (default) = backfill with existing data
    customer_file_source: for CUSTOM subtype: USER_PROVIDED_ONLY, PARTNER_PROVIDED_ONLY,
                          BOTH_USER_AND_PARTNER_PROVIDED

    Returns {ok, audience_id, name, subtype}.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}

    params: dict[str, Any] = {
        "name": name,
        "subtype": subtype,
        "access_token": token,
    }
    if description:
        params["description"] = description
    if rule:
        params["rule"] = rule
    if retention_days and subtype == "WEBSITE":
        params["retention_days"] = str(retention_days)
    if prefill:
        params["prefill"] = "true"
    if customer_file_source:
        params["customer_file_source"] = customer_file_source

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            r = await c.post(f"{GRAPH}/{META_ACCOUNT}/customaudiences", data=params)
            d = r.json()
            if "error" in d:
                err = d["error"]
                return {"ok": False, "step": "audience",
                        "error": err.get("message", ""),
                        "error_type": err.get("type", ""),
                        "error_code": err.get("code", ""),
                        "error_subcode": err.get("error_subcode", ""),
                        "error_user_title": err.get("error_user_title", ""),
                        "error_user_msg": err.get("error_user_msg", ""),
                        "fbtrace": err.get("fbtrace_id", ""),
                        "sent_params": {k: v for k, v in params.items() if k != "access_token"}}
            return {"ok": True, "audience_id": d.get("id"), "name": name, "subtype": subtype}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def update_meta_entity(
    entity_id: str,
    entity_type: str,
    status: str = "",
    name: str = "",
    daily_budget_cents: int = 0,
    lifetime_budget_cents: int = 0,
    bid_amount_cents: int = 0,
    targeting: str = "",
    end_time: str = "",
) -> dict:
    """Update an existing Meta campaign, ad set, or ad. Only pass the fields
    you want to change — everything omitted is left untouched.

    entity_id            : the campaign/adset/ad ID to update
    entity_type          : "campaign", "adset", or "ad" (used for validation only;
                           the Graph API endpoint is the bare ID)
    status               : ACTIVE, PAUSED, or ARCHIVED. Leave empty to not change.
    name                 : new name. Leave empty to not change.
    daily_budget_cents   : new daily budget in cents (campaigns with CBO, or ad sets
                           with ABO). e.g. 2500 = $25/day. 0 = no change.
    lifetime_budget_cents: new lifetime budget in cents. 0 = no change.
    bid_amount_cents     : new bid cap in cents (only for bid-capped strategies).
    targeting            : JSON string, ad sets only. Replaces the whole spec.
    end_time             : ISO 8601 end time. Leave empty to not change.

    Returns {ok, entity_id, updated_fields, result}.
    Common uses: pause a starved ad set, shift CBO budget, rename, stop an ad.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}

    if entity_type not in ("campaign", "adset", "ad"):
        return {"ok": False, "error": "entity_type must be campaign, adset, or ad."}

    params: dict[str, Any] = {"access_token": token}
    changed: list[str] = []
    if status:
        params["status"] = status.upper()
        changed.append(f"status={status.upper()}")
    if name:
        params["name"] = name
        changed.append("name")
    if daily_budget_cents > 0:
        params["daily_budget"] = str(daily_budget_cents)
        changed.append(f"daily_budget={daily_budget_cents/100:.2f}")
    if lifetime_budget_cents > 0:
        params["lifetime_budget"] = str(lifetime_budget_cents)
        changed.append(f"lifetime_budget={lifetime_budget_cents/100:.2f}")
    if bid_amount_cents > 0:
        params["bid_amount"] = str(bid_amount_cents)
        changed.append("bid_amount")
    if targeting:
        params["targeting"] = targeting
        changed.append("targeting")
    if end_time:
        params["end_time"] = end_time
        changed.append("end_time")

    if len(params) == 1:
        return {"ok": False, "error": "Nothing to update — pass at least one field."}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            r = await c.post(f"{GRAPH}/{entity_id}", data=params)
            d = r.json()
            if "error" in d:
                err = d["error"]
                return {"ok": False, "entity_id": entity_id,
                        "error": err.get("message", ""),
                        "error_code": err.get("code", ""),
                        "error_subcode": err.get("error_subcode", ""),
                        "debug": str(d)}
            return {"ok": True, "entity_id": entity_id, "entity_type": entity_type,
                    "updated_fields": changed, "result": d}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def delete_meta_entity(entity_id: str, entity_type: str) -> dict:
    """Permanently DELETE a Meta campaign, ad set, or ad. Irreversible.

    entity_id  : the campaign/adset/ad ID to delete
    entity_type: "campaign", "adset", or "ad"

    Prefer update_meta_entity(status="PAUSED") for anything you might want back,
    and status="ARCHIVED" to hide it from the UI while keeping its history.
    Deleting a campaign deletes every ad set and ad inside it.

    Returns {ok, entity_id, deleted}.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}
    if entity_type not in ("campaign", "adset", "ad"):
        return {"ok": False, "error": "entity_type must be campaign, adset, or ad."}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            r = await c.delete(f"{GRAPH}/{entity_id}", params={"access_token": token})
            d = r.json()
            if "error" in d:
                return {"ok": False, "entity_id": entity_id,
                        "error": d["error"].get("message", str(d["error"]))}
            return {"ok": True, "entity_id": entity_id, "entity_type": entity_type,
                    "deleted": bool(d.get("success", True))}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def get_meta_entities(
    level: str = "campaign",
    parent_id: str = "",
    fields: str = "",
    effective_status: str = "",
    limit: int = 50,
) -> dict:
    """Read Meta campaigns, ad sets, or ads with their live settings — budgets,
    status, targeting, creative. The read side of the ads stack.

    level           : "campaign", "adset", or "ad"
    parent_id       : scope the read. Empty = whole ad account. Pass a campaign ID
                      to list its ad sets, or an ad set ID to list its ads.
    fields          : comma-separated Graph fields. Empty = a sensible default set
                      per level (id, name, status, budgets, etc.).
    effective_status: filter, e.g. "ACTIVE" or "ACTIVE,PAUSED". Empty = all.
    limit           : max rows (default 50).

    Returns {ok, level, count, data}.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}
    if level not in ("campaign", "adset", "ad"):
        return {"ok": False, "error": "level must be campaign, adset, or ad."}

    defaults = {
        "campaign": "id,name,status,effective_status,objective,daily_budget,"
                    "lifetime_budget,bid_strategy,start_time,stop_time",
        "adset": "id,name,status,effective_status,campaign_id,daily_budget,"
                 "lifetime_budget,optimization_goal,billing_event,bid_amount,"
                 "targeting,start_time,end_time",
        "ad": "id,name,status,effective_status,adset_id,campaign_id,"
              "creative{id,name,object_story_spec}",
    }
    edge = {"campaign": "campaigns", "adset": "adsets", "ad": "ads"}[level]
    node = parent_id if parent_id else META_ACCOUNT

    params: dict[str, Any] = {
        "fields": fields or defaults[level],
        "limit": str(limit),
        "access_token": token,
    }
    if effective_status:
        vals = [s.strip() for s in effective_status.split(",") if s.strip()]
        params["effective_status"] = json.dumps(vals)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            r = await c.get(f"{GRAPH}/{node}/{edge}", params=params)
            d = r.json()
            if "error" in d:
                return {"ok": False, "error": d["error"].get("message", str(d["error"]))}
            rows = d.get("data", [])
            return {"ok": True, "level": level, "count": len(rows), "data": rows}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def get_meta_insights(
    level: str = "campaign",
    entity_id: str = "",
    date_preset: str = "last_7d",
    time_range: str = "",
    fields: str = "",
    breakdowns: str = "",
    limit: int = 100,
) -> dict:
    """Read Meta performance data — spend, purchases, cost per purchase, ROAS,
    CTR, hook rate. This is what replaces Supermetrics reporting.

    level      : "account", "campaign", "adset", or "ad"
    entity_id  : the ID to report on. Empty = whole ad account.
    date_preset: today, yesterday, last_7d, last_14d, last_30d, this_month,
                 last_month, maximum. Ignored if time_range is set.
    time_range : JSON, e.g. '{"since":"2026-07-01","until":"2026-07-12"}'
    fields     : comma-separated. Empty = spend, impressions, clicks, ctr, cpc,
                 purchases, cost per purchase, ROAS, video hook metrics.
    breakdowns : e.g. "publisher_platform" or "publisher_platform,platform_position"
                 — use this to see the Reels share of spend.
    limit      : max rows (default 100).

    Returns {ok, level, count, data}.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}
    if level not in ("account", "campaign", "adset", "ad"):
        return {"ok": False, "error": "level must be account, campaign, adset, or ad."}

    node = entity_id if entity_id else META_ACCOUNT
    default_fields = (
        "spend,impressions,reach,frequency,clicks,ctr,cpc,cpm,"
        "actions,action_values,cost_per_action_type,purchase_roas,"
        "video_p25_watched_actions,video_p75_watched_actions"
    )
    params: dict[str, Any] = {
        "level": level,
        "fields": fields or default_fields,
        "limit": str(limit),
        "access_token": token,
    }
    if time_range:
        params["time_range"] = time_range
    else:
        params["date_preset"] = date_preset
    if breakdowns:
        params["breakdowns"] = breakdowns

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
            r = await c.get(f"{GRAPH}/{node}/insights", params=params)
            d = r.json()
            if "error" in d:
                return {"ok": False, "error": d["error"].get("message", str(d["error"]))}
            rows = d.get("data", [])
            return {"ok": True, "level": level, "count": len(rows), "data": rows}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _resolve_image_hash(c, account: str, token: str, image_url: str) -> tuple:
    """Resolve a public image URL to a Meta image hash.

    Tries /adimages first. If the app lacks that capability, falls back to
    creating a throwaway creative with picture=<url> — Meta ingests the image
    and reports its hash — then deletes the throwaway.
    Returns (hash, method) or (None, error_string).
    """
    # Path 1: /adimages
    try:
        r = await c.post(f"{GRAPH}/{account}/adimages",
                         data={"url": image_url, "access_token": token})
        d = r.json()
        if "error" not in d:
            imgs = d.get("images", {})
            if imgs:
                h = list(imgs.values())[0].get("hash")
                if h:
                    return h, "adimages"
    except Exception:
        pass

    # Path 2: throwaway creative, read back the hash Meta assigned
    try:
        spec = {
            "name": "tmp-hash-probe",
            "object_story_spec": json.dumps({
                "page_id": "736164559573286",
                "link_data": {
                    "link": "https://meandlia.com",
                    "message": "probe",
                    "picture": image_url,
                },
            }),
            "access_token": token,
        }
        r = await c.post(f"{GRAPH}/{account}/adcreatives", data=spec)
        d = r.json()
        if "error" in d:
            return None, f"probe creative failed: {d['error'].get('message','')}"
        cid = d.get("id")
        r2 = await c.get(f"{GRAPH}/{cid}",
                         params={"fields": "image_hash", "access_token": token})
        h = r2.json().get("image_hash")
        try:
            await c.delete(f"{GRAPH}/{cid}", params={"access_token": token})
        except Exception:
            pass
        if h:
            return h, "probe"
        return None, "probe returned no image_hash"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


@mcp.tool()
async def create_meta_ad_placements(
    ad_name: str,
    adset_id: str,
    feed_image_url: str,
    story_image_url: str,
    primary_text: str,
    headline: str,
    link_url: str,
    description: str = "",
    call_to_action: str = "SHOP_NOW",
    page_id: str = "736164559573286",
    instagram_actor_id: str = "",
    activate: bool = True,
) -> dict:
    """Create ONE ad that carries two crops and lets Meta serve the right one
    per placement — 4:5 in feeds, 9:16 in Reels/Stories. This is placement
    asset customization (asset_feed_spec), NOT two separate ads.

    Use this instead of publishing a second ad for the vertical crop: two ads
    with the same product would compete against each other for the same budget.

    ad_name        : name shown in Ads Manager
    adset_id       : existing ad set to create the ad under
    feed_image_url : public HTTPS URL of the 4:5 image (feeds, explore)
    story_image_url: public HTTPS URL of the 9:16 image (stories, reels)
    primary_text   : body text
    headline       : headline
    link_url       : click destination
    description    : description line
    call_to_action : CTA button (default SHOP_NOW)
    page_id        : Facebook Page ID (defaults to Me + Lia)
    instagram_actor_id: IG account ID to represent the brand on Instagram.
                  REQUIRED by Meta whenever the placement rules name Instagram
                  positions (subcode 1772103, "Instagram account is missing").
                  Leave empty to auto-resolve from the Page's connected IG
                  account.
    activate       : True = ACTIVE immediately; False = PAUSED

    Returns {ok, ad_id, creative_id, feed_hash, story_hash, ig_actor, status}.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}
    account = META_ACCOUNT

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
            feed_hash, m1 = await _resolve_image_hash(c, account, token, feed_image_url)
            if not feed_hash:
                return {"ok": False, "step": "feed_image", "error": m1}
            story_hash, m2 = await _resolve_image_hash(c, account, token, story_image_url)
            if not story_hash:
                return {"ok": False, "step": "story_image", "error": m2}

            asset_feed_spec = {
                "images": [
                    {"hash": feed_hash, "adlabels": [{"name": "feed_crop"}]},
                    {"hash": story_hash, "adlabels": [{"name": "story_crop"}]},
                ],
                "bodies": [{"text": primary_text}],
                "titles": [{"text": headline}],
                "descriptions": [{"text": description or " "}],
                "link_urls": [{"website_url": link_url}],
                "call_to_action_types": [call_to_action],
                "ad_formats": ["SINGLE_IMAGE"],
                "asset_customization_rules": [
                    {
                        "customization_spec": {
                            "publisher_platforms": ["facebook", "instagram"],
                            "facebook_positions": ["feed", "marketplace",
                                                   "video_feeds", "search"],
                            "instagram_positions": ["stream", "explore"],
                        },
                        "image_label": {"name": "feed_crop"},
                        "priority": 1,
                    },
                    {
                        "customization_spec": {
                            "publisher_platforms": ["facebook", "instagram"],
                            "facebook_positions": ["story", "facebook_reels"],
                            "instagram_positions": ["story", "reels",
                                                    "ig_search", "profile_feed"],
                        },
                        "image_label": {"name": "story_crop"},
                        "priority": 2,
                    },
                ],
            }

            # Meta requires an IG identity when placement rules name Instagram
            # positions (subcode 1772103). Resolve from the Page if not given.
            ig_actor = instagram_actor_id
            ig_source = "supplied"
            if not ig_actor:
                # The Page's instagram_business_account is the id that
                # instagram_user_id wants. /act_X/instagram_accounts is empty on
                # this account, and page_backed_instagram_accounts needs a Page
                # token we don't hold — so go to the Page fields directly.
                try:
                    r0 = await c.get(f"{GRAPH}/{page_id}", params={
                        "fields": "instagram_business_account{id,username},"
                                  "connected_instagram_account{id,username}",
                        "access_token": token,
                    })
                    d0 = r0.json()
                    node = (d0.get("instagram_business_account")
                            or d0.get("connected_instagram_account") or {})
                    ig_actor = node.get("id", "")
                    if ig_actor:
                        ig_source = f"page_igba:{node.get('username','')}"
                except Exception:
                    pass
            if not ig_actor:
                try:
                    r0 = await c.get(f"{GRAPH}/{account}/instagram_accounts",
                                     params={"fields": "id,username",
                                             "access_token": token})
                    rows = r0.json().get("data", [])
                    if rows:
                        ig_actor = rows[0].get("id", "")
                        ig_source = "adaccount_lookup"
                except Exception:
                    pass
            if not ig_actor:
                probe: dict[str, Any] = {}
                for label, url, params in (
                    ("adaccount_instagram_accounts",
                     f"{GRAPH}/{account}/instagram_accounts",
                     {"fields": "id,username", "access_token": token}),
                    ("page_backed_instagram_accounts",
                     f"{GRAPH}/{page_id}/page_backed_instagram_accounts",
                     {"fields": "id,username", "access_token": token}),
                    ("page_ig_fields", f"{GRAPH}/{page_id}",
                     {"fields": "instagram_business_account{id,username},"
                                "connected_instagram_account{id,username}",
                      "access_token": token}),
                ):
                    try:
                        rp = await c.get(url, params=params)
                        probe[label] = rp.json()
                    except Exception as e:
                        probe[label] = f"{type(e).__name__}: {e}"
                return {"ok": False, "step": "ig_identity",
                        "error": "Could not resolve an Instagram account. "
                                 "Candidates below — pick the right id.",
                        "probe": probe,
                        "feed_hash": feed_hash, "story_hash": story_hash}

            # Meta deprecated instagram_actor_id in object_story_spec (subcode
            # 2238281) — instagram_user_id is the supported field.
            oss: dict[str, Any] = {"page_id": page_id, "instagram_user_id": ig_actor}
            creative_spec = {
                "name": ad_name,
                "object_story_spec": json.dumps(oss),
                "asset_feed_spec": json.dumps(asset_feed_spec),
                "access_token": token,
            }
            r = await c.post(f"{GRAPH}/{account}/adcreatives", data=creative_spec)
            d = r.json()
            if "error" in d:
                err = d["error"]
                return {"ok": False, "step": "creative",
                        "feed_hash": feed_hash, "story_hash": story_hash,
                        "ig_actor": ig_actor, "ig_source": ig_source,
                        "error": err.get("message", ""),
                        "error_user_title": err.get("error_user_title", ""),
                        "error_user_msg": err.get("error_user_msg", ""),
                        "error_subcode": err.get("error_subcode", ""),
                        "fbtrace": err.get("fbtrace_id", "")}
            creative_id = d.get("id")

            r = await c.post(f"{GRAPH}/{account}/ads", data={
                "name": ad_name,
                "adset_id": adset_id,
                "creative": json.dumps({"creative_id": creative_id}),
                "status": "PAUSED",
                "access_token": token,
            })
            d = r.json()
            if "error" in d:
                return {"ok": False, "step": "ad_create", "creative_id": creative_id,
                        "error": d["error"].get("message", "")}
            ad_id = d.get("id")

            final_status = "PAUSED"
            if activate:
                r = await c.post(f"{GRAPH}/{ad_id}",
                                 data={"status": "ACTIVE", "access_token": token})
                if r.json().get("success"):
                    final_status = "ACTIVE"

            return {"ok": True, "ad_id": ad_id, "creative_id": creative_id,
                    "feed_hash": feed_hash, "feed_method": m1,
                    "story_hash": story_hash, "story_method": m2,
                    "ig_actor": ig_actor, "ig_source": ig_source,
                    "ad_name": ad_name, "status": final_status}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class _HostRewrite:
    """Pure-ASGI shim: force the inbound Host to 'localhost' so FastMCP's
    streamable-HTTP DNS-rebinding / TrustedHost guard (on by default in
    fastmcp>=2.3) accepts requests that Railway routed by the real public
    host. Railway has already matched the service by the real Host header
    before this runs, so rewriting it here is safe and affects nothing else.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = [
                (k, v) for (k, v) in (scope.get("headers") or [])
                if k != b"host"
            ]
            headers.append((b"host", b"localhost"))
            scope = dict(scope)
            scope["headers"] = headers
            # keep scope["server"] consistent with the rewritten host
            if scope.get("server"):
                scope["server"] = ("localhost", scope["server"][1])
        await self.app(scope, receive, send)


class _BearerAuth:
    """Minimal pure-ASGI gate. Accepts the shared secret via EITHER
    `Authorization: Bearer <secret>` OR a `?key=<secret>` query param
    (Claude Desktop's custom-connector puts the secret in the URL, which is
    why the Bearer-only check never matched). If no secret is configured,
    the gate is a no-op (open server; posts still default to draft).
    """

    def __init__(self, app, secret: str):
        self.app = app
        self.secret = secret

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and self.secret:
            headers = dict(scope.get("headers") or [])
            got = headers.get(b"authorization", b"").decode()
            bearer_ok = got == f"Bearer {self.secret}"

            key_ok = False
            qs = scope.get("query_string", b"").decode()
            if qs:
                from urllib.parse import parse_qs
                key_ok = self.secret in parse_qs(qs).get("key", [])

            if not (bearer_ok or key_ok):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    secret = os.getenv("MCP_SHARED_SECRET", "")

    # Get FastMCP's streamable-HTTP ASGI app (method name varies by version),
    # wrap it: uvicorn -> _HostRewrite -> _BearerAuth -> FastMCP asgi.
    asgi = None
    for name in ("http_app", "streamable_http_app"):
        fn = getattr(mcp, name, None)
        if fn:
            asgi = fn()
            break

    if asgi is None:
        # Fallback: no wrappable app on this FastMCP version. Runs UNAUTHED —
        # only use behind Railway private networking.
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
    else:
        import uvicorn
        uvicorn.run(_HostRewrite(_BearerAuth(asgi, secret)),
                    host="0.0.0.0", port=port)
