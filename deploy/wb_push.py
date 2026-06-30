#!/usr/bin/env python3
# Me + Lia — Broadcast 2 & 3 finalize (Draft -> corrected template -> scheduled)
# Runs on Polecat's Mac. Uses KLAVIYO_KEY from the shell env (never pass it inline).
# DRY_RUN=True prints the plan only. Set DRY_RUN=False to execute.
import os, json, urllib.request, urllib.error

DRY_RUN = True   # <-- set to False to actually push

KEY = os.environ.get("KLAVIYO_KEY") or os.environ.get("KLAVIYO_API_KEY")
assert KEY, "Set KLAVIYO_KEY in your shell env first."
BASE = "https://a.klaviyo.com/api"
HDR = {"Authorization": f"Klaviyo-API-Key {KEY}", "revision": "2025-01-15",
       "Content-Type": "application/json", "Accept": "application/json"}

def call(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(BASE+path, data=data, method=method, headers=HDR)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, (json.load(r) if r.read else {})
    except urllib.error.HTTPError as e:
        try: return e.code, json.load(e)
        except Exception: return e.code, {"raw": e.read().decode()[:300]}

def fetch_html(url):
    with urllib.request.urlopen(url) as r:
        return r.read().decode()

JOBS = [
  {"label":"Broadcast 2 — Martina", "campaign":"01KVAK8XAKF6HZNYD1PH1A9D6A",
   "message":"01KVAK8XAWQN3FE5XNT3C1TRTD",
   "html":"https://raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/deploy/b2_final.html",
   "tname":"ML Broadcast 2 — Martina (final)", "when":"2026-07-08T10:45:00+00:00"},
  {"label":"Broadcast 3 — Celeste White", "campaign":"01KVAKDY9HHSESK9N92GTQGJYN",
   "message":"01KVAKDY9VCBGP6SY4GY6CSTA1",
   "html":"https://raw.githubusercontent.com/Alaska-Artemisia/Gemini-relay/main/deploy/b3_final.html",
   "tname":"ML Broadcast 3 — Celeste (final)", "when":"2026-07-15T10:45:00+00:00"},
]

for j in JOBS:
    print(f"\n=== {j['label']}  (send {j['when']}) ===")
    html = fetch_html(j["html"]); print(f"  fetched template html: {len(html)} bytes")
    if DRY_RUN:
        print("  DRY_RUN: would create template, assign to message, set send time, schedule.")
        continue
    # 1) create template
    st,r = call("POST","/templates/", {"data":{"type":"template",
        "attributes":{"name":j["tname"],"editor_type":"CODE","html":html}}})
    tid = r.get("data",{}).get("id"); print(f"  [1] create template -> {st} id={tid}")
    if not tid: print("  STOP:",r); continue
    # 2) assign template to the campaign message
    st,r = call("POST","/campaign-message-assign-template/", {"data":{"type":"campaign-message",
        "id":j["message"],"relationships":{"template":{"data":{"type":"template","id":tid}}}}})
    print(f"  [2] assign template -> {st}")
    # 3) set static send time
    st,r = call("PATCH",f"/campaigns/{j['campaign']}/", {"data":{"type":"campaign","id":j["campaign"],
        "attributes":{"send_strategy":{"method":"static","datetime":j["when"],"options":{"is_local":False}}}}})
    print(f"  [3] set send time -> {st}")
    # 4) schedule (create send job)
    st,r = call("POST","/campaign-send-jobs/", {"data":{"type":"campaign-send-job","id":j["campaign"]}})
    print(f"  [4] schedule send job -> {st}")
    # 5) verify
    st,r = call("GET",f"/campaigns/{j['campaign']}/?fields[campaign]=status,send_time")
    a = r.get("data",{}).get("attributes",{})
    print(f"  [5] verify -> status={a.get('status')} send_time={a.get('send_time')}")

print("\nDone." if not DRY_RUN else "\nDRY_RUN complete — set DRY_RUN=False to push.")
