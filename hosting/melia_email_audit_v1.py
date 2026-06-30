#!/usr/bin/env python3
# Me + Lia - Email Image Audit. Pulls every customer-facing email from Klaviyo (your key),
# renders each with headless Chrome (images load from any host, exactly as a subscriber sees),
# and stitches ONE PDF straight into your Drive. Read-only on Klaviyo. Nothing is sent.
import os, re, sys, glob, json, time, subprocess, tempfile, urllib.request, urllib.error

KEY = os.environ.get("KLAVIYO_KEY")
if not KEY:
    raise SystemExit("KLAVIYO_KEY not set in this shell. Open the terminal where `mk` works.")
REV, BASE = "2025-01-15", "https://a.klaviyo.com/api"

OUT_DIR = "/Users/Foongbear/Library/CloudStorage/GoogleDrive-da@heyleyholdings.com/Shared drives/Me + Lia/Content"
OUT_PDF = os.path.join(OUT_DIR, "MeLia_Email_Image_Audit.pdf")

MARTINA_IMG = "https://cdn.shopify.com/s/files/1/0746/9708/1059/files/martina-red-product-page-3x2.jpg?v=1781829413"
CELESTE_IMG = "https://cdn.shopify.com/s/files/1/0746/9708/1059/files/celeste_norobe_v1-_1.jpg?v=1782444289"

# (template_id, label, type, [(find, replace), ...])  -- fixes show B2/B3 in corrected form
EMAILS = [
    ("UWcN9U", "Welcome 1 - Guide Delivery",            "Flow / Welcome",        []),
    ("RgHLMy", "Welcome 1 - Re-send (non-openers)",     "Flow / Welcome",        []),
    ("XPqgcR", "Welcome 2 - Why We Exist",              "Flow / Welcome",        []),
    ("SYwpWD", "Welcome 3 - Studio",                    "Flow / Welcome",        []),
    ("TwUcdS", "Welcome 4 - Bianca (hero / conversion)","Flow / Welcome",        []),
    ("Ubr8dz", "Welcome 5 - Gentle Nudge",              "Flow / Welcome",        []),
    ("XdxjGr", "Browse Abandon - Email 1",              "Flow / Browse Abandon", []),
    ("QQgMyU", "Browse Abandon - Email 2",              "Flow / Browse Abandon", []),
    ("Xts9SG", "Broadcast 1 - Lisbon / Bianca  [SENT]", "Campaign",              []),
    ("UNmsBi", "Broadcast 2 - Martina  [DRAFT]",        "Campaign",
        [("https://meandlia.com/cdn/REPLACE-with-martina-cherry.jpg", MARTINA_IMG),
         ("$[price]", "$148")]),
    ("VfDuuf", "Broadcast 3 - Celeste Navy  [DRAFT]",   "Campaign",
        [("https://meandlia.com/cdn/REPLACE-with-celeste-lounge-set.jpg", CELESTE_IMG),
         ("$[price]", "$118"),
         ('https://meandlia.com/products/celeste-lounge-set"',
          'https://meandlia.com/products/celeste-lounge-set-navy"')]),
    ("Yir6Qg", "Winback 1 - The Soft Tap  [SCHED Jul 1]",        "Campaign", []),
    ("TdDtxS", "Winback 2 - Reason to Look Again  [SCHED Jul 8]","Campaign", []),
    ("XNDyKU", "Winback 3 - The Permission Close  [SCHED Jul 15]","Campaign", []),
]

def get_html(tid):
    url = f"{BASE}/templates/{tid}/?fields%5Btemplate%5D=html,name"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Klaviyo-API-Key " + KEY)
    req.add_header("revision", REV); req.add_header("accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            a = json.loads(r.read().decode())["data"]["attributes"]
            return a.get("html") or ""
    except urllib.error.HTTPError as e:
        print(f"   !! GET template {tid} failed HTTP {e.code}"); return None

def find_chrome():
    cands = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    cands += sorted(glob.glob(os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium")))
    for c in cands:
        if os.path.exists(c): return c
    return None

def ensure_pypdf():
    try:
        import pypdf; return pypdf
    except ImportError:
        print("   installing pypdf (one-time)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--user", "--quiet", "pypdf"], check=False)
        import importlib, site
        importlib.reload(site)
        import pypdf; return pypdf

def banner(i, label, typ, tid, imgs):
    im4 = "<br>".join(f"&nbsp;&nbsp;{u}" for u in imgs) if imgs else "&nbsp;&nbsp;(no images)"
    return (f'<div style="font-family:Arial,Helvetica,sans-serif;background:#1a0f0f;color:#fff;'
            f'padding:10px 16px;font-size:12px;line-height:1.55;">'
            f'<b>{i}. {label}</b> &nbsp;|&nbsp; {typ} &nbsp;|&nbsp; template {tid}<br>'
            f'<span style="color:#E8B7BE;">images ({len(imgs)}):</span><br>{im4}</div>')

def render_pdf(chrome, html_path, pdf_path):
    cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           "--run-all-compositor-stages-before-draw", "--virtual-time-budget=15000",
           f"--print-to-pdf={pdf_path}", "file://" + html_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if not os.path.exists(pdf_path):  # retry with classic headless flag
        cmd[1] = "--headless"
        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return os.path.exists(pdf_path)

def main():
    chrome = find_chrome()
    if not chrome:
        raise SystemExit("No Chrome/Chromium found. Install Google Chrome, then re-run.")
    print("Chrome:", chrome)
    pypdf = ensure_pypdf()
    tmp = tempfile.mkdtemp(prefix="melia_audit_")
    pdfs, index_rows = [], []

    # cover / index page
    for i, (tid, label, typ, fixes) in enumerate(EMAILS, 1):
        print(f"[{i}/{len(EMAILS)}] {label}")
        html = get_html(tid)
        if html is None:
            index_rows.append((i, label, typ, tid, -1)); continue
        for a, b in fixes:
            html = html.replace(a, b)
        imgs = re.findall(r'<img[^>]+src="([^"]+)"', html)
        index_rows.append((i, label, typ, tid, len(imgs)))
        b = banner(i, label, typ, tid, imgs)
        m = re.search(r'<body[^>]*>', html)
        if m:
            html = html[:m.end()] + b + html[m.end():]
        else:
            html = b + html
        hp = os.path.join(tmp, f"e{i:02d}.html"); open(hp, "w").write(html)
        pp = os.path.join(tmp, f"e{i:02d}.pdf")
        if render_pdf(chrome, hp, pp):
            pdfs.append(pp)
        else:
            print(f"   !! render failed for {label}")

    # build cover
    rows = "".join(
        f'<tr><td style="padding:4px 10px;">{i}</td>'
        f'<td style="padding:4px 10px;">{label}</td>'
        f'<td style="padding:4px 10px;color:#9E4C58;">{typ}</td>'
        f'<td style="padding:4px 10px;text-align:center;">{"ERR" if n<0 else n}</td></tr>'
        for (i, label, typ, tid, n) in index_rows)
    cover = (f'<html><body style="font-family:Arial,Helvetica,sans-serif;padding:40px;">'
             f'<h1 style="color:#9E4C58;font-weight:600;">Me + Lia - Email Image Audit</h1>'
             f'<p style="color:#555;">Generated {time.strftime("%d %b %Y %H:%M")} - '
             f'{len([p for p in pdfs])} of {len(EMAILS)} emails rendered. '
             f'Each page is labelled with its image URLs so you can confirm best shots.</p>'
             f'<table style="border-collapse:collapse;font-size:13px;margin-top:14px;">'
             f'<tr style="background:#1a0f0f;color:#fff;"><th style="padding:6px 10px;">#</th>'
             f'<th style="padding:6px 10px;text-align:left;">Email</th>'
             f'<th style="padding:6px 10px;">Type</th><th style="padding:6px 10px;"># imgs</th></tr>'
             f'{rows}</table></body></html>')
    chp = os.path.join(tmp, "cover.html"); open(chp, "w").write(cover)
    cpp = os.path.join(tmp, "cover.pdf")
    cover_ok = render_pdf(chrome, chp, cpp)

    writer = pypdf.PdfWriter()
    for p in ([cpp] if cover_ok else []) + pdfs:
        for pg in pypdf.PdfReader(p).pages:
            writer.add_page(pg)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_PDF, "wb") as f:
        writer.write(f)
    print("\nSAVED ->", OUT_PDF)
    print(f"{len(pdfs)} emails rendered, {sum(1 for r in index_rows if r[4] < 0)} failed to fetch.")

main()
