#!/usr/bin/env python3
# Me + Lia - two jobs in one run, using your KLAVIYO_KEY (this shell only; never echoed):
#   PART 1  Repoint Broadcast 2 (Martina) and Broadcast 3 (Celeste) off the legacy list
#           onto the engaged core (TkyB2N), excluding owners of that product line.
#           (creates the two owner-exclusion segments first if they don't exist)
#   PART 2  Schedule the three winback drafts (Jul 1 / 8 / 15) so they actually send.
# Idempotent: safe to re-run. Repoint leaves B2/B3 as DRAFTS. Touches nothing else.
import os, json, urllib.request, urllib.error, urllib.parse

KEY = os.environ.get("KLAVIYO_KEY")
if not KEY:
    raise SystemExit("KLAVIYO_KEY not set in this shell. Open the terminal where `mk` works.")
REV  = "2025-01-15"
BASE = "https://a.klaviyo.com/api"

def kapi(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Klaviyo-API-Key " + KEY)
    req.add_header("revision", REV)
    req.add_header("accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode() or "{}"
            return r.status, json.loads(txt)
    except urllib.error.HTTPError as e:
        try:    return e.code, json.loads(e.read().decode() or "{}")
        except Exception: return e.code, {}

# ---- constants (all verified live this session) ----
ENGAGED = "TkyB2N"                                # Broadcast Core - Engaged 90d (US), 131 profiles
METRIC  = "SGQE9A"                                # "Ordered Product" (per-line-item, has Name)
B2 = "01KVAK8XAKF6HZNYD1PH1A9D6A"                 # Broadcast 2 - Martina
B3 = "01KVAKDY9HHSESK9N92GTQGJYN"                 # Broadcast 3 - Celeste
WINBACKS = [
    ("Winback 1 - The Soft Tap",          "01KW954BC2PT2BHC8ZX18WFFP7"),
    ("Winback 2 - Reason to Look Again",  "01KW9553Z577ZH8GTN0SS4BAHP"),
    ("Winback 3 - The Permission Close",  "01KW955N42Y11FT4J9FNHWE59H"),
]

def owner_segment(name, product_word):
    """Find an existing owner segment by name, else create it (placed an order whose
    product Name contains <word>, any time). Returns segment id or None."""
    flt = urllib.parse.quote(f'equals(name,"{name}")')
    st, j = kapi("GET", f"/segments/?filter={flt}")
    if st == 200 and j.get("data"):
        sid = j["data"][0]["id"]
        print(f"  segment exists: {name}  ->  {sid}")
        return sid
    body = {"data": {"type": "segment", "attributes": {
        "name": name,
        "definition": {"condition_groups": [{"conditions": [{
            "type": "profile-metric", "metric_id": METRIC, "measurement": "count",
            "measurement_filter": {"type": "numeric", "operator": "greater-than", "value": 0},
            "timeframe_filter": {"type": "date", "operator": "alltime"},
            "metric_filters": [{"property": "Name",
                "filter": {"type": "string", "operator": "contains", "value": product_word}}],
        }]}]},
    }}}
    st, j = kapi("POST", "/segments/", body)
    if st in (200, 201) and j.get("data"):
        sid = j["data"]["id"]
        print(f"  segment created: {name}  ->  {sid}")
        return sid
    print(f"  !! could not create segment '{name}'  (HTTP {st}): {json.dumps(j)[:400]}")
    return None

def repoint(campaign_id, label, owner_seg):
    excluded = [owner_seg] if owner_seg else []
    body = {"data": {"type": "campaign", "id": campaign_id,
            "attributes": {"audiences": {"included": [ENGAGED], "excluded": excluded}}}}
    st, j = kapi("PATCH", f"/campaigns/{campaign_id}/", body)
    if st in (200, 201):
        aud = j.get("data", {}).get("attributes", {}).get("audiences", {})
        print(f"  {label}: repointed -> included {aud.get('included')}  excluded {aud.get('excluded')}"
              + ("" if owner_seg else "   (NOTE: owner-exclusion skipped - segment missing)"))
    else:
        print(f"  !! {label}: PATCH failed (HTTP {st}): {json.dumps(j)[:400]}")

def schedule(campaign_id, label):
    st, j = kapi("GET", f"/campaigns/{campaign_id}/?fields%5Bcampaign%5D=status,scheduled_at")
    status = j.get("data", {}).get("attributes", {}).get("status") if st == 200 else "?"
    if status != "Draft":
        print(f"  {label}: status already '{status}' - skipping (no double-schedule)")
        return
    body = {"data": {"type": "campaign-send-job", "id": campaign_id}}
    st, j = kapi("POST", "/campaign-send-jobs/", body)
    if st in (200, 201, 202):
        st2, j2 = kapi("GET", f"/campaigns/{campaign_id}/?fields%5Bcampaign%5D=status,scheduled_at")
        a = j2.get("data", {}).get("attributes", {}) if st2 == 200 else {}
        print(f"  {label}: SCHEDULED -> status '{a.get('status')}'  at {a.get('scheduled_at')}")
    else:
        print(f"  !! {label}: schedule failed (HTTP {st}): {json.dumps(j)[:400]}")

print("=== PART 1  repoint Broadcasts 2 & 3 to the engaged core ===")
martina = owner_segment("Owners - Martina (any print)", "Martina")
celeste = owner_segment("Owners - Celeste (any)",       "Celeste")
repoint(B2, "Broadcast 2 (Martina)", martina)
repoint(B3, "Broadcast 3 (Celeste)", celeste)

print("\n=== PART 2  schedule the three winbacks (Jul 1 / 8 / 15) ===")
for label, cid in WINBACKS:
    schedule(cid, label)

print("\nDone. Broadcasts 2 & 3 remain DRAFTS (audience swapped). Winbacks now scheduled.")
