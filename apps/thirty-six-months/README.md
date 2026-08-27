# 36 Months — daily engine

A single-page planner that turns a 36-month roadmap (Sep 2026 → Aug 2029) into
today's schedule: study blocks fitted around classes and a four-hour commute, a
session timer, water, workouts, and the phase checklists that feed the blocks.

It runs three ways from the same source in `www/`:

| | |
|---|---|
| **Android app** | `android/` wraps `www/` in a WebView. Offline, real notifications, home-screen icon. |
| **Home-screen web app** | Serve `www/` over https and use *Add to Home screen*. |
| **A file** | Open `www/index.html` in any browser. |

## Getting the APK onto a phone

Every push that touches this folder builds one. Open the repository's
**Releases** page on the phone, tap the `.apk`, and allow the browser to
install apps when Android asks. Later builds install straight over earlier
ones and leave your data alone.

To build it yourself you need the Android SDK (platform 34, build-tools 34):

```
cd android
./gradlew assembleRelease
# app/build/outputs/apk/release/app-release.apk
```

## About `android/sideload.jks`

A signing key is committed here on purpose, and it is **not** a secret — the
password is `sideload` and it is in `app/build.gradle` in plain text.

Android identifies an app by its signature. A key generated fresh on each CI
run would make every build look like a *different* app, so installing an update
would fail unless you first uninstalled — taking the logged hours, ticks and
settings with it. A stable key is what makes an update an update.

It is good for sideloading and nothing else. It has never signed anything on
the Play Store and must not: publishing needs a key that is genuinely private.
If this app is ever distributed properly, generate a new key, keep it out of
the repository, and pass it through CI secrets.

## What the wrapper adds over the web page

- **Notifications that fire with the app closed.** `AlarmManager` +
  `setAndAllowWhileIdle`, re-armed after each fire and after a reboot. Being
  inexact by design avoids asking for the exact-alarm permission; a study
  reminder can drift a few minutes, but it must not go missing.
- **Assets over `https://appassets.androidplatform.net/`** rather than
  `file://`, via `WebViewAssetLoader`, so `localStorage` — which holds
  everything — sits on a normal web origin.
- **Screen stays on** while the study timer runs.
- **Back button** leaves the plan view before it leaves the app.
- **Backup to Downloads** as JSON.

## Data

Everything lives in `localStorage` under `roadmap-v4`, on the device only.
Nothing is uploaded and there is no account. Three years is a long time to
trust one phone, so *Today → Backup* writes a JSON file you should copy
somewhere else now and again; the same panel restores one.

The web page also runs inside the Claude artifact runtime, where
`window.storage` exists and is used instead. In a browser or the APK it does
not exist, so `localStorage` is the store — without that fallback every tick
and logged hour is discarded on reload.

## Layout

```
www/                     the app — everything below is one HTML file plus assets
  index.html
  fonts/                 Bricolage Grotesque, Public Sans, JetBrains Mono, local
  manifest.webmanifest   for Add to Home screen
android/
  app/src/main/java/…    MainActivity + the JS bridge, alarms, notifications
  app/src/main/res/      vector launcher and status-bar icons
  sideload.jks           see above
```

`android/app/build.gradle` points the asset source set at `../../www`, so the
web app is packaged from where it lives — there is no second copy to keep in
sync.
