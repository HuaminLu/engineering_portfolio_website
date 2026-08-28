# AGENTS.md — Agent Bootstrap & Operating Manual

You are an AI coding agent (Gemini Antigravity, Claude Code, Codex, or similar) working on
**Lucas (Huamin) Lu's engineering portfolio**. This file is your complete onboarding: read it
top to bottom once, then start working autonomously. Do not ask the human questions this file
already answers.

## Who this is for and what it's about

- **Owner:** Lucas (Huamin) Lu — 2B Mechatronics Engineering, University of Waterloo.
  GitHub `HuaminLu`, email `lunhuaminlu@gmail.com`.
- **Purpose:** co-op job applications to US robotics/tech companies (Tesla, Amazon Robotics,
  humanoid/industrial robotics). Recruiters skim this site in ~30 seconds — everything serves
  that reader.
- **Live site:** https://huaminlu.github.io/engineering_portfolio_website/ — GitHub Pages,
  deploys `main` branch root automatically ~1 min after every push. The repo is **public**.

## END GOAL (the definition of done)

The site is finished when:

1. **Every dashed placeholder box is replaced with a real photo or video**, processed through
   the media pipeline in `README.md` (§ The Media Pipeline — read it before touching media).
2. Captions match what their images actually show.
3. The `linkedin` and `resume.pdf` links in `index.html`'s about section point at real
   destinations (currently `href="#"` stubs — ask the human for the URLs/file once).
4. Every page loads correctly on desktop and phone, the home marquee auto-scrolls, drags,
   and clicks through, and the fold fits any screen height.
5. Nothing on the site overstates: numbers must trace back to the project docs in
   `Unitree G1 Work Upload/*/AGENTS.md` or the calculations shown on the pages.

Anything you do should move toward that state. When all five hold, the job is maintenance.

## Setup on a fresh machine (5 minutes, fully autonomous)

```bash
# 1. clone (https needs no key; use ssh if the machine has one configured)
git clone https://github.com/HuaminLu/engineering_portfolio_website.git
cd engineering_portfolio_website

# 2. preview locally — it's static, any file server works
python3 -m http.server 8080    # → http://localhost:8080

# 3. check the toolchain for media work (install what's missing)
magick -version || sudo apt install -y imagemagick
ffmpeg -version || sudo apt install -y ffmpeg

# 4. confirm push access
git remote -v && git push --dry-run origin main
# if push is rejected: `gh auth login` (browser device flow) or ask the human
# to run it — everything else works without auth.
```

There is **no build step, no package.json, no dependencies**. Never introduce one.

## Site architecture (what does what)

| File | Role |
|---|---|
| `index.html` | Home. Structure: header → circuit edge → `.fold` (hero + `.tiles` marquee + see-all button) → about → circuit edge → footer |
| `projects.html` | 3-column grid of all 8 tiles. Section breaks out to 78vw ≥1200px |
| `robot-arm.html`, `robot-hand.html` | Flagship hardware projects. Achievements list → hero → numbered sections (mechanical / calcs / electrical / embedded) |
| `unitree-g1.html` | Hub linking 5 sub-pages; platform specs; control-planes summary |
| `g1-*.html` (×5) | Deep-dive sub-pages, one per G1 project |
| `css/style.css` | The whole design system. Tokens in `:root` at the top. ~700 lines |
| `js/reveal.js` | Scroll-in fade animation. Adds `.reveal`→`.visible` via IntersectionObserver |
| `js/dragscroll.js` | Home marquee: **exact fold sizing** (measures viewport, sets `.fold` height), tile cloning for the seamless loop, float-accumulator autoscroll (~48 px/s, pauses on hover, resumes after 1 s), drag-to-pan that still lets plain clicks navigate |
| `images/` | Media. Pipeline: `README.md`. Raw dumps land in `images/_inbox/` |
| `Unitree G1 Work Upload/` | Real source of the 5 G1 projects, each with its own `AGENTS.md`. This is *content*, not site infrastructure — don't refactor it |

## NON-NEGOTIABLE CONVENTIONS

These encode decisions the owner made explicitly. Breaking them = redoing work.

1. **Pure static HTML/CSS/JS.** No frameworks, no npm, no build tools, no CDN libraries.
2. **Design tokens only** — colors come from the CSS variables in `:root` (`--bg: #0a0a0a`,
   `--fg: #f2f2f2`, `--fg-dim`, `--fg-faint`, `--line`, …). Never hardcode new colors.
3. **Font: Share Tech Mono** (Google Fonts) with monospace fallbacks. Body 24px weight 600;
   headings weight 800; `b/strong` 900. The owner asked for big, bold text — don't shrink it.
4. **Cache-busting is mandatory:** every HTML file references assets as
   `css/style.css?v=N` / `js/*.js?v=N`. Whenever you edit ANY css/js file, bump `N` in ALL
   HTML files in the same commit: `sed -i 's/?v=OLD/?v=NEW/g' *.html`. Skipping this ships
   invisible changes (GitHub Pages caches for 10 min) — it has bitten before.
5. **Nav dropdown is duplicated in every HTML file.** Change a menu item → update all 10
   files (use `sed` across `*.html`). Same for the footer.
6. **Folds must fit any screen.** Two measured folds exist: `.fold` on `index.html`
   (sized by `js/dragscroll.js` `sizeFold()`) and `.pfold` on every project page — title +
   stats + intro + hero fill exactly one viewport (sized by `js/reveal.js` `sizePfold()`,
   which runs even under reduced-motion). Neither height is hardcoded. If you add content
   above either fold, re-test short windows; height-based media queries progressively hide
   tile text below 820/700/560 px.
7. **Marquee behaviors** (owner-specified, don't regress): auto-scroll starts on load
   (~108 px/s); pauses on hover; resumes the moment the mouse leaves; drag-to-pan works;
   a plain click (< 6 px movement) still opens the project; the loop is seamless (tiles
   cloned once — edit the originals in `index.html`; clones are runtime-only); all tile
   text sections are JS-equalized to the tallest so image areas match heights.
8. **Layout widths:** content column is 1100px; the home marquee and the projects grid
   break out to **78vw centered** (grid only ≥1200px). Titles must stay left-aligned with
   their expanded content — break out the whole section, not just the grid.
9. **Media rules — most slots are gif-style VIDEO LOOPS now:** the six home marquee
   tiles and the five clip squares on `unitree-g1.html`'s project cards are `[ clip: … ]`
   slots — fill them with short (3–6 s) looping MP4s (`<video autoplay muted loop
   playsinline>`), which look exactly like GIFs at ~10× smaller size. Never actual .gif
   files unless explicitly requested. Static slots (`[ img: … ]`) stay JPG. Images
   ≤ 400 KB (heroes ≤ 700 KB), tile loops ~2 MB, page clips ≤ 6 MB, CRF 26–30,
   `-strip` EXIF before committing (public repo — GPS must not leak). Pipeline: `README.md`.
10. **Truthfulness:** the payload numbers, training steps, gains, and calculations shown on
    the pages come from real project docs. Never inflate or invent specs. If you write new
    copy, source it from `Unitree G1 Work Upload/*/AGENTS.md`.
11. **Commit style:** one logical change per commit, imperative subject line, push to
    `main` directly (no branches unless the human asks). Always `git pull --rebase` first —
    multiple machines/agents touch this repo.

12. **Mobile layer (≤ 640px) is a dedicated override block** at the END of
    `css/style.css` — every component has an explicit phone size there (17px body, 42px
    header with 22px equal-height nav buttons, full-width `100vw` marquee with 74vw tiles,
    full-width about photo, compact project-page type). When you add or restyle ANY
    component, add its mobile size to that block in the same commit. Android portrait
    (~360–430 px wide) is the reference target.

## Standard workflows

### A. Integrating photos/videos (the main outstanding task)
Follow `README.md` § The Media Pipeline exactly. Summary: human dumps raw files into
`images/_inbox/` → you open and *look at* every file → match content to the placeholder
`ph-label`/`ph-desc` slots → process with the magick/ffmpeg commands given there → write to
`images/<page>/<slot>.{jpg,mp4}` → replace each `div.ph` with `<img>`/`<video>` → fix any
caption the real image contradicts → commit, push, verify live → report placements +
leftovers to the human.

### B. Editing styles or scripts
Edit → test locally (resize the window; try the marquee: hover pause, drag, click-through)
→ bump `?v=` in all HTML files → commit + push → hard-refresh the live site to confirm.

### C. Adding a new project page
1. Copy `robot-hand.html` as the skeleton (its class usage is canonical).
2. Sections numbered `01/02/03…`; achievements list up top; placeholders with honest
   `ph-desc` shot descriptions; code snippets use `<span class="kw|cm|st|nm|fn">` highlighting.
3. Add the page to: the nav dropdown in **all** HTML files, `projects.html` grid,
   optionally the home marquee (both `index.html` tile and its `projects.html` twin),
   and prev/next `project-nav` links of its neighbors.

### D. Post-push verification (MANDATORY after every push)

Run the full checklist below after EVERY push, before reporting done. Check both PC and
Android layouts — use real browser rendering when available (DevTools device mode /
a browser-control tool at 390×844 for phone, ~1440×900 and a short 1366×700 window for PC);
otherwise verify each item structurally (curl the live files, grep the rules, trace the CSS).

**PC (wide + short windows):**
- [ ] Home fold: hero + marquee + See-All button fit ONE screen — button never cut off
      (resize height: 900 → 700 → 560 px; tile text compresses progressively, never clips)
- [ ] Marquee: auto-scrolls on load, pauses on hover, resumes instantly on mouse-off,
      drags, plain click opens the project, loop has no visible seam
- [ ] Every project page: `.pfold` fits ONE screen (title + stats + intro + hero); hero has
      breathing room below; scrolling reveals achievements + numbered sections with fades
- [ ] Nav: GALLERY / PROJECTS ▾ / HOME same pixel height, dropdown opens with 1/2/3/3.x
      indices, active page highlighted
- [ ] Gallery page: grid + title break out together ≥1200px, title left-aligns with tiles
- [ ] All tile text sections equal height (image areas align across the marquee)

**Android / phone (≤ 640px, portrait 9:16):**
- [ ] Header: logo + all three nav buttons on one line at 360px, equal heights, no wrap
- [ ] Marquee: full screen width, one ~74vw tile with the next peeking; touch-swipe pans;
      no horizontal page scroll anywhere
- [ ] All images/clips centered and spanning content width — INCLUDING the about portrait
- [ ] Clip slots keep sane ratios (G1 card clips become 16:9 banners; tiles stay portrait)
- [ ] Text: nothing overflows or wraps mid-word; code blocks scroll inside their box
- [ ] Both folds still fit one phone screen (svh handles the browser chrome)

**Both:**
- [ ] `grep -ho '?v=[0-9]*' *.html | sort -u` → exactly ONE version, bumped if css/js changed
- [ ] Live site returns 200 for /, css, both js files, favicon (after ~60 s deploy wait)
- [ ] Internal links resolve (script below); no console errors on load

### D2. Static verification commands
```bash
# link integrity: every internal href resolves
for f in *.html; do grep -o 'href="[a-z0-9-]*\.html"' $f | sed 's/href="//;s/"//' | sort -u \
  | while read l; do [ -f "$l" ] || echo "BROKEN in $f: $l"; done; done
# version consistency: exactly one ?v= value across the site
grep -ho '?v=[0-9]*' *.html | sort -u        # must print ONE line
# live check after push (wait ~60s)
curl -s -o /dev/null -w "%{http_code}\n" https://huaminlu.github.io/engineering_portfolio_website/
```

## Current backlog (highest value first)

1. **Place real media** — every placeholder on all 10 pages (workflow A). This is the big one.
2. Get the real LinkedIn URL and resume PDF from the human; wire up the about-links stubs.
3. After media lands: re-check fold fit on 1366×768 and a phone; tune if tiles overflow.
4. Nice-to-have: OpenGraph/meta tags (`og:image` using the arm hero) for link previews;
   a favicon (circuit/robot motif, black/white).
5. Nice-to-have: `sitemap.xml` + `robots.txt` for indexing.

## Operating posture

Work autonomously: read this file, check `git log --oneline -15` to see where things stand,
pick the highest-value backlog item you can complete, and do it end-to-end (implement →
verify → push → concise report). Ask the human only for things you genuinely cannot obtain
yourself (the resume file, the LinkedIn URL, photo dumps, judgment calls on which of two
good photos they prefer). When in doubt about a *style* decision, match what the site
already does — consistency beats novelty here. When in doubt about a *fact*, check
`Unitree G1 Work Upload/*/AGENTS.md`; if it isn't there, ask rather than invent.
