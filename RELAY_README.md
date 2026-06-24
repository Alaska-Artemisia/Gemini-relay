# MELIA_RELAY_README — Gemini + Browser-Agent relay (how to use)

Single source of truth for the Me + Lia automation relay. If you are a new Claude
chat and the operator (Polecat) pointed you here: read this top to bottom, then you
can dispatch jobs. Everything is here EXCEPT the secret token (see Auth).

Primary home: this repo (`RELAY_README.md`). A mirror copy may also live in the Drive
"3. Gemini Working Folder".

--------------------------------------------------------------------------------
## What this is
Two relays share ONE public GitHub repo as a job queue + file host. A watcher on
Polecat's Mac polls the repo, executes each job, writes the result to Google Drive,
and deletes the job file to dequeue it.

- Browser-agent relay — headless Playwright: load a URL, run JS you supply, scroll,
  screenshot. For live-site audits / DOM probes.
- Gemini image relay — generates/edits images. ALWAYS use this for image generation;
  NEVER use the gemini:generate_image MCP tool (it reliably times out).

## Components
- GitHub repo (public): Alaska-Artemisia/Gemini-relay — queue + file host. Job files
  are committed, picked up, then deleted.
- Mac watchers (launchd): browser agent label `com.meandlia.browser-agent` (polls
  `browser-jobs/`); Gemini image watcher (polls `jobs/`). A "Job processed:" commit
  means the relay CLIENT is alive; a SEPARATE watcher does the actual render.
- Drive output folder: "3. Gemini Working Folder", ID 1xPPaKAcjRmt3i8k2iLitkBHUCQNpj5nu.
  Every result lands here: browser result JSON, screenshots, generated PNGs.

## Auth (token)
Dispatch hits the GitHub Contents API and needs the relay repo's GitHub access token
(a classic personal access token).
- It is supplied by Polecat — ask him, or conversation_search a recent relay session.
- Use it only as the GitHub API auth header from the code container; never in a URL,
  never echo it, never save it to a shared doc. At runtime set it as an env var (e.g. T)
  and read os.environ['T'].

## Dispatch (GitHub Contents API)
PUT https://api.github.com/repos/Alaska-Artemisia/Gemini-relay/contents/<path>
body: { "message": "...", "content": "<base64 of the job JSON>", "sha": "<only if the path already exists>" }
If the path exists, GET its sha first and include it; on a 409 conflict, re-GET sha and retry.

Paths:
- Browser jobs   -> browser-jobs/<name>.json
- Image jobs     -> jobs/<name>.json
- Image hosting  -> hosting/<name>.png, fetched via
  https://raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/hosting/<name>.png
  (the raw CDN lags ~30-90s after a PUT before it is fetchable — verify a 200 first).

### Proven Python dispatch helper (runs in the code container)
    import json, base64, time, urllib.request, urllib.error, os
    TOKEN = os.environ['T']                 # set the token in env var T first
    REPO  = 'Alaska-Artemisia/Gemini-relay'
    def api(method, path, body=None):
        data = json.dumps(body).encode() if body else None
        hdr = {'Authorization': 'token ' + TOKEN, 'User-Agent': 'melia',
               'Accept': 'application/vnd.github+json'}
        req = urllib.request.Request(f'https://api.github.com/repos/{REPO}/{path}',
                                     data=data, method=method, headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            try: return e.code, json.load(e)
            except Exception: return e.code, {}
    def put(path, raw, msg):
        b64 = base64.b64encode(raw).decode()
        for _ in range(6):                  # warm-up + 409 retries
            body = {'message': msg, 'content': b64}
            st, j = api('GET', f'contents/{path}')
            if st == 200 and isinstance(j, dict) and j.get('sha'):
                body['sha'] = j['sha']
            st, j = api('PUT', f'contents/{path}', body)
            if st in (200, 201): return True
            if st == 409: time.sleep(1.5); continue
            return False
        return False

## Browser job schema
{ "type":"browser", "filename":"<name>", "headful":false, "width":1440, "height":900,
  "deviceScaleFactor":1, "userAgent":"...", "steps":[...] }
(result is written as <name>__result.json). Step types (each echoed under result.steps[i]):
- {"do":"goto","url":"...","waitUntil":"domcontentloaded","timeoutMs":30000}  (domcontentloaded, NOT networkidle)
- {"do":"waitMs","ms":1500}
- {"do":"scroll","passes":3,"pauseMs":400}
- {"do":"eval","script":"() => { ... }"}   (JS function AS A STRING; return value -> result.steps[i].value)
- {"do":"click", ...}        (selector-based)
- {"do":"screenshot", ...}   (image saved to the Drive folder)

### Worked example
    probe = "() => { const h=[...document.querySelectorAll('h1')]; return h.map(x=>({text:(x.textContent||'').trim().slice(0,90), cls:x.className, section:(x.closest('[id^=shopify-section]')||{}).id})); }"
    job = {"type":"browser","filename":"probe_pdp_h1","width":1440,"height":900,"steps":[
      {"do":"goto","url":"https://meandlia.com/products/bianca-cloud-rose","waitUntil":"domcontentloaded","timeoutMs":30000},
      {"do":"waitMs","ms":1800},
      {"do":"eval","script":probe} ]}
    put('browser-jobs/probe_pdp_h1.json', json.dumps(job).encode(), 'probe pdp h1s')

## Lifecycle / polling
1. PUT the job to browser-jobs/ (or jobs/).
2. The watcher runs it, writes the result to Drive, DELETES the job to dequeue.
   Poll until the job filename is gone:
       st, j = api('GET', 'contents/browser-jobs')
       names = [f['name'] for f in j] if isinstance(j, list) else []
       # sleep(7) loop until '<name>.json' not in names
3. Read the result from Drive.

## Reading results (Drive)
- Browser result: <filename>__result.json in the Working Folder.
  Find: search_files  title contains '<filename>__result' and parentId = '1xPPaKAcjRmt3i8k2iLitkBHUCQNpj5nu'
  Read: download_file_content (base64 in 'content'); small files also include a readable contentSnippet.
  Shape: { job, ok, startedAt, finishedAt, data, screenshots, steps:[{do,...,ok,value}], error, finalUrl, console, pageErrors, network:{failed,bad} }
- Images: PNG(s) in the same folder; find by title, then download_file_content.

## Gotchas / known-good practice
- Warm-up: first call after the watcher has been idle can fail — just retry immediately.
  No troubleshooting / restarts / settings advice.
- domcontentloaded, NOT networkidle (networkidle hangs on this site's tracking beacons).
- Console / network noise (Klaviyo CORS, GA/TikTok aborts, shop.app CSP frame-ancestors)
  is headless tracking-beacon noise, NOT a customer-facing problem. Ignore it.
- Image relay: always dispatch via jobs/ PUT; never gemini:generate_image.
- Render stall: "Job processed:" = relay client alive only; if jobs are picked up but no
  PNG lands, the render watcher stalled -> Polecat reloads it on the Mac via launchctl.
- Adobe / Firefly big outputs: delivered to Drive via a fetch-job loop (watcher-v8)
  because a chat can't put multi-MB base64 straight into Drive create_file.
- Exact image-job JSON fields: copy them from a recent jobs/*.json in repo history or via
  conversation_search; don't guess.

## Point a new chat at the relay
Tell it: "Read RELAY_README in the Gemini-relay repo (or MELIA_RELAY_README in my Drive)
and use it to dispatch a relay job." Then paste the GitHub access token when it asks.

_Last updated: 2026-06-24._
