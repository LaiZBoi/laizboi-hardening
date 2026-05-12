# Mobile App Screenshots — Play Console assets

Captures of the Client St0r mobile app for Play Console listing
submission, at the three Play Console form factors:

| Folder        | Viewport     | Play Console slot                  |
|---------------|--------------|------------------------------------|
| `phone/`      | 1080 × 1920  | Phone screenshots                  |
| `tablet-7/`   | 1200 × 1920  | 7-inch tablet screenshots          |
| `tablet-10/`  | 1600 × 2560  | 10-inch tablet screenshots         |

## What's captured

Per folder: `login`, `dashboard`, `tickets`, `assets`, `vault`, `kb`,
`vehicles`, `inventory`, `organizations`, `settings`.

## How they were generated

`scripts/generate_mobile_screenshots.py` drives a headless Chromium
against the Expo web export of the mobile bundle:

1. `npx expo export --platform web` → static bundle to `/tmp/mobile-web-export/`
2. SPA-fallback HTTP server on :8765 serves the bundle (always returns
   `index.html` for unknown paths so expo-router history-mode routes
   resolve client-side).
3. Selenium:
   - Disables web security so axios can hit the API at a different origin
   - Injects `X-Forwarded-Proto: https` so Django doesn't redirect to HTTPS
   - Drives the real login form with credentials for the `screenshot`
     superuser, which has Membership rows on all 6 demo orgs
   - Navigates to each route and saves the viewport as PNG

## Why not from a real device?

Real-device capture is slower and harder to keep in sync with the
bundled code. The Expo web export shares the same JSX/TSX screens as
the native build, so what you see here is what the Android user sees —
modulo platform-specific native screens (camera scanner, image
picker, push-permission sheet) which fall back to web-side stubs.

## Refreshing

```sh
cd mobile && npm install
cd .. && /home/administrator/venv/bin/python scripts/generate_mobile_screenshots.py
```

The bundle re-export and the headless run take about 90 seconds combined.
