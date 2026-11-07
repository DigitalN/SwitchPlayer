"""Render Switch Player's icon from its SVG source into the app icon and the README's image.

    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 scripts/make_icon.py
    .../python3 scripts/make_icon.py <icon.svg> --png <out.png>    (render one PNG only)

WebKit draws the SVG (macOS has no other SVG renderer built in), so a window opens for a few
seconds. sips scales the 1024-pixel render into an iconset, and iconutil packs it into
`Application Files/SwitchPlayer.icns`, which SwitchPlayer.spec gives the app bundle.
"""

import argparse
import base64
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "Application Files" / "SwitchPlayer.svg"
ICNS = ROOT / "Application Files" / "SwitchPlayer.icns"
README_IMAGE = ROOT / "docs" / "images" / "icon.png"
ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]


def render(svg, png, size=1024):
    """Draw `svg` into a `size`-pixel PNG with a transparent background, through WebKit."""
    import webview
    from AppKit import NSBitmapImageFileTypePNG, NSBitmapImageRep, NSScreen

    scale = NSScreen.mainScreen().backingScaleFactor() or 1
    points = size / scale
    data = base64.b64encode(Path(svg).read_bytes()).decode("ascii")
    html = (f'<html><body style="margin:0;background:transparent;overflow:hidden">'
            f'<img src="data:image/svg+xml;base64,{data}" width="{points}" height="{points}" style="display:block">'
            f'</body></html>')
    done = threading.Event()
    errors = []

    def snapshot(window):
        from PyObjCTools import AppHelper
        from WebKit import WKSnapshotConfiguration
        from webview.platforms.cocoa import BrowserView
        import time
        time.sleep(1.5)
        shot = threading.Event()

        def take():
            web = BrowserView.instances[window.uid].webview

            def finished(image, error):
                if error is not None:
                    errors.append(str(error))
                else:
                    cg = image.CGImageForProposedRect_context_hints_(None, None, None)[0]
                    rep = NSBitmapImageRep.alloc().initWithCGImage_(cg)
                    rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(str(png), True)
                shot.set()

            web.takeSnapshotWithConfiguration_completionHandler_(WKSnapshotConfiguration.alloc().init(), finished)

        AppHelper.callAfter(take)
        shot.wait(20)
        done.set()
        window.destroy()

    window = webview.create_window("Switch Player icon", html=html, width=int(points), height=int(points),
                                   frameless=True, transparent=True, easy_drag=False)
    webview.start(snapshot, window)
    if errors or not Path(png).is_file():
        raise SystemExit(f"WebKit couldn't render {svg}: {errors or 'no image'}")
    subprocess.run(["/usr/bin/sips", "-z", str(size), str(size), str(png)], check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("svg", nargs="?", default=SOURCE)
    parser.add_argument("--png", help="render only this PNG, 1024 pixels")
    args = parser.parse_args()
    if args.png:
        render(args.svg, args.png)
        return
    with tempfile.TemporaryDirectory() as work:
        master = Path(work) / "icon.png"
        render(args.svg, master)
        iconset = Path(work) / "SwitchPlayer.iconset"
        iconset.mkdir()
        for points, factor in ICONSET:
            name = f"icon_{points}x{points}{'@2x' if factor == 2 else ''}.png"
            pixels = str(points * factor)
            subprocess.run(["/usr/bin/sips", "-z", pixels, pixels, str(master), "--out", str(iconset / name)],
                           check=True, capture_output=True)
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True)
        README_IMAGE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["/usr/bin/sips", "-z", "256", "256", str(master), "--out", str(README_IMAGE)],
                       check=True, capture_output=True)
    print(ICNS)
    print(README_IMAGE)


if __name__ == "__main__":
    sys.exit(main())
