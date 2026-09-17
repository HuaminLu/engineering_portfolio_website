import os
import subprocess
from PIL import Image

FFMPEG = r"C:\Users\luhua\AppData\Local\Programs\Python\Python314\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
OUT_DIR = r"C:\github\engineering_portfolio_website\images\unitree-g1"

os.makedirs(OUT_DIR, exist_ok=True)

# Target: 270x360 (3:4 portrait)
WIDTH = 270
HEIGHT = 360
FPS = 16

SPECS = [
    # Top-Left (Cell 0)
    {
        "src": "videos/unitree-g1/mimic-both-take1-phone.mp4",
        "out": "grid-hand-mimic.gif",
        "start": 6.0,
        "duration": 5.2,
        "speed": 1.4,
        "crop": "ih*3/4:ih:(iw-ih*3/4)/2:0" # crop center 3:4 from landscape
    },
    {
        "src": "videos/unitree-g1/walk-2-phone.mp4",
        "out": "grid-g1-walk.gif",
        "start": 21.0,
        "duration": 6.0,
        "speed": 1.4,
        "crop": "iw:iw*4/3:0:(ih-iw*4/3)/2" # crop center 3:4 from portrait
    },

    # Top-Right (Cell 1)
    {
        "src": "videos/unitree-g1/arm-policy-real.mp4",
        "out": "grid-arm-policy.gif",
        "start": 8.0,
        "duration": 6.2,
        "speed": 1.4,
        "crop": "ih*3/4:ih:(iw-ih*3/4)/2:0" # crop center 3:4
    },
    {
        "src": "videos/unitree-g1/estop-remote-demo.mp4",
        "out": "grid-estop.gif",
        "start": 3.0,
        "duration": 4.8,
        "speed": 1.25,
        "crop": "iw:iw*4/3:0:(ih-iw*4/3)/2" # crop center 3:4
    },

    # Bottom-Left (Cell 2)
    {
        "src": "videos/unitree-g1/hands-seq-recorder-phone-only.mp4",
        "out": "grid-hand-recorder.gif",
        "start": 7.0,
        "duration": 5.8,
        "speed": 1.4,
        "crop": "ih*3/4:ih:(iw-ih*3/4)/2:0" # crop center 3:4
    },
    {
        "src": "videos/unitree-g1/barcode-scanner-grasp-1.mp4",
        "out": "grid-barcode-grasp.gif",
        "start": 3.0,
        "duration": 5.2,
        "speed": 1.35,
        "crop": "iw:iw*4/3:0:(ih-iw*4/3)/2" # crop center 3:4
    },

    # Bottom-Right (Cell 3)
    {
        "src": "videos/unitree-g1/arm-gui-1-phone.mp4",
        "out": "grid-arm-recorder.gif",
        "start": 12.0,
        "duration": 6.2,
        "speed": 1.4,
        "crop": "iw:iw*4/3:0:(ih-iw*4/3)/2" # crop center 3:4
    },
    {
        "src": "videos/unitree-g1/one-hand-pinch.mp4",
        "out": "grid-hand-pinch.gif",
        "start": 3.0,
        "duration": 5.0,
        "speed": 1.35,
        "crop": "iw:iw*4/3:0:(ih-iw*4/3)/2" # crop center 3:4
    },
]

results = []

for spec in SPECS:
    src = spec["src"]
    out_name = spec["out"]
    out_path = os.path.join(OUT_DIR, out_name)
    start = spec["start"]
    dur = spec["duration"]
    speed = spec["speed"]
    crop = spec["crop"]

    print(f"Generating {out_name} from {src}...")

    pts_factor = 1.0 / speed
    vf = (
        f"crop={crop},"
        f"scale={WIDTH}:{HEIGHT}:flags=lanczos,"
        f"setpts={pts_factor:.4f}*PTS,fps={FPS},"
        f"split[s0][s1];"
        f"[s0]palettegen=max_colors=128:stats_mode=diff[p];"
        f"[s1][p]paletteuse=dither=bayer:bayer_scale=3"
    )

    cmd = [
        FFMPEG,
        "-y",
        "-ss", str(start),
        "-t", str(dur),
        "-i", src,
        "-vf", vf,
        "-loop", "0",
        out_path
    ]

    subprocess.run(cmd, check=True, capture_output=True)

    # Measure exact gif duration
    img = Image.open(out_path)
    tot_dur = 0
    frames = 0
    try:
        while True:
            tot_dur += img.info.get("duration", 100)
            frames += 1
            img.seek(img.tell() + 1)
    except EOFError:
        pass

    size_kb = os.path.getsize(out_path) / 1024
    print(f"  -> Created {out_name}: {frames} frames, {tot_dur} ms ({tot_dur/1000:.2f}s), {size_kb:.1f} KB")
    results.append((out_name, tot_dur, size_kb))

print("\n--- Summary of G1 Grid GIFs ---")
for name, dur, size in results:
    print(f"'{name}': duration={dur}ms ({dur/1000:.2f}s), size={size:.1f}KB")
