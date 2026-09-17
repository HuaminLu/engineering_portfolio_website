import os
import shutil
import subprocess

FFMPEG_PATH = r"C:\Users\luhua\AppData\Local\Programs\Python\Python314\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"
SRC_BASE = r"C:\Users\luhua\OneDrive - MSFT\[4] University\2WA Resources\Robot Videos Final Day\Combined Phone & PC\Robot Arm"
DEST_DIR = r"C:\github\engineering_portfolio_website\images\robot-arm"

os.makedirs(DEST_DIR, exist_ok=True)

# 1. Image Copy Mapping: (source_rel_path, dest_filename)
IMAGE_COPIES = [
    # Hero Image
    ("Final Image of Arm.png", "hero-final-assembled-arm.png"),

    # Section 01: CAD
    (os.path.join("1. CAD", "Screenshot 2026-09-17 044346.png"), "cad-full-arm-iso.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 044431.png"), "cad-shoulder-base-hub.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 045402.png"), "cad-elbow-joint-clearance.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 045422.png"), "cad-forearm-wrist-roll.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 045635.png"), "cad-wrist-pitch-yaw.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 045642.png"), "cad-wireframe-internals.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 045647.png"), "cad-spigot-shear-joint.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 050345.png"), "cad-robstride-actuator-model.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 050400.png"), "cad-terminal-clamp.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 050437.png"), "cad-brush-slip-ring.png"),
    (os.path.join("1. CAD", "Screenshot 2026-09-17 051002.png"), "cad-pick-and-place-pose.png"),

    # Section 02: Cassette Ring
    (os.path.join("2. Cassette Ring", "PXL_20260831_172316434.jpg"), "cassette-spiral-coil.jpg"),
    (os.path.join("2. Cassette Ring", "PXL_20260831_184547056.jpg"), "cassette-bearing-race.jpg"),
    (os.path.join("2. Cassette Ring", "PXL_20260902_053921469.jpg"), "cassette-bench-fitup.jpg"),
    (os.path.join("2. Cassette Ring", "PXL_20260903_205901662.jpg"), "cassette-joint-graduation.jpg"),

    # Section 03: Electrical
    (os.path.join("3. Electrical", "PXL_20260904_074523250.jpg"), "elec-multimeter-continuity.jpg"),
    (os.path.join("3. Electrical", "PXL_20260905_225403136.jpg"), "elec-xt30-can-harness.jpg"),
    (os.path.join("3. Electrical", "PXL_20260905_225855736.jpg"), "elec-harness-loom.jpg"),
    (os.path.join("3. Electrical", "PXL_20260905_233231400.jpg"), "elec-bench-transceiver-test.jpg"),

    # Section 04: Motor Module & Buck Regulation
    (os.path.join("4. Motor Module", "PXL_20260904_074523250.jpg"), "motor-robstride-bench.jpg"),
    (os.path.join("4. Motor Module", "PXL_20260905_225403136.jpg"), "motor-joint-bracket-fit.jpg"),
    (os.path.join("4. Motor Module", "PXL_20260905_225855736.jpg"), "motor-bench-wiring.jpg"),
    (os.path.join("4. Motor Module", "PXL_20260905_233231400.jpg"), "motor-voltage-check.jpg"),
    (os.path.join("Motor Module with Buck Converters", "PXL_20260913_015943459.jpg"), "buck-module-mount.jpg"),
    (os.path.join("Motor Module with Buck Converters", "PXL_20260913_015959391.jpg"), "buck-decoupling-cap.jpg"),
    (os.path.join("Motor Module with Buck Converters", "PXL_20260913_063316326.jpg"), "buck-bench-load-test.jpg"),
    (os.path.join("Motor Module with Buck Converters", "PXL_20260913_065348663.jpg"), "buck-harness-routing.jpg"),
    (os.path.join("Motor Module with Buck Converters", "PXL_20260913_070512545.jpg"), "buck-assembled-module.jpg"),

    # Section 05: Assembly
    (os.path.join("5. Assembly", "PXL_20260908_192820322.jpg"), "assembly-link-spigot-press.jpg"),
    (os.path.join("5. Assembly", "PXL_20260908_193030684.jpg"), "assembly-heat-set-inserts.jpg"),
    (os.path.join("5. Assembly", "PXL_20260908_201313518.jpg"), "assembly-bearing-seating.jpg"),
    (os.path.join("5. Assembly", "PXL_20260911_065810858.jpg"), "assembly-motor-mounting.jpg"),
    (os.path.join("5. Assembly", "PXL_20260913_001952667.jpg"), "assembly-lower-arm-cluster.jpg"),
    (os.path.join("5. Assembly", "PXL_20260913_015042559.jpg"), "assembly-forearm-wiring-pass.jpg"),
    (os.path.join("5. Assembly", "PXL_20260913_021039333.jpg"), "assembly-wrist-clevis-fit.jpg"),
    (os.path.join("5. Assembly", "PXL_20260913_053103207.jpg"), "assembly-full-arm-upright.jpg"),
    (os.path.join("5. Assembly", "PXL_20260913_053108889.jpg"), "assembly-cable-strain-relief.jpg"),
    (os.path.join("5. Assembly", "PXL_20260913_053119484.jpg"), "assembly-bench-rest-pose.jpg"),

    # Section 06: Gripper
    (os.path.join("Gripper", "Screenshot 2026-09-17 044402.png"), "gripper-cad-assembly.png"),
    (os.path.join("Gripper", "Screenshot 2026-09-17 044510.png"), "gripper-cad-jaw-detail.png"),
    (os.path.join("Gripper", "Screenshot 2026-09-17 044519.png"), "gripper-cad-flange-interface.png"),
    (os.path.join("Gripper", "PXL_20260917_075849329.jpg"), "gripper-build-bench.jpg"),
    (os.path.join("Gripper", "PXL_20260917_075857671.jpg"), "gripper-mounted-wrist.jpg"),

    # Section 07: Control Hub
    (os.path.join("CAD", "Screenshot 2026-09-17 044542.png"), "hub-cad-enclosure-iso.png"),
    (os.path.join("CAD", "Screenshot 2026-09-17 044602.png"), "hub-cad-internal-rails.png"),
    (os.path.join("Electrical", "PXL_20260916_094653026.jpg"), "hub-elec-terminal-blocks.jpg"),
    (os.path.join("Electrical", "PXL_20260916_100233781.jpg"), "hub-elec-transceiver-wiring.jpg"),
    (os.path.join("Electrical", "PXL_20260916_100442521.jpg"), "hub-elec-switch-integration.jpg"),
    (os.path.join("Finished", "PXL_20260916_100537918.jpg"), "hub-finished-open-top.jpg"),
    (os.path.join("Finished", "Finished control hub with harness at base of arm.jpg"), "hub-finished-base-harness.jpg"),
    (os.path.join("Finished", "PXL_20260917_080014009.jpg"), "hub-finished-pedestal-setup.jpg"),
    (os.path.join("Finished", "PXL_20260917_080146457.jpg"), "hub-finished-rear-ports.jpg"),
    (os.path.join("Finished", "PXL_20260917_080157893.jpg"), "hub-finished-indicator-leds.jpg"),

    # Section 08: Weighted Base Mount
    (os.path.join("Weighted Base Mount", "Screenshot 2026-09-17 051108 - CAD.png"), "base-cad-pedestal.png"),
    (os.path.join("Weighted Base Mount", "PXL_20260917_080635006.jpg"), "base-ballast-plate-setup.jpg"),
    (os.path.join("Weighted Base Mount", "PXL_20260917_080646149.jpg"), "base-high-load-stability.jpg"),
]

print(f"Copying {len(IMAGE_COPIES)} images...")
for rel_src, dest_name in IMAGE_COPIES:
    src_full = os.path.join(SRC_BASE, rel_src)
    dest_full = os.path.join(DEST_DIR, dest_name)
    if not os.path.exists(src_full):
        print(f"WARNING: Source file does not exist: {src_full}")
    else:
        shutil.copy2(src_full, dest_full)
        print(f"Copied: {dest_name}")

def convert_to_gif(src_rel, dest_name, start_sec=0, duration=8, speed=1.8, width=540, fps=18):
    src_full = os.path.join(SRC_BASE, src_rel)
    dest_full = os.path.join(DEST_DIR, dest_name)
    print(f"\nConverting {src_rel} -> {dest_name}...")

    pts_factor = 1.0 / speed
    vf_filter = (
        f"setpts={pts_factor:.4f}*PTS,fps={fps},"
        f"scale={width}:-1:flags=lanczos,split[s0][s1];"
        f"[s0]palettegen=max_colors=128:stats_mode=diff[p];"
        f"[s1][p]paletteuse=dither=bayer:bayer_scale=3"
    )

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-ss", str(start_sec),
        "-t", str(duration),
        "-i", src_full,
        "-vf", vf_filter,
        "-loop", "0",
        dest_full
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"ERROR converting {dest_name}: {res.stderr}")
    else:
        size_mb = os.path.getsize(dest_full) / (1024 * 1024)
        print(f"SUCCESS: {dest_name} ({size_mb:.2f} MB)")

convert_to_gif(os.path.join("Videos", "PXL_20260917_080935191.TS.mp4"), "hero-manual-articulation.gif", start_sec=0, duration=15, speed=1.9, width=540, fps=18)
convert_to_gif(os.path.join("Videos", "PXL_20260913_022144903.LS.mp4"), "assembly-joint-rotation-wiring.gif", start_sec=1, duration=12, speed=1.8, width=540, fps=18)
convert_to_gif(os.path.join("Gripper", "PXL_20260917_075903333.TS.mp4"), "gripper-rack-pinion-jaw.gif", start_sec=1, duration=10, speed=1.8, width=540, fps=18)

print("\nAll Robot Arm assets processed successfully!")
