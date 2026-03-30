# ClientSt0r Android

A production-ready Android wrapper for **https://clientst0r.app** built with:

- **Trusted Web Activity (TWA)** — loads the web app in a full-screen Chrome session with no browser UI
- **Biometric gate** — fingerprint / face / PIN authentication required before the app loads
- **Android App Links** — `https://clientst0r.app/**` URLs open directly in the app
- **Firebase Cloud Messaging** — push notifications from the server
- **Material 3** dark theme matching the web app palette
- **Kotlin + Jetpack Compose** (biometric UI only — the rest is the web app)

---

## Project Structure

```
clientst0r-android/
├── app/
│   ├── src/
│   │   ├── main/
│   │   │   ├── java/app/clientst0r/
│   │   │   │   ├── ClientSt0rApp.kt          # Application — Firebase + notification channels
│   │   │   │   ├── MainActivity.kt           # Entry point — splash + biometric gate
│   │   │   │   ├── TwaActivity.kt            # TWA — loads https://clientst0r.app
│   │   │   │   ├── BiometricHelper.kt        # BiometricPrompt wrapper
│   │   │   │   ├── MyFirebaseMessagingService.kt
│   │   │   │   ├── NotificationHelper.kt
│   │   │   │   └── ui/
│   │   │   │       ├── BiometricScreen.kt    # Compose UI for auth gate
│   │   │   │       └── theme/               # Material 3 colours, typography, theme
│   │   │   ├── res/
│   │   │   │   ├── values/                  # strings, colors, themes
│   │   │   │   ├── drawable/                # vector icons + splash image
│   │   │   │   ├── mipmap-anydpi-v26/       # adaptive launcher icons
│   │   │   │   └── xml/                     # network security, backup rules
│   │   │   └── AndroidManifest.xml
│   │   └── debug/res/values/strings.xml     # "ClientSt0r (debug)" label
│   ├── build.gradle.kts
│   ├── google-services.json                 # ← replace with real file (see below)
│   └── proguard-rules.pro
├── gradle/wrapper/gradle-wrapper.properties
├── build.gradle.kts
├── settings.gradle.kts
├── gradle.properties
├── gradlew / gradlew.bat
├── keystore.properties.template             # ← copy to keystore.properties (see below)
└── .github/workflows/build.yml             # CI: debug on every push, AAB on tag
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Android Studio | Hedgehog 2023.1.1 or newer |
| JDK | 17 (bundled with Android Studio) |
| Android SDK | API 34 (Platform + Build Tools) |
| Chrome / Chromium | 72+ installed on test device (required for TWA) |

> **First clone — wrapper JAR bootstrap**
> The `gradle-wrapper.jar` is not included in VCS.  Open the project in
> Android Studio once — it detects the missing JAR and downloads it
> automatically.  Alternatively, if you have Gradle 8.6 installed locally,
> run `gradle wrapper --gradle-version 8.6` in the project root.
> The CI workflow uses `gradle/actions/setup-gradle` which handles this
> automatically.

---

## Setup

### 1. Firebase

1. Go to [Firebase Console](https://console.firebase.google.com) → your project (or create one).
2. Add an Android app with package name `app.clientst0r`.
3. Download **google-services.json** and replace `app/google-services.json`.
4. Enable **Cloud Messaging** in the console.

> The placeholder `app/google-services.json` contains only stub values and will cause a build error at runtime if not replaced.

### 2. Release Signing

```bash
# Generate a release keystore (one-time)
keytool -genkeypair -v \
  -keystore release.jks \
  -alias clientst0r \
  -keyalg RSA -keysize 2048 \
  -validity 10000

# Copy the template and fill in your values
cp keystore.properties.template keystore.properties
```

Edit `keystore.properties`:

```properties
storeFile=../release.jks
storePassword=your_store_password
keyAlias=clientst0r
keyPassword=your_key_password
```

> `keystore.properties` and `release.jks` are in `.gitignore` — **never commit them**.

### 3. Launcher Icons (PNG fallbacks for API 24–25)

The adaptive icon vector drawables cover API 26+. For API 24–25 you need PNG
mipmap files. Generate them in Android Studio:

1. Right-click `app/src/main/res` → **New → Image Asset**
2. Icon Type: **Launcher Icons (Adaptive and Legacy)**
3. Foreground: use `res/drawable/ic_launcher_foreground.xml`
4. Background: use `res/drawable/ic_launcher_background.xml`
5. Click **Next → Finish** — Studio writes PNGs into all mipmap-*dpi folders.

### 4. Digital Asset Links (App Links + TWA verification)

For Android App Links and TWA domain verification to work, you must host an
`assetlinks.json` file at `https://clientst0r.app/.well-known/assetlinks.json`.

1. Get your release certificate SHA-256 fingerprint:
   ```bash
   keytool -list -v -keystore release.jks -alias clientst0r | grep SHA256
   ```
2. Update `res/values/strings.xml` → `asset_statements` with your fingerprint.
3. Create/update `https://clientst0r.app/.well-known/assetlinks.json`:
   ```json
   [{
     "relation": ["delegate_permission/common.handle_all_urls"],
     "target": {
       "namespace": "android_app",
       "package_name": "app.clientst0r",
       "sha256_cert_fingerprints": ["AA:BB:CC:...your fingerprint..."]
     }
   }]
   ```
4. Verify: `adb shell pm get-app-links app.clientst0r`

---

## Building

### Debug APK (no signing required)

```bash
./gradlew assembleDebug
# Output: app/build/outputs/apk/debug/app-debug.apk
```

Install directly:
```bash
adb install app/build/outputs/apk/debug/app-debug.apk
```

### Release AAB (Play Store)

Requires `keystore.properties` and `release.jks` to be in place.

```bash
./gradlew bundleRelease
# Output: app/build/outputs/bundle/release/app-release.aab
```

### Release APK (sideload)

```bash
./gradlew assembleRelease
# Output: app/build/outputs/apk/release/app-release.apk
```

---

## CI / CD (GitHub Actions)

The workflow at `.github/workflows/build.yml` runs automatically:

| Event | Action |
|-------|--------|
| Push to `main` / PR | Debug APK built and uploaded as artifact |
| Push tag `v*` | Release AAB built and uploaded as artifact |

### Required GitHub Secrets

| Secret | Value |
|--------|-------|
| `GOOGLE_SERVICES_JSON` | Full contents of your `google-services.json` |
| `KEYSTORE_BASE64` | `base64 -w0 release.jks` output |
| `KEYSTORE_PROPERTIES` | Full contents of your `keystore.properties` |

---

## Push Notifications

When the app first launches (or the FCM token rotates), `MyFirebaseMessagingService.onNewToken()` is called. Implement the TODO to upload it to the server:

```
POST https://clientst0r.app/api/fcm-tokens/
Content-Type: application/json

{ "token": "<fcm_token>" }
```

To send a notification from the server, call the FCM HTTP v1 API with the stored token.

Notification payload data keys:

| Key | Values | Effect |
|-----|--------|--------|
| `alert` | `"true"` | Uses high-priority channel (heads-up notification) |
| `url` | Any URL on `clientst0r.app` | Tapping the notification opens that URL in the TWA |

---

## Architecture

```
User launches app
       │
       ▼
 MainActivity  ← App Link / notification tap also lands here
  (splash screen)
       │
       ▼
 BiometricScreen (Compose)
  ┌─ BiometricHelper.showPrompt()
  │      │ success
  │      ▼
  └─ launchTwa(url?)
             │
             ▼
       TwaActivity  extends LauncherActivity
        └─ loads https://clientst0r.app  (Chrome Custom Tab / TWA)
```

If Chrome is not installed the TWA falls back to a WebView
(`FALLBACK_STRATEGY=webview` in AndroidManifest).
