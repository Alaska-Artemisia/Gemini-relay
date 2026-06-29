#!/usr/bin/env python3
# Me + Lia - geo backfill. Daily: any profile still missing a country -> Dallas, Texas, US.
# Dallas gives Klaviyo a Central-time location so recipient-local sends fire on US time.
# Surgical (location only). Idempotent. Key read from your shell profile at runtime.
import json, subprocess, urllib.request, urllib.error, urllib.parse, datetime

def get_key():
    return subprocess.run(
        ['/bin/zsh','-c',
         'source ~/.zshrc 2>/dev/null; source ~/.zprofile 2>/dev/null; '
         'source ~/.bash_profile 2>/dev/null; printf %s "$KLAVIYO_KEY"'],
        capture_output=True, text=True).stdout.strip()

KEY = get_key()
REV = "2025-01-15"
BASE = "https://a.klaviyo.com/api"
LOC = {"city": "Dallas", "region": "Texas", "country": "United States"}
now = datetime.datetime.now(datetime.timezone.utc)
def ts(): return now.strftime("%Y-%m-%dT%H:%M:%SZ")
if not KEY:
    print(f"{ts()} ERROR: KLAVIYO_KEY not found in shell profile"); raise SystemExit(1)

LOOKBACK_DAYS = 7      # re-scan a week so a missed daily run never loses anyone
GRACE_MIN     = 60     # let Klaviyo IP-geo resolve first; only stamp empties older than this
since = (now - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
grace = now - datetime.timedelta(minutes=GRACE_MIN)

def gget(url):
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Klaviyo-API-Key " + KEY)
    req.add_header("revision", REV); req.add_header("accept", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")

# NOTE: Klaviyo wants the datetime UNQUOTED in the filter
q = urllib.parse.urlencode({"filter": f"greater-or-equal(created,{since})", "page[size]": "100"})
url = f"{BASE}/profiles/?{q}"
empties, scanned = [], 0
try:
    while url:
        d = gget(url); scanned += len(d.get("data", []))
        for p in d.get("data", []):
            a = p.get("attributes", {}) or {}
            loc = a.get("location") or {}
            country = (loc.get("country") or "").strip()
            email = a.get("email"); created = a.get("created")
            if country or not email:
                continue
            try:
                cdt = datetime.datetime.fromisoformat((created or "").replace("Z", "+00:00"))
            except Exception:
                cdt = now
            if cdt <= grace:
                empties.append(email)
        url = (d.get("links") or {}).get("next")
except urllib.error.HTTPError as e:
    print(f"{ts()} ERROR scanning {e.code}: {e.read().decode()[:300]}"); raise SystemExit(1)

if not empties:
    print(f"{ts()} scanned {scanned} recent profiles, 0 missing country"); raise SystemExit(0)

profiles = [{"type": "profile", "attributes": {"email": e, "location": LOC}} for e in empties]
body = {"data": {"type": "profile-bulk-import-job",
                 "attributes": {"profiles": {"data": profiles}}}}
req = urllib.request.Request(f"{BASE}/profile-bulk-import-jobs/",
                             data=json.dumps(body).encode(), method="POST")
req.add_header("Authorization", "Klaviyo-API-Key " + KEY)
req.add_header("revision", REV); req.add_header("Content-Type", "application/json")
req.add_header("accept", "application/json")
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        jid = json.loads(r.read().decode() or "{}").get("data", {}).get("id", "?")
    print(f"{ts()} scanned {scanned}, stamped {len(empties)} -> Dallas, Texas, US (job {jid})")
except urllib.error.HTTPError as e:
    print(f"{ts()} ERROR import {e.code}: {e.read().decode()[:300]}")
