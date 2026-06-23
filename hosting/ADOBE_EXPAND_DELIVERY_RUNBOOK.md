# Adobe / Firefly Edit + Terminal-Free Delivery — RUNBOOK

_Last proven working: 2026-06-23. Owner: Polecat (Me + Lia)._

This runbook covers two linked capabilities:
1. **Adobe/Firefly image edits** (generative expand, crop, adjustments) run from a Claude session.
2. **Terminal-free delivery** of the result into Google Drive — landing at any path, including next to the source file.

Run the whole thing **in one session**. Adobe short-URLs expire within the hour and container state is wiped between sessions.

---

## PART 1 — FIREFLY GENERATIVE EXPAND (the proven Adobe edit)

`image_generative_expand` runs on Adobe **Firefly** server-side. It is the **keeper tool for canvas extension** because it preserves the original pixels exactly and only generates into the new border region.

**Firefly vs Gemini for expansion:** use **Firefly**, not Gemini. Gemini drifts the whole frame (the red→pink color shift on Stella/Valentina). Firefly is drift-free on the subject. Note this explicitly: Gemini is the default for *background swaps*, but it is NOT the tool for *expansion*.

**Inherent caveat:** any generative expand invents plausible content in the new pixels (e.g. it turned a tabletop into a bench + floor + chair leg on the scrunchie test). The subject is untouched, but the surroundings are invented. For a literal "just more of the same surface, nothing invented" extension, use a real-pixel/mirror composite instead — no model can be reliably told "only extend the texture."

### Step-by-step (one session, no stopping)

1. **Network probe (optional sanity check).** `at.adobe.com` (upload host) and `photoshop-api.adobe.io` (output host) are reachable. The old S3 download host `cis-utils-storage-prod.s3-accelerate.amazonaws.com` stays 403 — **it is never needed**, the pipeline routes around it.

2. **Get the source bytes into the container.** Either the user uploads to chat, or pull from the relay CDN (`raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/hosting/<name>`). Get size + mime: `wc -c < file`, `file --mime-type -b file`.

3. **`adobe_mandatory_init`** — call once before any Adobe tool.

4. **Upload:** `asset_initialize_file_upload({path, file_size, media_type})` → returns a transfer document. PUT the bytes to the single `rel/block/transfer` href (`curl -L -X PUT ... --data-binary @file`). Then `asset_finalize_file_upload({filename, transfer_document})` — **pass the FULL transfer document back verbatim** (every key incl. `_links`, `repo:size`, etc.). An empty `{}` is rejected with `repo:size undefined`. Finalize returns `presignedAssetUrl`.

5. **Expand:** `image_generative_expand({imageURIs:[presignedAssetUrl], options:{expandPixels:{left,right,top,bottom}, seeds:[N]}, outputFileType:"png"})`. One seed = one output; for multiple seeds fire one call per seed. Returns a `photoshop-api.adobe.io` short-URL.
   - **30% expand on a WxH image** = L/R `round(W*0.3)`, T/B `round(H*0.3)`.
   - On any "No approval received" gate: retry once, it clears.

6. **Download + validate immediately** (short-URL expires within the hour): `curl -sL -o out.png "<short-url>"`, then `file out.png` MUST say `PNG image data`, NOT `ASCII text` (137-byte ASCII = a denial in disguise).

7. **Preview** with `asset_inline_preview` on the short-URL to eyeball the generated borders before delivery.

8. **Deliver** — see Part 2.

---

## PART 2 — TERMINAL-FREE DELIVERY (the fetch-job loop)

### Why it exists
The Drive `create_file` tool needs the file's base64 **typed literally into the tool call**. Claude authors tool calls as text and cannot read a file off disk into an argument, so any payload bigger than a few KB cannot be placed there. A 6 MB PNG = ~8M base64 chars = impossible inline. **Direct Drive write works ONLY for small text files (scripts, configs); multi-MB binaries must go CDN → fetch-job.**

### The loop (zero terminal for the user, every time)
1. Claude pushes the finished file to the relay CDN: PUT to `api.github.com/repos/Alaska-Artemisia/Gemini-relay/contents/hosting/<name>` (token below). Capture the `raw.githubusercontent.com` download_url and the file's md5.
2. Claude writes a **fetch job** to `api.github.com/repos/Alaska-Artemisia/Gemini-relay/contents/jobs/<jobname>.json` (base64 of the JSON payload, `{"message":...,"content":...}`).
3. The **relay client** (`com.meandlia.gemini-relay-client`) polls GitHub every 5s, pulls the job to `~/gemini-jobs/pending/`, commits "Job processed:".
4. **watcher-v8** (`com.meandlia.gemini-watcher`) sees `type:"fetch"`, downloads the URL straight to `dest`, md5-verifies, writes a done file.

The file lands at **whatever `dest` Claude specifies** — including the source file's own folder. Set `dest` to `<source_folder>/<name>_corrected.png` to land next to the original.

### Fetch job schema
```json
{
  "type": "fetch",
  "filename": "stella_red_corrected",
  "url": "https://raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/hosting/foo.png",
  "dest": "/Users/Foongbear/Library/CloudStorage/GoogleDrive-da@heyleyholdings.com/Shared drives/Me + Lia/Content/<folder>/foo.png",
  "md5": "ac2e1b39..."
}
```
A Gemini render job is the same queue but with NO `type` field (defaults to "gemini") — render path is unchanged from v7.

### Confirming a fetch landed
- `grep -E "Fetched:" ~/gemini-jobs/watcher.log | tail` → `Fetched: <dest> (md5 ok)`
- or `ls -la "<dest>"`

---

## KEY FACTS / GOTCHAS

- **Relay PAT:** `<RELAY_PAT — retrieve from conversation history, login Alaska-Artemisia>` (login Alaska-Artemisia). Not in container env — retrieved from conversation history. Repo is public; reads are fine unauthenticated but writes need the token. Use `Authorization: token <PAT>`.
- **Fast sequential PUTs** to GitHub cause 409/422 — retry loop with 2s backoff clears it.
- **Drive `create_file` auto-converts `text/plain` to a Google Doc** (corrupts code files). For any code/script write: set `contentMimeType` to a non-convertible type (e.g. `application/javascript`) AND `disableConversionToGoogleType: true`.
- **Inbound limit (unsolved):** Claude cannot read the user's local Drive files. Source bytes must reach Adobe via chat upload or a fetchable URL. A true "point at a Drive folder, batch-process N files" flow needs a local inbound helper that CDN-pushes the sources first. Outbound (delivery) is fully solved; inbound is the next build.
- **Batch ceiling:** Adobe generative/processing batches cap at ~20 files, sequential.

## ACTIVATION (one-time only, already done 2026-06-23)
Swapping the watcher to a new version is the ONLY step that needs terminal — it's launchd service management, unreachable from a Claude session:
```bash
cp "$HOME/Library/CloudStorage/GoogleDrive-da@heyleyholdings.com/My Drive/watcher-vN.mjs" "$HOME/gemini-jobs/watcher.mjs" && \
launchctl unload "$HOME/Library/LaunchAgents/com.meandlia.gemini-watcher.plist" && \
launchctl load "$HOME/Library/LaunchAgents/com.meandlia.gemini-watcher.plist" && \
grep "Watcher v" "$HOME/gemini-jobs/watcher.log" | tail -n 1
```

## FILE LOCATIONS
- Active watcher source (My Drive build folder, parent `0ACAB5QP_qomGUk9PVA`): `watcher-v8.mjs`
- Live watcher on Mac: `~/gemini-jobs/watcher.mjs`
- Working Folder (Drive ID `1xPPaKAcjRmt3i8k2iLitkBHUCQNpj5nu`): `.../Me + Lia/Content/3. Gemini Working Folder`
- Relay repo: `Alaska-Artemisia/Gemini-relay` (`jobs/` queue, `hosting/` CDN)
