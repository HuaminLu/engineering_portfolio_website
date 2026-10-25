#!/usr/bin/env python3
"""Build a self-contained OFFLINE copy of the portfolio website.

    python scripts/make_offline_copy.py [destination]

Default destination:  <Desktop>/Portfolio Website (Offline)

What it does
  * copies every page, css/, js/, images/, videos/, demos/, favicon and resume
    (skips .git, source-code folders, docs, and the unused legacy .gif files)
  * embeds the Share Tech Mono web font straight into css/style.css and removes
    the Google Fonts tags, so pages render identically with no internet
  * gives the Brookfield demo page local copies of Chart.js, Font Awesome and
    the Outfit font instead of its CDN links
  * writes README.txt and verifies that every src/href/poster/url() resolves
    and that no page still points at the internet (outbound links excepted)

Needs internet only while building (a few small downloads). Re-run after any
site change. An existing copy made by this script is replaced; any other folder
at the destination is left alone and the script stops.
Visitor analytics (js/tracker.js) switch themselves off when opened from disk.
"""
import base64
import datetime
import os
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "OFFLINE COPY made by scripts/make_offline_copy.py"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
SHARE_TECH_CSS = "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap"
SKIP_NAMES = {"_inbox", "__pycache__", "Thumbs.db", ".gitkeep", ".DS_Store"}
OUTBOUND_OK = re.compile(r"github\.com|linkedin\.com|w3\.org")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def desktop_dir():
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
            if buf.value:
                return buf.value
        except Exception:
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def ignore(_dir, names):
    return {n for n in names if n.lower().endswith(".gif") or n in SKIP_NAMES}


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(desktop_dir(), "Portfolio Website (Offline)")
    dest = os.path.abspath(dest)

    # --- safety: only replace a folder this script created -----------------
    if os.path.exists(dest):
        readme = os.path.join(dest, "README.txt")
        ok = os.path.isfile(readme) and MARK in open(readme, encoding="utf-8", errors="ignore").read()
        if not ok:
            sys.exit(f"Destination exists and was not made by this script, stopping: {dest}")
        shutil.rmtree(dest)
    os.makedirs(dest)
    # provisional marker so an interrupted build can be replaced by the next run
    write(os.path.join(dest, "README.txt"), MARK + "\n(build in progress)\n")
    print(f"building offline copy in {dest}")

    # --- 1. copy the site ---------------------------------------------------
    for f in os.listdir(ROOT):
        if f.endswith(".html") or f in ("favicon.svg", "favicon.ico", "resume.pdf", "manifest.json", "sw.js"):
            shutil.copy2(os.path.join(ROOT, f), dest)
    # friendly-named entry point (a copy of index.html) for Start-menu / desktop shortcuts
    shutil.copy2(os.path.join(ROOT, "index.html"), os.path.join(dest, "Portfolio Website - Huamin Lu.html"))
    for d in ("css", "js", "images", "videos", "demos"):
        src = os.path.join(ROOT, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dest, d), ignore=ignore)

    # --- 2. embed Share Tech Mono, drop Google Fonts tags -------------------
    css_text = fetch(SHARE_TECH_CSS).decode("utf-8")
    woff_urls = sorted(set(re.findall(r"https://fonts\.gstatic\.com[^)]+", css_text)))
    if not woff_urls:
        sys.exit("could not find the Share Tech Mono font file in the Google Fonts response")
    b64 = base64.b64encode(fetch(woff_urls[0])).decode()
    style = os.path.join(dest, "css", "style.css")
    face = ("/* Offline copy: Share Tech Mono (SIL Open Font License) is embedded here so the site\n"
            "   renders identically without internet access. */\n"
            "@font-face { font-family: 'Share Tech Mono'; font-style: normal; font-weight: 400; "
            "font-display: swap;\n  src: url(data:font/woff2;base64,%s) format('woff2'); }\n\n" % b64)
    write(style, face + open(style, encoding="utf-8").read())

    pages = 0
    for f in os.listdir(dest):
        if not f.endswith(".html"):
            continue
        p = os.path.join(dest, f)
        s0 = s = open(p, encoding="utf-8").read()
        s = re.sub(r'\n\s*<link rel="preconnect" href="https://fonts\.(?:googleapis|gstatic)\.com"[^>]*>', "", s)
        s = re.sub(r'\n\s*<link href="https://fonts\.googleapis\.com/css2\?family=Share\+Tech\+Mono[^"]*" rel="stylesheet">', "", s)
        if s != s0:
            pages += 1
            write(p, s)

    # --- 3. Brookfield demo: local Chart.js, Font Awesome, Outfit -----------
    demo = os.path.join(dest, "demos", "brookfield-eco-monitor.html")
    if os.path.isfile(demo):
        vend = os.path.join(dest, "demos", "vendor")
        s = open(demo, encoding="utf-8").read()

        m = re.search(r'https://cdn\.jsdelivr\.net/npm/chart\.js[^"]*', s)
        if m:
            write_bytes(os.path.join(vend, "chart.umd.js"), fetch(m.group(0)))
            s = s.replace(m.group(0), "vendor/chart.umd.js")

        m = re.search(r'https://cdnjs\.cloudflare\.com/ajax/libs/font-awesome/([^/"]+)/css/all\.min\.css', s)
        if m:
            fa_css = fetch(m.group(0)).decode("utf-8")
            write(os.path.join(vend, "css", "all.min.css"), fa_css)
            base = f"https://cdnjs.cloudflare.com/ajax/libs/font-awesome/{m.group(1)}/webfonts/"
            for name in sorted(set(re.findall(r"\.\./webfonts/([^)\"']+)", fa_css))):
                write_bytes(os.path.join(vend, "webfonts", name), fetch(base + name))
            s = s.replace(m.group(0), "vendor/css/all.min.css")

        m = re.search(r'https://fonts\.googleapis\.com/css2\?family=Outfit[^"]*', s)
        if m:
            ocss = fetch(m.group(0).replace("&amp;", "&")).decode("utf-8")
            for i, url in enumerate(sorted(set(re.findall(r"https://fonts\.gstatic\.com[^)]+", ocss)))):
                name = url.rsplit("/", 1)[1].split("?")[0]
                if not re.fullmatch(r"[\w.-]+\.(?:woff2|woff|ttf)", name):
                    name = f"outfit-{i}.woff2"  # Google sometimes serves a query-string "kit" URL
                write_bytes(os.path.join(vend, "fonts", name), fetch(url))
                ocss = ocss.replace(url, "fonts/" + name)
            write(os.path.join(vend, "outfit.css"), ocss)
            s = s.replace(m.group(0), "vendor/outfit.css")
        write(demo, s)

    # --- 4. README --------------------------------------------------------
    try:
        commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"
    with open(os.path.join(dest, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(f"""Huamin (Lucas) Lu - Engineering Portfolio
{MARK}
=========================================================

HOW TO OPEN
  Double-click  index.html  or  "Portfolio Website - Huamin Lu.html"
  (same page, opens in your default browser). favicon.ico is the icon
  to use for a shortcut.
  Everything works without internet: all pages, photos, videos,
  the auto-scrolling project marquee, carousels, the Brookfield demo.

NOTES
  - Visitor analytics are automatically disabled when opened from disk,
    so showing this copy never sends an email or logs a visit.
  - Outbound links (GitHub, LinkedIn, the two external project repos)
    need internet. Everything else is local.
  - Fonts are embedded, so it looks identical to the live site.
  - Source of truth: https://huaminlu.github.io/engineering_portfolio_website
    Generated {datetime.date.today().isoformat()} from commit {commit}.
    Re-run  python scripts/make_offline_copy.py  after site changes.
""")

    # --- 5. verify --------------------------------------------------------
    missing, external, total = [], [], 0
    for root, _dirs, files in os.walk(dest):
        for f in files:
            if not f.endswith((".html", ".css")):
                continue
            s = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            refs = [(m, False) for m in re.findall(r'(?:src|href|poster)="([^"]+)"', s)]
            if f.endswith(".css"):
                refs += [(m.strip("'\""), True) for m in re.findall(r"url\(([^)]+)\)", s)]
            for ref, _is_css in refs:
                if ref.startswith(("mailto:", "#", "data:")):
                    continue
                if ref.startswith("http"):
                    if not OUTBOUND_OK.search(ref):
                        external.append((f, ref))
                    continue
                path = ref.split("?")[0].split("#")[0]
                if not path:
                    continue
                total += 1
                if not os.path.isfile(os.path.normpath(os.path.join(root, path))):
                    missing.append((f, ref))
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(dest) for f in fs)
    nfiles = sum(len(fs) for _r, _d, fs in os.walk(dest))
    print(f"pages cleaned of Google Fonts tags: {pages}")
    print(f"references checked: {total}  missing: {len(missing)}  still online: {len(external)}")
    for item in (missing + external)[:20]:
        print("   PROBLEM", item)
    print(f"done: {nfiles} files, {size / 1048576:.0f} MB")
    print(f"open: {os.path.join(dest, 'index.html')}")
    return 1 if (missing or external) else 0


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


if __name__ == "__main__":
    sys.exit(main())
