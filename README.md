# Bimmhacks: iDrive 7 App Installer

A one-click installer for BMW **MGU head units (iDrive 7)** that a client runs on their own PC. It
takes a car from stock to installed apps in three tabs, and bundles the tools for each stage:

1. **Root access** — enable root SSH over an ENET cable with the bundled
   [idrive7-8-ssh-root](https://github.com/cauliflowermossgrape/idrive7-8-ssh-root) tool (one flash
   per car; it writes the car's SSH key the rest of the flow uses).
2. **Apps** — the [MGU custom apps](https://github.com/cauliflowermossgrape/idrive7-apps), built per
   car: it reads BMW's SDK off that car (never shipped — BMW's code), builds the chosen apps and
   language, installs them with a full backup, then deletes the SDK and build output.
3. **App Store** — install the on-car [mgu-store](https://github.com/robertnisipeanu/mgu-store) so the
   client browses, installs and updates apps from the car screen afterwards.

Plus **Status** and **Restore to stock**.

![Installer window](installer/screenshot.png)

## What's in this repository

| Path | What |
|---|---|
| `Install Bimmhacks Apps.cmd` | **Windows:** double-click. Installs Python + Node.js (winget) if needed, offers to elevate, opens the window |
| `Install Bimmhacks Apps.command` / `install.sh` | macOS / Linux launcher |
| `installer/bimmhacks_installer.py` | the installer (window + command line), Python standard library + `cryptography` for root access |
| `installer/windows/bootstrap.ps1` | the Windows prerequisite / elevation check behind the `.cmd` |
| `installer/make_client_package.py` | packs everything into one zip for clients |
| `idrive7-apps/` | the custom apps (upstream copy — see [UPSTREAM.md](UPSTREAM.md)) |
| `tools/idrive7-8-ssh-root/` | the root-access tool (public domain) |
| `tools/mgu-store/` | the on-car App Store installer (`mgu-install.js`, GPL-3.0) |
| [CLIENT-GUIDE.md](CLIENT-GUIDE.md) | step-by-step guide for clients (goes into the zip) |

## Making the package for clients

```sh
python3 installer/make_client_package.py
# -> dist/Bimmhacks-iDrive7-Installer-<version>.zip  (no BMW SDK, no built apps, no client keys)
```

GitHub Actions builds the same zip on every push (*Actions → Client installer package → artifact*),
and checks the zip carries no BMW SDK, built apps or client SSH keys. Send the zip with
[CLIENT-GUIDE.md](CLIENT-GUIDE.md); the client unzips it and double-clicks `Install Bimmhacks Apps.cmd`.

## Command line

```sh
python3 installer/bimmhacks_installer.py root-flash --vin <VIN>     # enable root SSH (ENET)
python3 installer/bimmhacks_installer.py install --key <id_rsa>     # build + install apps
python3 installer/bimmhacks_installer.py store-install --key <id_rsa>   # install the App Store
python3 installer/bimmhacks_installer.py status|restore --key <id_rsa>
python3 installer/bimmhacks_installer.py apps                       # list apps + languages
```

## Requirements on the client PC

- Windows 10/11 (or macOS / Linux)
- Python 3.6+ with Tk; Node.js 14+ (the Windows launcher offers to install both)
- OpenSSH client (built into Windows 10/11, macOS, Linux)
- `cryptography` Python package for root access (installed on demand)
- an **ENET cable** for root access; the car parked, ignition on, charger connected
- internet **at the car** for the App Store step

## Licence and attribution

- Apps: **cauliflowermossgrape**, MIT — <https://github.com/cauliflowermossgrape/idrive7-apps>
- Root tool: **idrive7-8-ssh-root**, public domain (Unlicense)
- App Store: **mgu-store** by Robert Nisipeanu and IterioDev, GPL-3.0 (`tools/mgu-store/LICENSE`)
- Doom app: GPL-2.0, source in `idrive7-apps/third_party/`

Keep each tool's licence and the credit the installer shows. The apps' author asks that nobody sell
the apps as their own work: charge for your installation service and support, credit the originals,
and tell clients the apps themselves are free. BMW's SDK is never redistributed.
