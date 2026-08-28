# Engineering Portfolio — Huamin (Lucas) Lu

Live site: **https://huaminlu.github.io/engineering_portfolio_website/**

Static HTML/CSS/JS portfolio for UWaterloo Mechatronics co-op applications. No frameworks,
no build step — edit, commit, push, and GitHub Pages deploys in ~1 minute.

- Theme: black background, white Share Tech Mono text, circuit-trace top/bottom borders
- Pages: home (auto-scrolling project marquee + about), projects grid, 3 main project pages,
  Unitree G1 hub + 5 sub-project pages
- `Unitree G1 Work Upload/` holds the actual source code of the five G1 projects, each with
  its own `AGENTS.md` documentation

**For AI agents working on this repo: read `AGENTS.md` first.** It contains setup,
site conventions, and the task backlog. This README documents the repo for humans and the
full media pipeline in detail.

---

# The Media Pipeline (images & videos)

Every image/video slot in the site is currently a dashed **placeholder box** describing the
exact shot that belongs there. This section defines the complete pipeline from "folder of
raw photos" to "deployed site".

## 1. Where to upload raw files

Drop everything — photos, screenshots, videos, any filenames, uncropped, unedited — into:

```
images/_inbox/
```

That folder is the only manual step for the human. Phone dumps, screenshots with names like
`IMG_4283.jpg` or `Screenshot from 2026-08-27.png` are all fine. Original filenames are
*hints*, never requirements. (Alternative: any folder outside the repo works too — just tell
the agent the path.)

## 2. Final folder structure (what processed media becomes)

Processed files live in one folder per page, named after the **slot** they fill:

```
images/
  _inbox/            ← raw dumps (never deployed; gitignored once processing is done)
  home/              ← tiles on the landing marquee + about portrait
    tile-robot-arm.jpg
    tile-robot-hand.jpg
    tile-unitree-g1.jpg
    tile-hand-mimic.jpg
    tile-arm-policy.jpg
    tile-walk.jpg
    portrait.jpg
  robot-arm/
    hero.jpg  cad-assembly.jpg  spigot-joint.jpg  gyroid-slicer.jpg
    full-extension.jpg  internal-wiring.jpg  actuator.jpg  base-electronics.jpg
    motion-demo.mp4
  robot-hand/
    hero.jpg  palm-cad.jpg  finger-linkage.jpg  printed-parts.jpg
    wiring-schematic.jpg  bench-electronics.jpg  grip-demo.mp4
  unitree-g1/        hero.jpg  montage.mp4
  g1-hand-mimic/     (slots per that page's placeholders)
  g1-hand-recorder/  …
  g1-arm-policy/     …
  g1-arm-recorder/   …
  g1-walk/           …
```

**Naming rule:** lowercase-kebab-case, named for the *content slot*, not the camera file.
The slot name comes from the placeholder's `ph-label` text (e.g. `[ img: spigot joint
cross-section ]` → `spigot-joint.jpg`).

## 3. How the agent decides which photo goes where

The agent does **not** need the human to sort or rename anything:

1. **Read every placeholder first.** Each `<div class="ph">` in the HTML carries a
   `ph-label` (slot name) and a `ph-desc` (one-line description of the exact shot wanted,
   e.g. "Cross-section CAD view showing the interlocking lip and heat-set inserts").
   Collect the full slot list per page before opening any image.
2. **Open and look at every file in `_inbox/`** with the image-reading tool (Claude: `Read`
   on the image path; Gemini: attach/view the image). For each one, note in one line: what
   it shows (CAD render vs slicer screenshot vs bench photo vs GUI screenshot vs robot
   photo), orientation, and quality.
3. **Match by content, not filename.** A SolidWorks isometric of cylindrical links →
   `robot-arm/cad-assembly.jpg`. A yellow/black slider GUI → `g1-arm-recorder/` slots.
   The MuJoCo viewer with a red sphere → `g1-arm-policy/`.
4. **Ties:** several candidates for one slot → pick the sharpest, most complete frame; note
   runners-up in the commit message so the human can ask to swap. Photo matches no slot →
   leave it in `_inbox/` and list it as unplaced in the summary. Slot has no photo → leave
   the placeholder box in place (they degrade gracefully).

## 4. Cropping & resizing policy

**Do not hand-crop for aspect ratio.** Every media slot uses CSS `object-fit: cover`, which
center-crops in the browser to whatever box the layout gives it (tiles are portrait-ish,
`fig-wide` is 21:9, grid tiles are square). One uncropped master per slot serves all layouts.

Only edit pixels when content demands it:
- Crop away surrounding clutter when the subject is a small part of the frame
  (`magick in.jpg -gravity center -crop 80x80%+0+0 out.jpg` or targeted offsets)
- Rotate if the camera was tilted; never stretch or change aspect non-uniformly

**Resize + compress (ImageMagick):**

```bash
# heroes / fig-wide (full-width slots)
magick input.jpg -auto-orient -resize '1920x1920>' -strip -quality 82 images/<page>/<slot>.jpg
# everything else (tiles, split figures, rows)
magick input.jpg -auto-orient -resize '1400x1400>' -strip -quality 82 images/<page>/<slot>.jpg
# screenshots with text/UI (GUIs, slicers, terminals) — PNG source, keep as high-quality jpg
magick input.png -resize '1600x1600>' -strip -quality 90 images/<page>/<slot>.jpg
```

Budgets: **≤ 400 KB** per regular image, **≤ 700 KB** per hero. If over budget, step
quality down (82 → 75 → 70) before shrinking dimensions. `-strip` removes EXIF (also
removes GPS location data — required before publishing to a public repo).

## 5. Videos — compression, and mp4 vs GIF

**Default: MP4, not GIF.** The site already styles `<video autoplay muted loop playsinline>`
— it autoplays exactly like a GIF at ~10× smaller size and full color. GIF is limited to 256
colors and balloons to tens of MB.

```bash
# standard web clip: h264, quality-targeted (CRF), audio stripped, streaming-friendly
ffmpeg -i in.mp4 -vf "scale=1280:-2" -c:v libx264 -crf 28 -preset slow -an \
       -movflags +faststart images/<page>/<slot>.mp4
```

Balancing quality vs size — the CRF ladder (lower = better quality, bigger file):
- Start at `-crf 28`. Target **≤ 6 MB** per clip, hero demo up to **10 MB**.
- Too big → `-crf 30`, then drop `scale=960:-2`. Blocky/smeared → `-crf 26`.
- Trim to the interesting 5–15 s first: `-ss 00:00:02 -t 8` before `-i` reordering as needed.
- Robot demos: keep clips short and looping-friendly (motion that ends near its start pose
  loops cleanly).

GIF only if explicitly requested (e.g. for a README that can't embed video):

```bash
ffmpeg -i in.mp4 -vf "fps=12,scale=640:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse" \
       -t 8 images/<page>/<slot>.gif      # palette pass = far better colors at same size
```

## 6. Wiring media into the HTML

Replace the placeholder `div.ph` — keep the surrounding figure wrapper untouched:

```html
<!-- before -->
<div class="ph"> …icon/label/desc… </div>

<!-- image -->
<img src="images/robot-arm/cad-assembly.jpg"
     alt="SolidWorks isometric render of the 7-DOF arm" loading="lazy">

<!-- video -->
<video autoplay muted loop playsinline preload="metadata">
  <source src="images/robot-arm/motion-demo.mp4" type="video/mp4">
</video>
```

Rules:
- `alt` text = the placeholder's `ph-desc`, tightened. `loading="lazy"` on everything
  except each page's hero image.
- **Home marquee tiles:** the same tile markup appears twice per project on `index.html`?
  No — tiles exist once in the HTML; `js/dragscroll.js` clones them at runtime for the
  loop, so replacing each `div.ph` inside `.tile-img` once is enough. The projects-grid
  page (`projects.html`) has its own copy of each tile — update both.
- **Captions:** after seeing the real image, adjust the adjacent `caption` text if it
  describes something the photo doesn't show. Never leave a caption contradicting its image.

## 7. Deploying

```bash
cd ~/Personal/engineering_portfolio_website        # (path on the original machine)
# bump the cache-busting version in ALL html files whenever css/js changed:
sed -i 's/?v=<current>/?v=<current+1>/g' *.html    # check current with: grep -o '?v=[0-9]*' index.html | head -1
git add -A
git commit -m "Add project photos: <what was placed where>"
git push origin main
```

GitHub Pages redeploys automatically (~1 min). Adding images alone doesn't require a version
bump (new URLs are uncached); editing `css/style.css` or any `js/*.js` **always** requires
bumping `?v=` in every HTML file, or browsers serve stale assets for 10 minutes.

**After deploying:** verify with `curl -s -o /dev/null -w "%{http_code}" <live-image-url>`
and eyeball the live page. Report to the human: what was placed where, what's still
unplaced, total added size.

---

# Repo map

```
index.html                     home: hero, auto-scroll marquee, see-all button, about
projects.html                  3-wide grid of all 8 project tiles
robot-arm.html                 7-DOF QDD arm (achievements, CAD, torque calcs, CAN, code)
robot-hand.html                10-DOF hand (surfacing, power design, I2C offloading)
unitree-g1.html                G1 hub: platform, 5 sub-project cards, control planes
g1-hand-mimic.html             G1 sub-pages (5) — deep dives with real code
g1-hand-recorder.html
g1-arm-policy.html
g1-arm-recorder.html
g1-walk.html
css/style.css                  the entire design system (tokens at top)
js/reveal.js                   scroll-fade animations (IntersectionObserver)
js/dragscroll.js               marquee: fold sizing, loop cloning, autoscroll, drag
images/                        media (see pipeline above)
Unitree G1 Work Upload/        real source code of the 5 G1 projects + AGENTS.md each
AGENTS.md                      ← agent bootstrap: conventions, workflows, backlog
```

Built with Claude Code. Maintained by whichever AI agent is reading this — see `AGENTS.md`.
