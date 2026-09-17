import os
import re

with open("robot-arm.html", "r", encoding="utf-8") as f:
    html = f.read()

# 1. Check all img src
imgs = re.findall(r'<img\s+[^>]*src=["\']([^"\']+)["\']', html)
print(f"Checking {len(imgs)} image references...")
missing = []
for src in imgs:
    src_clean = src.split("?")[0]
    if not os.path.exists(src_clean):
        missing.append(src)

if missing:
    print(f"ERROR: Missing images: {missing}")
else:
    print(f"SUCCESS: All {len(imgs)} referenced images exist on disk!")

# 2. Check HTML tags
tags = re.findall(r'<(/?[a-zA-Z0-9]+)(?:\s+[^>]*)?>', html)
stack = []
void_tags = {"meta", "link", "img", "br", "hr", "input", "source", "!doctype"}
for tag in tags:
    tag = tag.lower()
    if tag in void_tags:
        continue
    if tag.startswith("/"):
        closing = tag[1:]
        if not stack:
            print(f"Unmatched closing tag: </{closing}>")
        elif stack[-1] != closing:
            print(f"Mismatched tag: expected </{stack[-1]}>, got </{closing}>")
        else:
            stack.pop()
    else:
        stack.append(tag)

if stack:
    print(f"Unclosed tags remaining: {stack}")
else:
    print("SUCCESS: HTML tags are perfectly balanced!")

# 3. Check scripts
scripts = re.findall(r'<script\s+[^>]*src=["\']([^"\']+)["\']', html)
print(f"Scripts referenced: {scripts}")
for s in scripts:
    s_clean = s.split("?")[0]
    if not os.path.exists(s_clean):
        print(f"Missing script: {s_clean}")
    else:
        print(f"Script verified: {s_clean}")
