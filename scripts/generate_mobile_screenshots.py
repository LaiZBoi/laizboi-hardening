#!/usr/bin/env python3
"""
Mobile screenshot generator for Play Console listing.

Renders the Expo mobile app via its react-native-web bundle (already
exported to /tmp/mobile-web-export and served on :8765), drives it
through Selenium at phone + tablet viewport sizes, and saves PNGs into
docs/mobile-screenshots/<form-factor>/.

Auth:
  Hits /api/mobile/v1/auth/login/ with the 'screenshot' user (created
  separately) and gets a real Token. The token is injected straight
  into localStorage under both expo-secure-store web-fallback prefixes
  so getToken() resolves immediately without going through the form.

CORS:
  Bundle is at localhost:8765, API at localhost:8000 — different
  origins. We launch Chromium with --disable-web-security to let the
  axios calls through.

Headers:
  X-Forwarded-Proto: https is injected on every request via CDP so
  Django's SECURE_SSL_REDIRECT doesn't bounce us to https://.
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
import urllib.request
import json

PROD_CHECKOUT = Path('/home/administrator')
WORKTREE_ROOT = Path('/home/administrator/.dev-worktree')

sys.path.insert(0, str(PROD_CHECKOUT))
os.chdir(PROD_CHECKOUT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402
django.setup()

from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402
from selenium.webdriver.chrome.service import Service  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.common.keys import Keys  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402

BUNDLE_URL = 'http://localhost:8765'
API_URL = 'http://localhost:8000/api/mobile/v1'
LOGIN_USERNAME = 'screenshot'
LOGIN_PASSWORD = 'ScreenShot!2026'

CHROMIUM_BIN = '/usr/bin/chromium-browser'
CHROMEDRIVER_BIN = '/usr/bin/chromedriver'

# Play Console screenshot sizes. Each entry: name, width, height.
# All shaped to a 16:9 or 9:16 aspect ratio (Play Console rejects other ratios).
# Phone / 7-inch tablet / 10-inch tablet: 9:16 portrait.
# Chromebook / Android XR: 16:9 landscape (those targets normally run in
# landscape and the app supports auto-rotate as of v3.17.475).
FORM_FACTORS = [
    ('phone',      1080, 1920),  #  9:16 portrait, ≥1080 per side
    ('tablet-7',   1215, 2160),  #  9:16 portrait (135 × 9:16), mid-range
    ('tablet-10',  1620, 2880),  #  9:16 portrait, ≥1080 per side
    ('chromebook', 1920, 1080),  # 16:9 landscape, ≥1080 per side
    ('android-xr', 1920, 1080),  # 16:9 landscape, ≥720 per side
]

# Each screen entry: (slug, path) — path is hash-routed via expo-router
SCREENS = [
    ('login',          '/login'),
    ('dashboard',      '/dashboard'),
    ('tickets',        '/tickets'),
    ('assets',         '/assets'),
    ('vault',          '/vault'),
    ('kb',             '/kb'),
    ('vehicles',       '/vehicles'),
    ('inventory',      '/inventory'),
    ('organizations',  '/organizations'),
    ('settings',       '/settings'),
]

OUTPUT_BASE = WORKTREE_ROOT / 'docs' / 'mobile-screenshots'


def get_login_token() -> str:
    req = urllib.request.Request(
        f'{API_URL}/auth/login/',
        data=json.dumps({'username': LOGIN_USERNAME, 'password': LOGIN_PASSWORD}).encode(),
        headers={'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https'},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
    return body['token']


def make_driver(width: int, height: int) -> webdriver.Chrome:
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--disable-web-security')   # cross-origin to :8000
    opts.add_argument('--incognito')              # no persisted localStorage between runs
    opts.add_argument('--ignore-certificate-errors')
    # Outer window — chrome will shave off some pixels for window decoration.
    # We force the inner viewport via setDeviceMetricsOverride below so the
    # screenshot lands on an exact 9:16 / 16:9 size that Play Console accepts.
    opts.add_argument(f'--window-size={width + 100},{height + 200}')
    opts.binary_location = CHROMIUM_BIN
    service = Service(CHROMEDRIVER_BIN)
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(45)
    drv.execute_cdp_cmd('Network.enable', {})
    drv.execute_cdp_cmd(
        'Network.setExtraHTTPHeaders',
        {'headers': {'X-Forwarded-Proto': 'https'}},
    )
    # Pin the viewport to the EXACT target dimensions. Otherwise headless
    # Chromium shrinks the viewport for chrome and the resulting PNG's
    # aspect ratio is off-spec.
    drv.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {
        'width': width,
        'height': height,
        'deviceScaleFactor': 1,
        'mobile': False,
    })
    return drv


def do_login(drv) -> None:
    """Drive the actual sign-in form. The web bundle blocks navigation to
    protected screens until storage holds a real token, so we let the login
    flow set it for us."""
    drv.get(BUNDLE_URL)
    # Wait up to 15s for the login inputs to appear (RN bundle init is slow).
    deadline = time.time() + 15
    inputs = []
    while time.time() < deadline:
        inputs = drv.find_elements(By.CSS_SELECTOR, 'input')
        if len(inputs) >= 3:
            break
        time.sleep(0.5)
    if len(inputs) < 3:
        raise RuntimeError(f'expected ≥3 inputs on login screen, got {len(inputs)} (url={drv.current_url})')
    inputs[1].clear()
    inputs[1].send_keys(LOGIN_USERNAME)
    inputs[2].clear()
    inputs[2].send_keys(LOGIN_PASSWORD)
    # Two elements contain "Sign in" text: the heading subtitle and the
    # actual button. The button is rendered last. Click that one.
    btns = drv.find_elements(By.XPATH, "//*[normalize-space(text())='Sign in']")
    if not btns:
        btns = drv.find_elements(By.XPATH, "//*[contains(text(), 'Sign in')]")
    if not btns:
        raise RuntimeError('Sign-in button not found')
    btns[-1].click()
    # Wait for either /dashboard route or a tab bar to appear.
    deadline = time.time() + 15
    while time.time() < deadline:
        if '/dashboard' in drv.current_url or drv.find_elements(By.XPATH, "//*[contains(text(), 'Dashboard')]"):
            break
        time.sleep(0.5)
    print(f'  [auth] post-login url={drv.current_url}')


def capture(drv, name: str, path: str, out_dir: Path) -> tuple[str, bool, str]:
    """Navigate to a path (expo-router uses history-mode routing) and screenshot."""
    try:
        url = f'{BUNDLE_URL}{path}'
        drv.get(url)
        time.sleep(3.5)  # let React render + API calls resolve
        out = out_dir / f'{name}.png'
        ok = drv.save_screenshot(str(out))
        return (name, ok, str(out) if ok else 'save_screenshot returned false')
    except Exception as exc:
        return (name, False, f'{type(exc).__name__}: {exc}')


def run_form_factor(name: str, width: int, height: int) -> list:
    out_dir = OUTPUT_BASE / name
    out_dir.mkdir(parents=True, exist_ok=True)
    drv = make_driver(width, height)
    results = []
    try:
        # Capture the login screen first (before auth).
        time.sleep(0.5)
        drv.get(BUNDLE_URL)
        time.sleep(3)
        login_out = out_dir / 'login.png'
        drv.save_screenshot(str(login_out))
        results.append(('login', True, str(login_out)))
        print(f'  [OK] {name}/login: {login_out}')

        do_login(drv)
        for slug, path in SCREENS:
            if slug == 'login':
                continue
            r = capture(drv, slug, path, out_dir)
            results.append(r)
            mark = 'OK' if r[1] else 'FAIL'
            print(f'  [{mark}] {name}/{slug}: {r[2]}')
    finally:
        try:
            drv.quit()
        except Exception:
            pass
    return results


def main() -> int:
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    all_results = {}
    for factor, w, h in FORM_FACTORS:
        print(f'Capturing {factor} ({w}x{h}) …')
        all_results[factor] = run_form_factor(factor, w, h)

    print('\n=== Summary ===')
    for factor, rs in all_results.items():
        ok = sum(1 for _, b, _ in rs if b)
        print(f'  {factor}: {ok}/{len(rs)} captured')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise
