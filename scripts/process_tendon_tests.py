import os
from batch_video_to_gif import convert_video_to_gif

tasks = [
    {
        "src": r"C:\Users\luhua\OneDrive - MSFT\[4] University\2WA Resources\Robotic Hand Pictures\PXL_20260211_233305855.TS.mp4",
        "dest": r"images\robot-hand\assembly-tendon-single-finger.gif",
        "start": "00:00:00",
        "duration": 16.0, # 16s @ 2x -> 8.0s
        "speed": 2.0,
        "fps": 12,
        "width": 260
    },
    {
        "src": r"C:\Users\luhua\OneDrive - MSFT\[4] University\2WA Resources\Robotic Hand Pictures\PXL_20260212_034735772.TS.mp4",
        "dest": r"images\robot-hand\assembly-tendon-four-finger.gif",
        "start": "00:00:00",
        "duration": 16.0, # 16s @ 2x -> 8.0s
        "speed": 2.0,
        "fps": 12,
        "width": 260
    },
    {
        "src": r"C:\Users\luhua\OneDrive - MSFT\[4] University\2WA Resources\Robotic Hand Pictures\PXL_20260216_003056213.TS.mp4",
        "dest": r"images\robot-hand\assembly-tendon-thumb-opposition.gif",
        "start": "00:00:00",
        "duration": 16.0, # 16s @ 2x -> 8.0s
        "speed": 2.0,
        "fps": 12,
        "width": 260
    }
]

for t in tasks:
    convert_video_to_gif(
        src=t["src"],
        dest=t["dest"],
        start=t["start"],
        duration=t["duration"],
        speed=t["speed"],
        fps=t["fps"],
        width=t["width"]
    )

print("All tendon test GIFs successfully generated!")
