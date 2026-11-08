# Make your own app and add it to the App Store

This guide shows how to build an app for the BMW MGU head unit (iDrive 7 and iDrive 8) and get it
into the on-car **MGU App Store**, the Bimmhacks way. There is a working example to copy:
[`idrive7-apps/apps/bimmhacks_hello`](../idrive7-apps/apps/bimmhacks_hello).

- [1. The quickest path — copy the starter app](#1-the-quickest-path--copy-the-starter-app)
- [2. How an app is put together](#2-how-an-app-is-put-together)
- [3. Build and preview on your PC](#3-build-and-preview-on-your-pc)
- [4. Install it on a car](#4-install-it-on-a-car)
- [5. Add it to the App Store (publish a source)](#5-add-it-to-the-app-store-publish-a-source)
- [6. Point the Bimmhacks installer at your apps](#6-point-the-bimmhacks-installer-at-your-apps)
- [7. Advanced concept: a media / screen-mirror app (and why it is hard)](#7-advanced-concept-a-media--screen-mirror-app-and-why-it-is-hard)

---

## 1. The quickest path — copy the starter app

```sh
cd idrive7-apps
cp -r apps/bimmhacks_hello apps/my_app
# edit apps/my_app/app.json: change "id" (give it your own prefix, e.g. acme_), "title", "version"
# edit apps/my_app/main.js: draw what you want
python kit/tools/build.py my_app          # -> dist/my_app@<version>.mgu
```

Then install it with the **Bimmhacks installer → Apps tab** (your app shows up in the list), or
publish it (section 5) so the on-car Store installs it.

> **Pick a unique id with a prefix** (`acme_gauges`, `bimmhacks_boost`). App ids are global on the
> head unit; a bare name BMW already uses can hide the tile.

---

## 2. How an app is put together

An app is a folder with a manifest and some JavaScript. The starter app has exactly two files:

**`app.json`** — the manifest. Everything the platform needs:

```json
{
  "id": "bimmhacks_hello",
  "title": "Bimmhacks Hello",
  "description": "One line shown in the app list.",
  "version": "1.0.0",
  "vendor": "bimmhacks",
  "loggingId": "OABH",
  "iconGlyph": "service",
  "screens": [
    { "name": "MAIN", "kind": "canvas", "title": "Bimmhacks Hello",
      "buttons": [ ["BTN_ACTION", "Count"], ["BTN_SETTINGS", "Settings"] ] }
  ],
  "settings": [
    { "key": "accent", "label": "Accent colour", "type": "choice", "default": "blue",
      "options": [ ["blue", "Bimmhacks Blue"], ["red", "M Red"], ["green", "Signal Green"] ] },
    { "key": "big", "label": "Large text", "type": "bool", "default": false }
  ]
}
```

- **`screens`** — `"kind": "canvas"` gives you a full pixel surface (component `MAIN_GFX`) to draw
  on. `buttons` wires the hardware / on-screen buttons `BTN_ACTION`, `BTN_PAGE`, `BTN_PREV`,
  `BTN_SETTINGS`.
- **`settings`** — the whole settings screen is generated from this. Types: `bool`, `number`
  (with `steps`/`unit`), `choice` (with `options`). Read them at runtime with
  `ctx.settings.get('accent')`.
- **`signals`** (optional) — live vehicle values. Declare the SOME/IP interface and fields you read,
  e.g. engine speed or coolant temperature; see any app in `idrive7-apps/apps/*/app.json` (gforce,
  warmup) for real signal names. Reading signals is how gauges get their data.
- **`iconGlyph`** draws the tile icon, or drop an `icon.png` (140×140) in the folder.

**`main.js`** — the logic. The shape never changes:

```js
const mgu = require('./kit/mgu');
const W = 1600, H = 520;

mgu.run({
  async start(ctx) {
    const { ui, settings, log } = ctx;
    let count = 0;
    const render = () => {
      const cv = ctx.canvas(W, H).clear('#0b0d12');
      cv.text('Hello', 56, 70, 76, '#ffffff', { font: 'bold' });
      cv.text(String(count), 60, 300, 128, '#0b63d6', { font: 'num' });
      return ui.image('MAIN_GFX', cv);           // push the frame to the screen
    };
    ui.onClick('BTN_ACTION', () => { count += 1; return render(); });
    ctx.loop('MAIN', 2, render);                 // redraw a couple of times a second
  },
});
```

The canvas has `clear`, `fillRect`, `roundRect`, `line`, `circle`, `arc` and
`text(str, x, y, size, colour, {font:'bold'|'num', align, spacing})`. The `kit/` runtime (mgu,
canvas, gauges, i18n, car) is vendored into the package at build time, so `require('./kit/mgu')`
just works. For live data, `require('./kit/car')` wraps the signals you declared. The richest
reference is the shipping apps in `idrive7-apps/apps/`.

> **No BMW SDK in your source.** Apps call `require('oap-sdk')`; the head unit supplies that from a
> stock BMW app already on it. Never commit or publish the SDK.

---

## 3. Build and preview on your PC

```sh
cd idrive7-apps
python kit/tools/build.py my_app                 # -> dist/my_app@<version>.mgu (+ .tgz)
python kit/tools/build.py --language ko my_app   # a fixed-language build
node kit/tools/preview.js dist/my_app out render BTN_ACTION render   # draw frames -> out/*.png
```

`build.py` needs the `oap-sdk` extracted from your own car once (the main
[README](../README.md) covers it). The preview runs the app in a headless browser and writes PNGs,
so you can see it without a car.

---

## 4. Install it on a car

- **Bimmhacks installer → Apps tab:** your app appears in the list with the built-in ones; tick it
  and **Install**. This is the fastest loop while developing.
- Command line: `python installer/bimmhacks_installer.py install --key <id_rsa> my_app`.

Everything the installer changes is backed up; **Restore to stock** removes it.

---

## 5. Add it to the App Store (publish a source)

The on-car Store installs from **sources** the owner adds: a GitHub repo, an HTTPS address, or a USB
stick. There are three layouts (full reference:
[mgu-store `docs/PUBLISHING.md`](https://github.com/robertnisipeanu/mgu-store/blob/main/docs/PUBLISHING.md)).
The simplest for a Bimmhacks app is **Releases**:

1. Build the package: `python kit/tools/build.py my_app` → `dist/my_app@1.0.0.mgu`.
2. On your GitHub repo, create a **Release** and attach `my_app@1.0.0.mgu` (name it exactly
   `<id>@<semver>.mgu` or `.tgz`). Remove any `node_modules/oap-sdk` from a `.tgz` first — the car
   generates the SDK itself.
3. Optional titles/icons: attach an `mgu-store.json`:
   ```json
   { "apps": { "my_app": { "title": "My App", "description": "…", "author": "Bimmhacks",
                           "icon": "my_app.png" } } }
   ```
4. On the car: **Apps → Store → Settings → Add source →** `your-name/your-repo`, pick **Releases**.
   Your app appears; open it and **Install**. New releases show as updates.

The other two layouts, in short:

- **Catalog** — a `catalog.json` listing each package with its size/sha256, in a release named
  `catalog` or at an HTTPS URL. Good for serving many apps or source packages the car builds.
- **Repo tree** — an `mgu-repo.json` at the repo root; the car builds every app from source. This is
  how `idrive7-apps` itself is a Store source.

Private repo? Add a GitHub fine-grained token (Contents: read-only) on the car (Settings → Add
token) or with `node tools/mgu-store/mgu-install.js token --identity <key>`.

---

## 6. Point the Bimmhacks installer at your apps

The installer's **Bimmhacks Apps** tab installs an extra source the same way. Set the source to your
repo (default: `waliworld/Bimmhacks`) and either:

- **online:** click *Make our apps available* — it makes sure the Store is on the car, then your
  source installs from the car screen; or
- **offline:** set a **Stage to** USB folder and the installer downloads your apps onto it, to install
  from **Apps → Store → USB**.

Command line:

```sh
python installer/bimmhacks_installer.py install-our-apps --key <id_rsa> --repo your-name/your-repo
python installer/bimmhacks_installer.py usb-repo --stage /path/to/usb --repo your-name/your-repo
```

---

## 7. Advanced concept: a media / screen-mirror app (and why it is hard)

People ask for two big ones: **play video (e.g. a YouTube client)** and **mirror an iPhone/Android
screen** on the centre display. These are **not** small canvas apps like the starter — they are
native projects. Here is an honest map of what they take, so you (or a native developer) can build
them properly rather than ship something that doesn't work.

**Read this first — safety and law.** Video on the centre display **while driving** is restricted in
most countries, and BMW/the platform gate it. Treat any media app as **parked-only by default**
(the way `idrive7-apps` gates Doom): pause when the car is moving. Don't build anything that defeats
that. And mirror/stream **your own** content only — **DRM-protected apps (Netflix, Disney+, YouTube
Premium offline, etc.) deliberately refuse to mirror or screen-capture, and that protection is not
something to work around.**

**The pieces the MGU gives you**

- **Drawing:** `mgu-render` (the Store's renderer) and the idrive7-apps GPU renderer draw app
  layers at up to 60 fps. A canvas app can show still frames; smooth video needs a native decoder.
- **Video decode on the unit exists:** IterioDev's `car-apps` ships `iteriodev-drplay` (an H.264
  player built on **ffmpeg / openh264**) for the Drive Recorder, which proves the unit can decode
  and show H.264 — the same building block a video or mirror app needs.
- **A screen pipe already exists:** IterioDev's `iteriodev-screencast` tool serves the centre
  display to a browser and turns clicks into touches. It shows how a native tool opens a port and
  moves frames in/out of the HMI.
- **Native companions install as *tools*,** not apps: a folder with a `component.json`, a systemd
  `.service`, and the binary, published as a tool kit. An app's JavaScript cannot start a daemon —
  the heavy lifting lives in the tool, the app is just its on-screen control panel.

**A YouTube-style client** would be: a native tool that fetches a stream (via YouTube's **official
Data API / IFrame player** and within YouTube's Terms of Service) and feeds H.264 frames to the
display through a drplay-like decoder, plus a small canvas app for search and playback controls.
The hard parts are the decoder/stream plumbing and staying inside YouTube's ToS — there is no
web-view on the MGU to just embed the site.

**An iPhone / Android mirror** ("make the phone think the car is a screen") means implementing a
**receiver** for a standard mirroring protocol on the head unit:

- **AirPlay (iPhone):** a native tool that speaks the AirPlay *receiver* role — advertise over
  Bonjour/mDNS, accept the connection, decode the H.264 the phone sends, and draw it via a
  drplay-style pipeline. Mature open-source receivers exist to port or integrate rather than write
  from scratch: **UxPlay** and **RPiPlay** (both GPL). The phone mirrors your own screen; protected
  video apps will show black by design.
- **Android:** the equivalent is a **Miracast / Wi-Fi-Direct** sink, or a USB path.

None of this is a weekend canvas app, and none of it is in this repository yet — it's a native build
(C/C++), packaged as an MGU **tool**, with a thin Bimmhacks control app on top, shipped parked-only.
If you want to pursue it, start from drplay (decode), screencast (frame transport) and UxPlay/RPiPlay
(the AirPlay receiver), package the result as a tool with a `component.json`, and publish it to your
Store source like any other component.

---

Questions or a new app idea? The starter in `idrive7-apps/apps/bimmhacks_hello` is the place to
begin, and the shipping apps next to it are the reference for everything else.
