---
name: melia-visual-identity
description: The locked Me + Lia visual identity system. Use whenever grading, generating, or approving any Me + Lia image (ads, PDP, lifestyle, grid). Covers the North Star grade PAIR (warm/cool), how to pick between them, world/model/kit rules, the production pipeline order, and the identity/pose learnings that stop AI drift. Supersedes single-grade MELIA_VISUAL_NORTHSTAR (rev b) and the MELIA_VISUAL_ENGINE grade sections.
status: LOCKED 2026-07-05
---

# Me + Lia Visual Identity — North Star (final)

## 0. TL;DR
- The grade is a matched pair, not one preset: v3 COOL (blues) and v3 WARM (creams). Same look, differing only in temperature and blue handling. Pick by garment palette.
- Anchor = the Mikuta reference set (19-frame folder), tuned cooler and dual because Me + Lia's range is bluer than Mikuta's.
- Grade is always the LAST step. Background/scene (Gemini) -> identity lock (two-image Gemini fusion) -> grade (Lightroom).
- The look: warm-not-golden, matte with lifted-but-not-muddy blacks, blues/greens pulled down, reds and skin protected, fine grain. The garment is the only fully saturated element; the world stays a faded warm-neutral Mediterranean ground.

## 1. The grade pair — how to choose
One question at grade time: is the garment warm-palette or cool-palette?
- Blue / navy / periwinkle / blue florals (Martina Midnight, Bianca Blue Blossom) -> v3 COOL (blues). Warm greys-out and dirties blue; cool keeps it true.
- Cream / blush / red-pink florals / oatmeal (Bianca Cloud Rose, rose prints) -> v3 WARM (creams). Warmth makes cream rich and pinks/reds sing.
- Genuinely neutral / mixed -> default to COOL (safer on whites).
Proven by test: warm kills the blue on Martina/blue florals; cool slightly flattens cream/rose. Neither single grade serves the whole range, hence the pair. They share everything except Temp and the blue/aqua saturation, so the grid still reads as one campaign.

## 2. The numbers (frozen)
Files: MeLia_Mikuta_NorthStar_v3_COOL_blues.xmp, MeLia_Mikuta_NorthStar_v3_WARM_creams.xmp.
Shared: Contrast -8, Highlights -28, Shadows +18, Whites -10, Blacks +5, Texture +5, Clarity -3, Vibrance +4, Saturation -5. Tone curve lifts black point to 16, rolls highlights to 244 (matte). HSL: Green sat -30/lum +5, Yellow sat -8, Red sat 0, Orange (skin guard) hue +4 / sat -6 / lum +8. Color grade: warm shadows (hue ~42, sat 10), warm-cream highlights. Grain 12/20/50. Sharpening 24, masking 40.
COOL differs: Temp +3, Tint +2, Blue sat -7, Aqua -10, Blue lum +3.
WARM differs: Temp +7, Tint +3, Blue sat -18, Aqua -14.
Calibration reference: the Mikuta North Star folder (Drive). Recalibrate against those frames if revised; do NOT anchor to our own older graded frames.

## 3. World, model, kit
- World: Mediterranean only — Old Towns, Harbours, Beaches, Coastal Rocks. Whitewashed Cycladic and warm-stone. No seamless studio as a final frame.
- Models: the two locked identities, Olivia and Melissa. Consistency of face across the grid beats a one-off "prettier" face — a drifted pretty face is a FAIL.
- Kit / styling: stacked silver, a single French market basket, black flat sandals, undone hair, minimal gold.
- Framing/aspect: garment is the hero and only saturated element; quiet ground around it. Build ads at 4:5 (feed), 1:1, 9:16.

## 4. Production pipeline — ORDER IS LOAD-BEARING
1. Scene / background / pose — Gemini relay render job. A single-image regen drifts identity; never trust it to hold a specific face on its own.
2. Identity lock = "fusion" — a TWO-IMAGE Gemini relay job (NOT a separate app). A normal jobs/ render on gemini-2.5-flash-image with images: [scene, locked_face_portrait] and a prompt "COMPLETELY REPLACE the head/face in IMAGE 1 with IMAGE 2." Locked face portraits live in the Working Folder: olivia_face_v2.png and melissa_face_v1.png. Fire with dispatch_relay_job to jobs/<name>.json. There is NO FaceFusion desktop app — "fusion" always meant this two-image Gemini job.
3. Grade — Lightroom, LAST. Apply the correct preset from the pair. Never grade before compositing (the "two photographs" tell).
4. Per-image check (one pass): skin not orange, black garments not muddy, stray blue not too loud. Fix only that.

### 4a. Fusion job schema (proven — mart_c_olivia, martina_warm1_olivia_ff)
{
  "filename": "<output_name>",
  "model": "gemini-2.5-flash-image",
  "outputDir": "/Users/Foongbear/Library/CloudStorage/GoogleDrive-da@heyleyholdings.com/Shared drives/Me + Lia/Content/3. Gemini Working Folder",
  "images": ["<Working Folder>/<scene>.png", "<Working Folder>/olivia_face_v2.png"],
  "prompt": "Two images... IMAGE 1 scene... IMAGE 2 the specific person... COMPLETELY REPLACE the head and face of the woman in IMAGE 1 with IMAGE 2... keep everything else identical: pose, dress, body, hands, setting, light. ONLY head and facial identity change.",
  "aspectRatio": "4:5"
}
images are local Mac paths (Working Folder), scene first, face second. Dispatch via dispatch_relay_job(path="jobs/<name>.json", content=<dict>). To make a NEW model's locked face: gemini-2.5-flash-image job with images: [] and a studio-portrait prompt (see olivia_face_v1 / melissa_face_v1).

## 5. Notes
Relay render/fusion works from chat via dispatch_relay_job. The gemini:generate_image MCP also renders directly (fallback when relay token is down); its aspectRatio rejects 4:5 — use 3:4.

## 6. Housekeeping / provenance
Supersedes single-grade MELIA_VISUAL_NORTHSTAR.md (rev b) grade section and MELIA_VISUAL_ENGINE.md grade section. Trash the superseded early North Star draft (1c5z0...). Grade anchor changed from "Martina Midnight + Clairo" to "the Mikuta North Star set," tuned cooler/dual to protect Me + Lia blues.
