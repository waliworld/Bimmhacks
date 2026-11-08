# Bimmhacks: iDrive 7 App Installer

A one-click installer for the **MGU Custom Apps** (BMW iDrive 7 / MGU head unit) that a client can
run on their own PC. It does every step of the upstream guide automatically:

1. connects to the car over SSH (root key from the idrive7-8-ssh-root tool),
2. reads BMW's app SDK from **that car** and extracts it (it is BMW's code, so it is never shipped),
3. builds the chosen apps in the chosen language (English, Korean, Russian, Chinese),
4. copies the kit to the car, verifies it, runs the read-only check, installs (with a full backup),
5. deletes the SDK and the built apps from the PC again.

It also does **Status** and **Restore to stock**.

![Installer window](installer/screenshot.png)

## What's in this repository

| Path | What |
|---|---|
| `Install Bimmhacks Apps.cmd` | **Windows:** double-click. Checks/installs Python + Node.js (winget), opens the window |
| `Install Bimmhacks Apps.command` / `install.sh` | macOS / Linux launcher |
| `installer/bimmhacks_installer.py` | the installer (window + command line), Python standard library only |
| `installer/windows/bootstrap.ps1` | the Windows prerequisite check behind the `.cmd` |
| `installer/make_client_package.py` | packs everything into one zip for clients |
| `idrive7-apps/` | full copy of the upstream app sources, data, docs and tools (see [UPSTREAM.md](UPSTREAM.md)) |
| [CLIENT-GUIDE.md](CLIENT-GUIDE.md) | step-by-step guide for clients (goes into the zip) |

## Making the package for clients

```sh
python3 installer/make_client_package.py
# -> dist/Bimmhacks-iDrive7-Installer-1.0.0.zip  (no BMW SDK, no built apps)
```

GitHub Actions builds the same zip on every push (*Actions* → *Client installer package* → artifact).
Send the zip with [CLIENT-GUIDE.md](CLIENT-GUIDE.md); the client unzips it and double-clicks
`Install Bimmhacks Apps.cmd`.

## Requirements on the client PC

- Windows 10/11 (or macOS / Linux)
- Python 3.6+ with Tk, Node.js 14+ (the Windows launcher offers to install both)
- OpenSSH client (built into Windows 10/11, macOS, Linux)
- root SSH key for the head unit; the car parked, ignition on, charger connected

## Licence and attribution

The apps are by **cauliflowermossgrape**, MIT licence:
<https://github.com/cauliflowermossgrape/idrive7-apps>. Keep `idrive7-apps/LICENSE` and the credit
line the installer shows. The Doom app is GPL-2.0 with its source in `idrive7-apps/third_party/`.
The upstream author asks that nobody sells the apps as their own work: charge for your installation
service and support, credit the original, and tell clients the apps themselves are free.
