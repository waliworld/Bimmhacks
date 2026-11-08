# Bimmhacks iDrive 7 Apps: Client Guide

Installs extra apps on BMWs with the **MGU head unit (iDrive 7)**: M Vitals, Redline, Service,
eDrive, Live Data, G-Force, Warm-Up, Virtual Dyno, Brake Test, Drag Timer, Fault Scanner, Doom
and more — and sets up an **on-car App Store** so you can add and update apps from the car screen
later. Everything the installer changes is backed up first, and one button puts the car back to stock.

The installer window has three tabs: **1. Root access → 2. Apps → 3. App Store.**

## Before you start

- **The PC connected to the car.** For root access this must be a **wired ENET cable** (a BMW
  ENET-to-Ethernet cable). For an already-rooted car you can also use the car's Wi-Fi hotspot.
- **Car parked, ignition on, a battery charger connected.** The screen reloads during installs and
  the head unit may reboot.
- **Windows: run as Administrator** for the root-access step (the launcher offers to relaunch
  elevated). This is needed so Windows uses the car's `169.254.x.x` ENET address.
- **Internet on the PC** the first time (Python/Node.js setup, Doom's game data). The **App Store**
  step also needs internet **at the car** (USB Ethernet adapter, LTE stick, tethered phone or Wi-Fi).

## Windows

1. Unzip `Bimmhacks-iDrive7-Installer-x.y.z.zip` (right-click → *Extract All*).
2. Double-click **`Install Bimmhacks Apps.cmd`**.
   - If Python or Node.js is missing, it offers to install them. Press **Y** and accept.
   - It offers to relaunch as Administrator — say **Y** for the root-access step.
   - If Windows SmartScreen warns, click *More info* → *Run anyway*.

### Tab 1 — Root access (once per car)

Only needed if the car is **not already rooted**. Already have root SSH and the `id_rsa` key? Skip
to Tab 2 and pick the key with *Browse*.

> ⚠️ **Flashing reprograms the head unit and wipes its coding and BMW Digital Key pairings.**
> Back up the coding first with BimmerCode or E-Sys, and plan to re-pair digital keys. This is a
> one-time step; do not repeat it.

1. Connect the ENET cable, wake the car.
2. Click **Detect VIN** (or type the 17-char VIN). Pick the head-unit variant (id7 for iDrive 7).
3. Click **Flash + Enable SSH (once)** and confirm. The unit reboots; this takes a few minutes.
4. When it finishes, the car's SSH key is filled into the connection fields automatically.
   - **Enable (repair)**: re-opens SSH on an already-flashed car whose SSH stopped — no flashing.
   - **Fix refusing key**: only if SSH opens but login is refused.
   - **Check state**: read-only; changes nothing.

### Tab 2 — Apps

1. Make sure the **Root SSH key** and **IP** are set (filled in by Tab 1, or *Browse* to the key).
2. Click **Test connection** — it must say *Connection OK*.
3. Tick the apps you want and pick the language, then click **Install / Update apps**.
4. On the iDrive: **Apps** → the new tiles (after 1-2 minutes, or after the reboot).
   - **Status** shows what is installed; **Restore to stock** removes everything.

### Tab 3 — App Store

Installs the on-car **MGU App Store**, so you browse, install and update apps from the car screen
afterwards (**Apps → Store**). The car needs internet for this (see above).

1. Make sure the car has internet, then click **Install the App Store**.
2. When it finishes, restart the head unit (car **Settings**, or power-cycle) to see the Store tile.

## macOS / Linux

Install Python 3 and Node.js (macOS: `brew install python node python-tk`; Ubuntu:
`sudo apt install python3 python3-tk nodejs openssh-client`), then double-click
**`Install Bimmhacks Apps.command`** (macOS) or run `./install.sh` (Linux).

Without a window (any OS):

```sh
# root access (ENET cable; once per car)
python3 installer/bimmhacks_installer.py root-status                 # read-only check
python3 installer/bimmhacks_installer.py root-flash --vin <17-CHAR-VIN>   # flash + enable SSH
python3 installer/bimmhacks_installer.py root-enable                 # repair, no flashing

# apps
python3 installer/bimmhacks_installer.py install --key <id_rsa> --ip 169.254.36.47
python3 installer/bimmhacks_installer.py install --key <id_rsa> --language ko redline service
python3 installer/bimmhacks_installer.py status  --key <id_rsa>
python3 installer/bimmhacks_installer.py restore --key <id_rsa>

# app store
python3 installer/bimmhacks_installer.py store-install --key <id_rsa>
```

The root key is written to `tools/idrive7-8-ssh-root/data/enrollments/<VIN>/id_rsa`.

## Updating

Run the installer again with the new version and click **Install / Update apps**, or use the on-car
App Store. A new backup is made each time; the first (stock) backup is always kept.

**After a BMW firmware update** the apps are hidden (the car runs stock). Run **Install / Update
apps** again to bring them back.

## Good to know

- **Engine Config** and **Transmission Config** are experimental and off by default. Engine Config
  can send control requests to the engine (DME) once a tune is selected in its settings.
- Driving apps show a **DEMO** badge when there is no live data from the car.
- If an app crashes repeatedly, the car removes it by itself. Run the install again.

## Troubleshooting

| Message | What to do |
|---|---|
| Root access can't reach the car | Use the **ENET cable** (not Wi-Fi), ignition on. Windows: run as Administrator. |
| *A valid 17-character VIN is needed* | Click **Detect VIN**, or type the VIN from the windshield. |
| *MainApp … a version the patch does not know* | This firmware isn't supported yet. Nothing was changed. Contact us. |
| *Could not log in to the head unit* | Do Tab 1 first, or *Browse* to the right `id_rsa`. Check the IP and that the car is awake. |
| App Store step fails to download | The **car** needs internet (USB Ethernet / LTE / tethered phone / Wi-Fi). |
| *'cryptography' could not be installed* | Run `python -m pip install --user cryptography`, then retry. |
| *Node.js / OpenSSH missing* | Install Node LTS from nodejs.org; Windows OpenSSH: Settings → Apps → Optional features. |

## Credits

The apps are **MGU Custom Apps** by cauliflowermossgrape, MIT licence, free original at
<https://github.com/cauliflowermossgrape/idrive7-apps>. Root access uses
[idrive7-8-ssh-root](https://github.com/cauliflowermossgrape/idrive7-8-ssh-root) (public domain).
The App Store is [mgu-store](https://github.com/robertnisipeanu/mgu-store) by Robert Nisipeanu and
IterioDev (GPL-3.0). Doom: doomgeneric (GPL-2.0) with Freedoom game data. BMW's app SDK is never
included: the installer reads it from your own car and deletes it from the PC afterwards.
