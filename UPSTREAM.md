# Upstream source

`idrive7-apps/` is a full copy of

- **Repository:** https://github.com/cauliflowermossgrape/idrive7-apps
- **Commit:** `8213223b039a3757e3559e08913140148a1f753b` (2026-11-08, "Merge PR #110: Redline 1.15.0 ...")
- **Licence:** MIT (`idrive7-apps/LICENSE`); Doom app GPL-2.0 (`idrive7-apps/apps/doom/LICENSE`,
  source in `idrive7-apps/third_party/doomgeneric`); fonts SIL OFL 1.1.

Everything was copied unchanged (apps, runtime, renderer, deploy scripts, docs, screenshots,
localizations, tests) except for one addition:

- `idrive7-apps/kit/tools/make_kit.py`: the environment variable `MGU_NO_DOCKER=1` skips the Docker
  build of the GPU renderer and ships the committed prebuilt one. The client installer sets it so a
  client PC that happens to have Docker doesn't start a 550 MB image build.

Not included (and never to be committed or shipped): BMW's `oap-sdk` and any built `.mgu`. They
contain BMW's copyrighted code; the installer extracts the SDK from each client's own car.

## Updating to a newer upstream version

```sh
git clone --depth 1 https://github.com/cauliflowermossgrape/idrive7-apps /tmp/upstream
rm -rf idrive7-apps && mkdir idrive7-apps
(cd /tmp/upstream && tar cf - --exclude=./.git .) | tar xf - -C idrive7-apps
# re-apply the MGU_NO_DOCKER line in idrive7-apps/kit/tools/make_kit.py (pick_renderer)
# update the commit above, bump VERSION in installer/bimmhacks_installer.py
python3 installer/make_client_package.py
```
