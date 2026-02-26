# ClientSt0r Browser Extension

A Chrome/Edge/Firefox extension that brings ClientSt0r's password vault and asset inventory directly into your browser.

## Features

- **Password Autofill** — detects login forms and offers to fill credentials from your vault
- **Password Search** — search, copy username/password/TOTP from the popup
- **Asset Search** — search assets, view IP/hostname, toggle "Needs Reorder" flag
- **Drag-to-Reorder** — personal ordering saved locally (no server changes)
- **Alerts** — badge count of breached/expired passwords, refreshed every 6 hours

## Installation

### Chrome / Edge (Developer Mode)

1. Open `chrome://extensions` (or `edge://extensions`)
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked**
4. Select the `clientst0r-extension/` directory
5. The ClientSt0r icon appears in the toolbar

### Firefox

1. Open `about:debugging#/runtime/this-firefox`
2. Click **Load Temporary Add-on…**
3. Select `clientst0r-extension/manifest.json`

> For permanent Firefox installation, the extension would need to be signed via AMO.

## Setup

1. Click the extension icon → click **⚙ Settings** (or right-click → Options)
2. Enter your **Server URL** (e.g. `https://clientst0r.app` or your self-hosted address)
3. Enter your **API Key** — generate one from **Settings → API Keys** in ClientSt0r
4. Click **Test Connection** to verify, then **Save Settings**

## Usage

### Autofill
- Visit any login page — a suggestion bar appears when you click into a username or password field
- Select a credential to auto-fill both fields

### Popup
- **Passwords tab** — pre-searched for the current site's hostname; copy username, password, or TOTP code
- **Assets tab** — search assets; toggle the "Needs Reorder" flag (syncs to the web app instantly)
- **Alerts tab** — lists breached, expired, and soon-to-expire passwords

### Drag to Reorder
Both lists support drag-and-drop ordering. Your preferred order is stored locally in `chrome.storage.sync` and persists across sessions.

## Security Notes

- API key is stored in `chrome.storage.local` (browser-encrypted, inaccessible to page JavaScript)
- Revealed passwords are held in memory only for the fill/copy operation — never persisted to disk
- The content script never has access to the API key; all secrets flow through the background service worker
- HTTPS is enforced for non-localhost server URLs

## Architecture

```
content.js  ──sendMessage──→  service-worker.js  ──fetch──→  ClientSt0r API
popup.js    ──sendMessage──→  service-worker.js
options.js  ──sendMessage──→  service-worker.js
```

All network requests originate from the service worker which has `host_permissions: ["<all_urls>"]`, bypassing CORS without any server-side changes.

## Compatibility

| Browser | Manifest V3 | Status |
|---------|-------------|--------|
| Chrome 88+ | ✅ | Supported |
| Edge 88+ | ✅ | Supported |
| Firefox 109+ | ✅ | Supported (via `browser_specific_settings`) |
