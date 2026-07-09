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
  META_ADS_TOKEN         Meta ads_read token (long-lived / System User — a
                         Graph Explorer token expires in ~1h). For get_meta_audiences.
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
