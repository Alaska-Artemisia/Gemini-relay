---
name: melia-visual-identity
status: LOCKED 2026-07-05 (rev with 3b styling rules)
---

# Me + Lia Visual Identity — North Star (final)

## 0. TL;DR
- Grade = matched pair v3 COOL (blues) / v3 WARM (creams). Pick by garment palette. Anchor = Mikuta set, tuned cooler/dual for Me+Lia blues. Grade is the LAST step: scene (Gemini) -> identity lock (two-image Gemini fusion) -> grade (Lightroom).
- Look: warm-not-golden, matte, lifted-but-not-muddy blacks, blues/greens down, reds+skin protected, fine grain. Garment is the only saturated element; world stays faded warm-neutral Mediterranean.

## 1. Grade pair — choose by garment
Blue/navy/periwinkle/blue florals -> v3 COOL. Cream/blush/red-pink florals/oatmeal -> v3 WARM. Neutral -> default COOL. Warm kills blue; cool slightly flattens cream. They share everything except Temp + blue/aqua sat.

## 2. Numbers (frozen)
Files: MeLia_Mikuta_NorthStar_v3_COOL_blues.xmp, MeLia_Mikuta_NorthStar_v3_WARM_creams.xmp.
Shared: Contrast -8, Highlights -28, Shadows +18, Whites -10, Blacks +5, Texture +5, Clarity -3, Vibrance +4, Saturation -5; curve lifts black point to 16, highlights to 244 (matte); Green sat -30/lum +5, Yellow sat -8, Red 0, Orange (skin) hue +4/sat -6/lum +8; warm shadow colour grade; grain 12/20/50; sharpening 24, mask 40.
COOL: Temp +3, Tint +2, Blue sat -7, Aqua -10, Blue lum +3.  WARM: Temp +7, Tint +3, Blue sat -18, Aqua -14.

## 3. World, model, kit
Mediterranean only (Old Towns, Harbours, Beaches, Coastal Rocks), whitewashed Cycladic + warm stone, NO seamless studio final. Models: Olivia + Melissa (consistency beats a prettier one-off). Kit: stacked silver, one French market basket, black flat sandals, undone hair, minimal gold. Ads at 4:5 / 1:1 / 9:16.

## 3b. STYLING & ENERGY — "a girl in a good dress on holiday" (LOCKED, hard rules)
Avoid posed/still/glowing catalog e-comm. Mikuta = CAUGHT MOMENTS, not modelling.
- Eye contact UNDER 25%. Mostly looking away/down/off-frame/mid-laugh; ~1 in 5 meets the lens. Never a held presenting gaze.
- On the go, LIVING in the dress: walking, mid-stride, climbing a step, turning, ducking under bougainvillea, coming out of a doorway, hem+hair in motion. Sitting/posing is the rare exception.
- Slightly undone: hair a little messed/windblown/pushed back; strap shifted; hem creased from wear. Groomed-perfect is wrong.
- Matte, never shiny/glowing: real skin, no dewy sheen, no beauty-glow, no glamour lighting. Holiday girl, not campaign face.
- Having a good time: ease, warmth, candid body language, not a held expression. Snapshot energy.
- Hands busy / weight asymmetric: touching a wall, holding skirt or basket, brushing hair; weight off one hip. Never stiff arms or primly folded hands.
- ANCHOR to real Mikuta frames as pose/energy references in the render (feed 1-2 alongside identity + garment), not just prose.

## 4. Pipeline (order load-bearing)
1. Scene/pose = Gemini render. Single-image regen drifts identity.
2. Identity lock = "fusion" = TWO-image Gemini job (gemini-2.5-flash-image), images [scene, olivia_face_v2.png], prompt "COMPLETELY REPLACE the head/face in IMAGE 1 with IMAGE 2". Locked faces in Working Folder: olivia_face_v2.png, melissa_face_v1.png. Fire via dispatch_relay_job to jobs/<name>.json. NO FaceFusion app.
3. Grade = Lightroom LAST, correct preset from the pair.
4. One-pass check: skin not orange, blacks not muddy, stray blue not loud.

Fusion/render schema: {filename, model:"gemini-2.5-flash-image", outputDir:<Working Folder>, images:[<mac paths>], prompt, aspectRatio}. gemini:generate_image MCP is a direct fallback; it rejects 4:5, use 3:4.

## 6. Provenance
Supersedes single-grade MELIA_VISUAL_NORTHSTAR (rev b) + MELIA_VISUAL_ENGINE grade sections. Anchor changed Martina+Clairo -> Mikuta set, cooler/dual for blues.
