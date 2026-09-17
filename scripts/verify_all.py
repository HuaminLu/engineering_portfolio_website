import os
import re

for filename in ["robot-hand.html", "index.html", "projects.html", "robot-arm.html"]:
    with open(filename, "r", encoding="utf-8") as f:
        content = f.read()
    imgs = re.findall(r'<img\s+[^>]*src=["\']([^"\']+)["\']', content)
    missing = [img for img in imgs if not os.path.exists(img.split("?")[0])]
    print(f"{filename}: {len(imgs)} images checked, missing: {len(missing)}")
    if missing:
        print("  Missing:", missing)
print("All pages verified!")
