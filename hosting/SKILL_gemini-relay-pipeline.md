---
name: gemini-relay-pipeline
description: How to run Gemini image jobs (background swaps, renders, fetches, and two-image fusion) through Me + Lia's GitHub relay + Mac watcher. Use whenever dispatching a Gemini render/fetch/fusion job, or when jobs are "dispatched but nothing lands." Encodes the two-token architecture, every failure mode, the token-wipe trap, and the correct diagnostic order.
---

# Gemini Relay Pipeline

## Architecture (two halves, same token in two homes)
1. Dispatch half (writes jobs) — the melia-cast MCP server on Railway. Tool: dispatch_relay_job(path, content, message). Uses RELAY_GITHUB_PAT in Railway -> melia-cast -> Variables. Server-side; no token in chat. Almost always works. path must start with jobs/, browser-jobs/, fetch/, or hosting/.
2. Consume half (runs jobs) — two node procs on the Mac in /Users/Foongbear/gemini-jobs/: github-relay-client.mjs (polls repo jobs/, pulls to local pending/, dequeues; authenticates with GITHUB_TOKEN env var) and watcher.mjs ("Gemini Watcher v8", runs gemini + fetch job types, writes output to the Working Folder). The watcher token is only in memory unless persisted to ~/.zshrc.
Consequence: rotate the PAT and Railway updates but the running Mac watcher keeps the dead one — it can still READ the public repo (poll) but cannot dequeue/commit, so jobs pile up.

## "fusion" is a gemini job, not a separate app
Identity lock = a two-image gemini-2.5-flash-image render: images: [scene, olivia_face_v2.png], prompt "COMPLETELY REPLACE the head/face in IMAGE 1 with IMAGE 2." Dispatch to jobs/. See melia-visual-identity skill.

## Job schemas (proven)
Fetch: { "type":"fetch", "url":"<https url>", "dest":"<absolute Mac path>" }
Render/fusion: { "filename":"name", "model":"gemini-2.5-flash-image", "outputDir":"<Working Folder>", "images":["<Mac path>",...], "prompt":"...", "aspectRatio":"2:3" }
images must be local Mac paths (not URLs, not container paths). Watcher runs fetches then renders in queue order.

## Presigned URLs expire (~6h)
Adobe/Lightroom presigned URLs (at.adobe.com -> S3) live ~6h (X-Amz-Expires=21600). A fetch job that sits while the watcher is down dies -> HTTP 403 AccessDenied / Request has expired. Re-mint (asset_get_presigned_urls) immediately before firing fetches.

## Working paths
Working Folder (Mac): /Users/Foongbear/Library/CloudStorage/GoogleDrive-da@heyleyholdings.com/Shared drives/Me + Lia/Content/3. Gemini Working Folder
Working Folder (Drive ID): 1xPPaKAcjRmt3i8k2iLitkBHUCQNpj5nu
Repo: Alaska-Artemisia/Gemini-relay. hosting/<f> is fetchable at raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/hosting/<f> (raw CDN lags ~30-90s after a PUT — verify a 200 first).

## Getting a container file onto Drive (create_file can fail)
Google Drive create_file may error opaquely (connector scope). Reliable route: dispatch the content to hosting/<f> (permanent repo save + raw URL), then a fetch job to pull that raw URL into the Working Folder (Drive). Verify the raw URL is 200 before firing the fetch.

## Diagnostic order when "dispatched but nothing lands"
1. Did dispatch write? dispatch_relay_job returns ok:true + commit sha. "No approval received" -> connector permission (Allow always).
2. Is the watcher consuming? Check the terminal / recent commits for "Job processed:". Repo queue not draining = client can't write = token bad for writes.
3. Is it running? ps aux | grep -iE "watcher|relay" | grep -v grep -> two node procs = up.
4. Read the log / foreground output. Error names the fault:
   - ENOTFOUND / ETIMEDOUT -> Mac network/DNS/VPN.
   - 401 / Bad credentials -> token stale. The token is persisted in ~/.zshrc from the last fix, so RELOAD it first; do NOT reflexively go to Railway.
   - Invalid character in header content ["Authorization"] -> GITHUB_TOKEN is NOT a real token (placeholder / has spaces / < / arrows). NOT a network error. Caused by copying export GITHUB_TOKEN="<paste ...>" verbatim. NEVER write that literal placeholder in a runnable command block.
   - 403 AccessDenied / Request has expired -> dead presigned URL in a fetch job. Ignore/re-mint.
   - startup lines then silence -> clean startup does NOT prove a good token; auth only fails at dequeue-time. Public-repo reads (poll) succeed on any/no token; only writes expose a bad one.

## Relaunch — RELOAD FIRST, do NOT default to re-pasting from Railway
The token is persisted in ~/.zshrc after the first fix. Normal recovery:
pkill -f github-relay-client.mjs; pkill -f watcher.mjs
cd /Users/Foongbear/gemini-jobs
unset GITHUB_TOKEN; source ~/.zshrc        (or open a fresh Terminal window)
curl -s -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user | grep '"login"'   (must print Alaska-Artemisia)
node github-relay-client.mjs & node watcher.mjs
Sanity: grep -n GITHUB_TOKEN ~/.zshrc — one good line; delete any placeholder line (later lines win on source).

## DANGER — the token-wipe trap (this erased the token on 2026-07-04)
NEVER run: sed -i '' '/GITHUB_TOKEN/d' ~/.zshrc  followed by  echo "...$GITHUB_TOKEN..." >> ~/.zshrc  unless a REAL token is already exported AND curl-verified in that exact shell. If the shell holds a placeholder/dummy, sed deletes the only persisted good copy and echo writes garbage over it — the live token then exists ONLY in Railway. Verify BEFORE persist; grep AFTER.

## Re-paste procedure (only when ~/.zshrc has no valid token, or it was rotated)
1. Railway -> melia-cast -> Variables -> RELAY_GITHUB_PAT -> reveal, copy.
2. In Terminal TYPE  export GITHUB_TOKEN='  then PASTE the value  then TYPE  '  and Enter (build it yourself; nothing to paste over).
3. VERIFY: curl -s -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user | grep '"login"'  -> Alaska-Artemisia.
4. ONLY then persist: sed -i '' '/GITHUB_TOKEN/d' ~/.zshrc ; echo "export GITHUB_TOKEN='$GITHUB_TOKEN'" >> ~/.zshrc ; grep GITHUB_TOKEN ~/.zshrc (must show one real ghp_/github_pat_ line).
5. Relaunch: pkill both; cd /Users/Foongbear/gemini-jobs; node github-relay-client.mjs & node watcher.mjs.
On future rotations update BOTH homes: Railway variable AND the ~/.zshrc line.

## Fallback that never needs the token
The gemini:generate_image MCP renders directly on the Mac (worked repeatedly 2026-07-05). Same two-image fusion args (scene + olivia_face_v2.png), but aspectRatio must be one of 1:1/16:9/9:16/3:2/2:3/4:3/3:4/21:9 — 4:5 is rejected; use 3:4. Use this to unblock a render while the relay token is being fixed.

## Pulling results back
Rendered PNG lands in the Working Folder -> syncs to Drive. Pull via Drive connector (search title contains '<filename>', Working Folder ID 1xPPaKAcjRmt3i8k2iLitkBHUCQNpj5nu). Allow sync lag; Drive is source of truth (not cached like raw CDN).
