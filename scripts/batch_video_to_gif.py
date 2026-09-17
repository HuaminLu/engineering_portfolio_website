#!/usr/bin/env python3
"""
scripts/batch_video_to_gif.py

Optimized batch pipeline to convert video recordings into high-quality, lightweight
animated GIFs for the portfolio website using 2-pass palettegen.
"""

import os
import sys
import argparse
import subprocess

DEFAULT_FFMPEG = r"C:\Users\luhua\AppData\Local\Programs\Python\Python314\Lib\site-packages\imageio_ffmpeg\binaries\ffmpeg-win-x86_64-v7.1.exe"

def convert_video_to_gif(src, dest, start="00:00:00", duration=16.0, speed=2.0, fps=12, width=260, ffmpeg_bin=DEFAULT_FFMPEG):
    if not os.path.exists(src):
        raise FileNotFoundError(f"Source video not found: {src}")
        
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    palette_tmp = os.path.join(os.path.dirname(os.path.abspath(dest)), f"_tmp_palette_{os.getpid()}.png")
    
    pts_factor = 1.0 / speed
    vf_base = f"setpts={pts_factor:.4f}*PTS,fps={fps},scale={width}:-1:flags=lanczos"
    
    print(f"[*] Processing: {os.path.basename(src)} -> {os.path.basename(dest)}")
    print(f"    Speed: {speed}x | Clip: {start} for {duration}s -> Output duration: {duration/speed:.1f}s | Width: {width}px")

    cmd_palette = [
        ffmpeg_bin, "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", src,
        "-vf", f"{vf_base},palettegen=max_colors=128:stats_mode=diff",
        palette_tmp
    ]
    subprocess.run(cmd_palette, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    cmd_gif = [
        ffmpeg_bin, "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", src,
        "-i", palette_tmp,
        "-lavfi", f"{vf_base} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3",
        dest
    ]
    subprocess.run(cmd_gif, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    
    if os.path.exists(palette_tmp):
        os.remove(palette_tmp)
        
    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"    [+] Created: {dest} ({size_mb:.2f} MB)")
    return size_mb

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Batch convert video clips to website-ready GIFs")
    parser.add_argument("--src", required=True, help="Path to input video")
    parser.add_argument("--dest", required=True, help="Path to output GIF")
    parser.add_argument("--start", default="00:00:00", help="Start timestamp (default: 00:00:00)")
    parser.add_argument("--duration", type=float, default=16.0, help="Source duration in seconds")
    parser.add_argument("--speed", type=float, default=2.0, help="Playback speed multiplier (default: 2.0)")
    parser.add_argument("--fps", type=int, default=12, help="GIF framerate (default: 12)")
    parser.add_argument("--width", type=int, default=260, help="Output width (default: 260)")
    
    args = parser.parse_args()
    convert_video_to_gif(args.src, args.dest, args.start, args.duration, args.speed, args.fps, args.width)
