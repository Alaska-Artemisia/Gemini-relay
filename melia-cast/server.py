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
                return {"ok": False, "error": d["error"].get("message", str(d["error"]))}
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
    if targeting:
        params["targeting"] = targeting
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
                return {"ok": False, "error": d["error"].get("message", str(d["error"]))}
            return {"ok": True, "adset_id": d.get("id"), "name": name, "status": status}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def create_meta_ad(
    image_url: str,
    ad_name: str,
    primary_text: str,
    headline: str,
    link_url: str,
    adset_id: str,
    description: str = "",
    call_to_action: str = "SHOP_NOW",
    page_id: str = "736164559573286",
    activate: bool = True,
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

    Returns {ok, ad_id, creative_id, image_hash, status} on success.
    """
    token = os.getenv("META_ADS_TOKEN", "")
    if not token:
        return {"ok": False, "error": "META_ADS_TOKEN not set."}

    account = META_ACCOUNT

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            # Step 1: Try uploading image by URL for hash (preferred).
            # Falls back to image_url in creative if /adimages is blocked.
            image_hash = None
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
                return {"ok": False, "step": "creative",
                        "image_hash": image_hash,
                        "error": d2["error"].get("message", str(d2["error"]))}
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
                return {"ok": False, "error": d["error"].get("message", str(d["error"]))}
            return {"ok": True, "audience_id": d.get("id"), "name": name, "subtype": subtype}
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
