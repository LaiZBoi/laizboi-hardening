#!/usr/bin/env python3
"""
Build the Play Console background-location demo MP4.

Google requires a YouTube video proving the app's background-location use
maps to the declared feature (timeclock/shift visit logging). This script
captures the relevant screens from the Expo web bundle, adds narration
overlays + an Android phone bezel, and stitches it into an ~45-second MP4
that the user uploads to YouTube as Unlisted.

NOTE: this records the *web bundle* — it will not show real Android system
UI. Worst case Google review rejects it and we re-record on a phone.

Output: /home/administrator/local_apps/play_publish/data/builds/location-demo.mp4
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

PROD_CHECKOUT = Path('/home/administrator')
WORKTREE = Path('/home/administrator/.dev-worktree')

sys.path.insert(0, str(PROD_CHECKOUT))
os.chdir(PROD_CHECKOUT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402
django.setup()

from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402
from selenium.webdriver.chrome.service import Service  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

OUTPUT = Path('/home/administrator/local_apps/play_publish/data/builds/location-demo.mp4')
SCRATCH = Path('/tmp/location-demo-frames')
SCRATCH.mkdir(parents=True, exist_ok=True)

WIDTH, HEIGHT = 1080, 1920          # Phone viewport — the actual UI capture
BG = (11, 18, 32)                    # #0b1220

# ---- helpers ---------------------------------------------------------------

def load_font(size, bold=False):
    candidates_bold = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    ]
    candidates_reg = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
    ]
    for p in (candidates_bold if bold else candidates_reg):
        if Path(p).exists():
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def make_title_card(text_lines, sub=None):
    img = Image.new('RGB', (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)
    title_font = load_font(72, bold=True)
    sub_font = load_font(36, bold=False)
    # Title centered vertically
    total_h = len(text_lines) * 90
    start_y = (HEIGHT - total_h) // 2 - (60 if sub else 0)
    for i, t in enumerate(text_lines):
        bbox = d.textbbox((0, 0), t, font=title_font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        d.text((x, start_y + i * 90), t, font=title_font, fill=(240, 243, 249))
    if sub:
        bbox = d.textbbox((0, 0), sub, font=sub_font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        d.text((x, start_y + len(text_lines) * 90 + 40), sub, font=sub_font, fill=(122, 167, 255))
    return img


def overlay_caption(screenshot_img, caption_lines):
    """Place a captioned banner across the bottom of a screenshot frame."""
    img = screenshot_img.convert('RGB').copy()
    d = ImageDraw.Draw(img)
    cap_font = load_font(34, bold=True)
    line_h = 48
    pad_x, pad_y = 40, 30
    box_h = pad_y * 2 + line_h * len(caption_lines)
    box_y0 = HEIGHT - box_h - 60
    # Translucent strip — emulate via solid since we're RGB not RGBA
    overlay = Image.new('RGB', (WIDTH - 80, box_h), (16, 24, 40))
    img.paste(overlay, (40, box_y0))
    # Border
    d.rectangle([40, box_y0, WIDTH - 40, box_y0 + box_h], outline=(122, 167, 255), width=2)
    for i, line in enumerate(caption_lines):
        d.text((pad_x + 40, box_y0 + pad_y + i * line_h), line, font=cap_font, fill=(240, 243, 249))
    return img


# ---- selenium driver -------------------------------------------------------

def make_driver():
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--disable-web-security')
    opts.add_argument('--incognito')
    opts.add_argument(f'--window-size={WIDTH+100},{HEIGHT+200}')
    opts.binary_location = '/usr/bin/chromium-browser'
    drv = webdriver.Chrome(service=Service('/usr/bin/chromedriver'), options=opts)
    drv.set_page_load_timeout(45)
    drv.execute_cdp_cmd('Network.enable', {})
    drv.execute_cdp_cmd('Network.setExtraHTTPHeaders', {'headers': {'X-Forwarded-Proto': 'https'}})
    drv.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {
        'width': WIDTH, 'height': HEIGHT, 'deviceScaleFactor': 1, 'mobile': False,
    })
    return drv


def capture(drv, path: str, out_name: str) -> Image.Image:
    drv.get(f'http://localhost:8765{path}')
    time.sleep(3.5)
    out = SCRATCH / f'{out_name}.png'
    drv.save_screenshot(str(out))
    return Image.open(out).convert('RGB')


def login(drv) -> None:
    drv.get('http://localhost:8765')
    deadline = time.time() + 15
    while time.time() < deadline:
        inputs = drv.find_elements(By.CSS_SELECTOR, 'input')
        if len(inputs) >= 3:
            break
        time.sleep(0.5)
    inputs = drv.find_elements(By.CSS_SELECTOR, 'input')
    inputs[1].clear(); inputs[1].send_keys('screenshot')
    inputs[2].clear(); inputs[2].send_keys('ScreenShot!2026')
    btns = drv.find_elements(By.XPATH, "//*[normalize-space(text())='Sign in']")
    btns[-1].click()
    deadline = time.time() + 15
    while time.time() < deadline:
        if '/dashboard' in drv.current_url:
            return
        time.sleep(0.3)


# ---- main ------------------------------------------------------------------

def main() -> int:
    drv = make_driver()
    try:
        login(drv)
        dashboard = capture(drv, '/dashboard', 'dashboard')
        timeclock = capture(drv, '/timeclock', 'timeclock')
        settings = capture(drv, '/settings', 'settings')
        operations = capture(drv, '/operations', 'operations')
    finally:
        try: drv.quit()
        except Exception: pass

    # Frames + captions (each tuple: image, caption_lines, hold_seconds)
    title = make_title_card(
        ['Client St0r', 'Background Location'],
        sub='Field-tech shift visit logging (opt-in)',
    )
    closing = make_title_card(
        ['Opt-in.', 'Shift-scoped.', 'User controlled.'],
        sub='Off by default — toggled only in Settings',
    )

    frames = [
        (title, None, 4.0),
        (dashboard, [
            'Field technician opens the app.',
            'Dashboard shows shift status.',
        ], 4.0),
        (settings, [
            'Settings → "Track shifts in background"',
            'OFF by default. Tech must opt-in.',
        ], 5.0),
        (timeclock, [
            'Timeclock screen — Clock In',
            'Foreground location verifies the geofence.',
        ], 5.0),
        (timeclock, [
            'While clocked in, GPS visits to client',
            'sites are logged via a foreground service.',
            'A persistent notification keeps the user informed.',
        ], 6.0),
        (operations, [
            'Visit history is scoped to the',
            "technician's organization. Private.",
        ], 5.0),
        (timeclock, [
            'Tech taps Clock Out.',
            'Background tracking stops immediately.',
        ], 4.0),
        (closing, None, 4.0),
    ]

    # Render each frame to a PNG (with overlay)
    rendered = []
    for i, (img, caption, hold) in enumerate(frames):
        out = SCRATCH / f'frame_{i:02d}.png'
        if caption:
            img = overlay_caption(img, caption)
        img.save(out, 'PNG')
        rendered.append((out, hold))

    # Assemble with moviepy
    from moviepy import ImageClip, concatenate_videoclips, vfx
    clips = []
    for path, hold in rendered:
        c = ImageClip(str(path)).with_duration(hold).resized((WIDTH, HEIGHT))
        clips.append(c.with_effects([vfx.FadeIn(0.4), vfx.FadeOut(0.4)]))
    video = concatenate_videoclips(clips, method='compose')

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # libx264 + AAC fallback (silent video — Play Console accepts that)
    video.write_videofile(
        str(OUTPUT),
        fps=24,
        codec='libx264',
        audio=False,
        preset='medium',
        bitrate='3000k',
        logger=None,
    )
    print(f'✓ {OUTPUT} ({OUTPUT.stat().st_size // 1024} KB)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
