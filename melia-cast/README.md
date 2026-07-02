# MELIA-CAST — hardened social-publishing MCP

A remote MCP server that publishes to Instagram + Facebook via your Metricool
account, built to kill the failure modes of the stock local bridge:

- **No 4-minute hangs** — every call is timeout-bounded (fail fast at 20s).
- **Safe retries** — idempotency keys mean firing twice = one post, never two.
- **Confirmation built in** — `schedule_post` submits then polls the calendar
  and returns the real post id; no reliance on a flaky separate read.
- **Media checked up front** — HTTP 200 + image/video content-type required;
  `github.io` URLs rejected outright.
- **Draft-safe** — nothing auto-publishes unless you pass `draft=false`.
- **Batch** — `schedule_batch` paces N posts in one call.
- **Runs remote** — it physically cannot wedge Claude Desktop.

## Tools
`ping` · `list_scheduled` · `schedule_post` · `reconcile` · `schedule_batch`

## Deploy on Railway (same place your Pinterest MCP lives)

1. **Rotate the Metricool token first** (it was exposed in chat). Metricool →
   settings → regenerate API token.
2. Put these files in a GitHub repo (a new repo, or a `melia-cast/` folder in
   the Gemini-relay repo).
3. Railway → New Project → Deploy from GitHub repo → pick it.
4. Set **Variables** (from `.env.example`):
   `METRICOOL_USER_TOKEN` (the NEW one), `METRICOOL_USER_ID=3850997`,
   `METRICOOL_BLOG_ID=4945902`, `MC_TIMEZONE=Australia/Melbourne`,
   `MCP_SHARED_SECRET=<long random string>`.
5. Railway builds from `requirements.txt` and runs the `Procfile` (`python
   server.py`). Grab the public URL it gives you, e.g.
   `https://melia-cast-production.up.railway.app`.

## Connect in Claude Desktop

Settings → Connectors → Add custom connector:
- **URL:** `https://<your-railway-domain>/mcp`  (streamable-HTTP)
- **Auth header:** `Authorization: Bearer <your MCP_SHARED_SECRET>`

Then remove — or leave read-only — the old local `mcp-metricool` entry in
`claude_desktop_config.json` so there's no confusion about which is which.

## First run
1. `ping` → expect "ok — Metricool reachable, auth valid".
2. `schedule_post` one test as a draft → expect `{status: "confirmed", id: ...}`.
3. Check it in the Metricool web calendar. Ship the rest with `schedule_batch`.
