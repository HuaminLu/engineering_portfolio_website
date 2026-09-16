import sys
from PIL import Image

def rotate(path, degrees):
    try:
        deg = int(degrees)
        with Image.open(path) as img:
            # Pillow rotate counter-clockwise by default; negative or expand
            # Rotate clockwise: angle = 360 - deg
            rotated = img.rotate(360 - (deg % 360), expand=True)
            rotated.save(path, quality=95)
        print(f"Successfully rotated {path} by {deg} degrees clockwise.")
    except Exception as e:
        print(f"Error rotating {path}: {e}")

if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python rotate_image.py <path_to_image> <degrees_clockwise>")
        print("Example: python rotate_image.py images/wind-tunnel/proto_14.jpg 90")
    else:
        rotate(sys.argv[1], sys.argv[2])
