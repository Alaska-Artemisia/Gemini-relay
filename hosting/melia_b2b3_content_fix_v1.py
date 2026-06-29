#!/usr/bin/env python3
# Me + Lia - finish Broadcasts 2 (Martina) & 3 (Celeste) so they're send-ready.
#   * swap placeholder hero image  -> real Shopify product image
#   * swap "$[price]" placeholder   -> real price ($148 Martina / $118 Celeste)
#   * fix Celeste "Shop Now" link   -> /products/celeste-lounge-set-navy (was a dead /celeste-lounge-set)
#   * set inbox From-Name           -> "Me + Lia" on both (body signature "- Melina" untouched)
# Reads each live template, edits in place, writes back. Idempotent. Leaves both as DRAFTS.
import os, json, urllib.request, urllib.error
KEY = os.environ.get("KLAVIYO_KEY")
if not KEY:
    raise SystemExit("KLAVIYO_KEY not set in this shell. Open the terminal where `mk` works.")
REV, BASE = "2025-01-15", "https://a.klaviyo.com/api"

def kapi(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Klaviyo-API-Key " + KEY)
    req.add_header("revision", REV); req.add_header("accept", "application/json")
    if data is not None: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode() or "{}")
        except Exception: return e.code, {}

MARTINA_IMG = "https://cdn.shopify.com/s/files/1/0746/9708/1059/files/martina-red-product-page-3x2.jpg?v=1781829413"
CELESTE_IMG = "https://cdn.shopify.com/s/files/1/0746/9708/1059/files/celeste_norobe_v1-_1.jpg?v=1782444289"

# template_id -> list of (find, replace)
TEMPLATE_EDITS = {
    "UNmsBi": [  # Broadcast 2 - Martina Cherry ($148)
        ("https://meandlia.com/cdn/REPLACE-with-martina-cherry.jpg", MARTINA_IMG),
        ("$[price]", "$148"),
    ],
    "VfDuuf": [  # Broadcast 3 - Celeste Lounge Set Navy ($118)
        ("https://meandlia.com/cdn/REPLACE-with-celeste-lounge-set.jpg", CELESTE_IMG),
        ("$[price]", "$118"),
        ('https://meandlia.com/products/celeste-lounge-set"',
         'https://meandlia.com/products/celeste-lounge-set-navy"'),
    ],
}

# campaign-message id -> full content block (from_label flipped to "Me + Lia")
MSG_CONTENT = {
    "01KVAK8XAWQN3FE5XNT3C1TRTD": {  # B2
        "label": "Broadcast 2 \u00b7 Small Prints (Martina)",
        "content": {"subject": "Why we keep the prints small",
                    "preview_text": "The opposite of a dress that wears you.",
                    "from_email": "hello@meandlia.com", "from_label": "Me + Lia"},
    },
    "01KVAKDY9VCBGP6SY4GY6CSTA1": {  # B3
        "label": "Broadcast 3 \u00b7 Celeste at Home",
        "content": {"subject": "Saturday morning. Coffee. The Celeste set.",
                    "preview_text": "For the softest part of the week.",
                    "from_email": "hello@meandlia.com", "from_label": "Me + Lia"},
    },
}

print("=== templates: hero image / price / link ===")
for tid, edits in TEMPLATE_EDITS.items():
    st, j = kapi("GET", f"/templates/{tid}/?fields%5Btemplate%5D=html")
    if st != 200:
        print(f"  {tid}: GET failed (HTTP {st}) - skipped"); continue
    html = j["data"]["attributes"]["html"]; orig = html; applied = []
    for find, repl in edits:
        n = html.count(find)
        if n: html = html.replace(find, repl); applied.append(f"{find[:38]}... x{n}")
    if html == orig:
        print(f"  {tid}: already fixed (no placeholders found)"); continue
    st, j = kapi("PATCH", f"/templates/{tid}/",
                 {"data": {"type": "template", "id": tid, "attributes": {"html": html}}})
    print(f"  {tid}: {'updated' if st in (200,201) else f'PATCH FAILED HTTP {st}'}  [{'; '.join(applied)}]")
    if st not in (200, 201): print("     ", json.dumps(j)[:300])

print("\n=== from-name -> Me + Lia ===")
for mid, m in MSG_CONTENT.items():
    body = {"data": {"type": "campaign-message", "id": mid, "attributes": {
        "definition": {"channel": "email", "label": m["label"], "content": m["content"]}}}}
    st, j = kapi("PATCH", f"/campaign-messages/{mid}/", body)
    if st in (200, 201):
        fl = j.get("data", {}).get("attributes", {}).get("definition", {}).get("content", {}).get("from_label")
        print(f"  {mid}: from_label now '{fl}'")
    else:
        print(f"  {mid}: PATCH FAILED HTTP {st}: {json.dumps(j)[:300]}")

print("\nDone. B2 & B3 are send-ready content-wise, still DRAFTS. Preview each in Klaviyo before sending.")
