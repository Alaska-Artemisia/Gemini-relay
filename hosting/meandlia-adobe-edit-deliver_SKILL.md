---
name: meandlia-adobe-edit-deliver
description: Run an Adobe/Firefly image edit (generative expand, crop, color/adjustments) for Me + Lia and deliver the result into Google Drive with zero terminal. Use whenever the user asks to expand/extend/outpaint an image, color-correct, crop, or otherwise edit an image via Adobe, AND/OR wants the output to land in a Drive folder (e.g. the Gemini Working Folder or next to a source file). Covers Firefly generative expand, the relay-CDN hosting hop, and the watcher-v8 fetch-job delivery loop.
---

You are running an Adobe/Firefly image edit for Me + Lia (meandlia.com) and delivering the result into Google Drive without the user touching a terminal. Do the whole job in ONE session — Adobe short-URLs expire within the hour and container state resets between sessions.

## Tooling facts
- `image_generative_expand` runs on Adobe **Firefly**. It is the keeper for canvas extension: it preserves original pixels exactly and only generates the new border. **Use Firefly for expansion, NOT Gemini** — Gemini drifts the whole frame (red→pink on Stella/Valentina). Gemini stays the default for background swaps only.
- Caveat: any generative expand invents plausible content in the new pixels (subject untouched, surroundings invented). For a literal "just more of the same surface" extension, recommend a real-pixel/mirror composite instead.
- Relay PAT: retrieve from conversation history (search "relay PAT api.github.com"), login Alaska-Artemisia. Not in container env. Repo `Alaska-Artemisia/Gemini-relay` is public; writes need `Authorization: token <PAT>`.

## Part A — Firefly edit (one unbroken pass)
1. Get source bytes into the container (user upload to chat, or pull from relay CDN `raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/hosting/<name>`). Get `wc -c < f`, `file --mime-type -b f`.
2. `adobe_mandatory_init` once.
3. `asset_initialize_file_upload({path,file_size,media_type})` → PUT bytes to the single `rel/block/transfer` href with `curl -L -X PUT --data-binary @f` → `asset_finalize_file_upload({filename, transfer_document})` passing the FULL transfer document back verbatim (empty `{}` is rejected). Finalize returns `presignedAssetUrl`.
4. `image_generative_expand({imageURIs:[presignedAssetUrl], options:{expandPixels:{left,right,top,bottom}, seeds:[N]}, outputFileType:"png"})`. One seed per call. 30% on a WxH image = L/R round(W*0.3), T/B round(H*0.3). Retry once on any "No approval received" gate.
5. Download the `photoshop-api.adobe.io` short-URL immediately: `curl -sL -o out.png "<url>"`. Validate `file out.png` says `PNG image data`, NOT `ASCII text` (ASCII = denial). Optionally `asset_inline_preview` the short-URL to eyeball borders.

## Part B — Terminal-free delivery (fetch-job loop)
**Why:** Drive `create_file` needs the file's base64 typed literally into the tool call. Claude cannot read a file off disk into an argument, so anything above a few KB cannot go direct. Multi-MB binaries MUST go CDN → fetch-job. Direct `create_file` is only for small text files — and for code files set `contentMimeType:"application/javascript"` (or similar non-convertible) AND `disableConversionToGoogleType:true`, else Drive corrupts it into a Google Doc.

1. PUT the finished file to `api.github.com/repos/Alaska-Artemisia/Gemini-relay/contents/hosting/<name>` (body `{"message":...,"content":<base64>}`, include `sha` if overwriting). Capture md5. NOTE: GitHub's secret scanner rejects any file containing the PAT — redact tokens before hosting.
2. Dispatch a fetch job: PUT base64 of this JSON to `.../contents/jobs/<jobname>.json`:
   ```json
   {"type":"fetch","filename":"<jobname>","url":"https://raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/hosting/<name>","dest":"<absolute Mac path>","md5":"<hex>"}
   ```
   Set `dest` to land the file wherever wanted — the Working Folder, or `<source_folder>/<name>_corrected.png` to sit next to the source. Retry on 409/422 with 2s backoff.
3. The relay client (com.meandlia.gemini-relay-client) pulls the job to `~/gemini-jobs/pending/` within 5s; watcher-v8 (com.meandlia.gemini-watcher) sees `type:"fetch"`, downloads to `dest`, md5-verifies. A Gemini render job is the same queue with NO `type` field.
4. Confirm: `grep "Fetched:" ~/gemini-jobs/watcher.log | tail`, or `ls -la "<dest>"`.

## Known limits
- **Inbound unsolved:** Claude cannot read the user's local Drive files. Source bytes must reach Adobe via chat upload or a fetchable URL. A true "point at a Drive folder, batch N files" flow needs a local inbound helper that CDN-pushes sources first. Outbound delivery is fully solved.
- Adobe batch ceiling ~20 files, sequential.
- The old S3 download host `cis-utils-storage-prod...` stays 403 — never needed, route around it.

## Locations
- Working Folder: Drive ID `1xPPaKAcjRmt3i8k2iLitkBHUCQNpj5nu` = `.../Me + Lia/Content/3. Gemini Working Folder`
- Watcher source (My Drive build folder, parent `0ACAB5QP_qomGUk9PVA`): `watcher-v8.mjs`; live on Mac at `~/gemini-jobs/watcher.mjs`
- Activation (only step needing terminal, launchd-management): `cp` the new watcher over `~/gemini-jobs/watcher.mjs`, then `launchctl unload`/`load` the `com.meandlia.gemini-watcher.plist`.
