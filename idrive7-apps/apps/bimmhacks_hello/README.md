# Bimmhacks Hello — starter app

The smallest useful MGU app, meant to be copied. It draws one canvas screen, counts taps of the
**Count** button, and reads two settings (accent colour, large text) declared in `app.json`.

- `app.json` — the manifest: id, title, version, the MAIN canvas screen, its buttons and the
  settings. The whole settings UI is generated from here.
- `main.js` — the logic: `mgu.run({ start })`, draw a frame with `ctx.canvas()`, push it with
  `ui.image('MAIN_GFX', cv)`, and handle `ui.onClick('BTN_ACTION', …)`.

## Build and install

```sh
# from the idrive7-apps folder, with the oap-sdk extracted from your car (see the main README)
python kit/tools/build.py bimmhacks_hello          # -> dist/bimmhacks_hello@1.0.0.mgu
node kit/tools/preview.js dist/bimmhacks_hello out render BTN_ACTION render   # PC preview -> out/*.png
```

Install it with the **Bimmhacks installer → Apps tab** (it appears in the list), or publish it to
your own GitHub release so the on-car **Store** can install it.

Full walkthrough, including how to add it to the App Store and how to start a new app from scratch:
**[docs/MAKE-YOUR-OWN-APP.md](../../../docs/MAKE-YOUR-OWN-APP.md)** (at the repository root).
