# Bimmhacks iDrive 7 Apps: Client Guide

Installs extra apps on BMWs with the **MGU head unit (iDrive 7)**: M Vitals, Redline, Service,
eDrive, Live Data, G-Force, Warm-Up, Virtual Dyno, Brake Test, Drag Timer, Fault Scanner, Doom
and more. Everything the installer changes is backed up first, and one button puts the car back to stock.

## Before you start

- **Root SSH access to the head unit.** You need the `id_rsa` key file from the
  *idrive7-8-ssh-root* tool (`data\enrollments\<VIN>\id_rsa`). If you don't have it, contact us.
- **The PC connected to the car** (the same connection the root tool uses).
- **Car parked, ignition on, a battery charger connected.** The screen reloads during the
  install and the head unit may reboot once.
- **Internet on the PC** the first time (to install Python/Node.js if missing, and for Doom's game data).

## Windows

1. Unzip `Bimmhacks-iDrive7-Installer-x.y.z.zip` (right-click → *Extract All*).
2. Double-click **`Install Bimmhacks Apps.cmd`**.
   - If Python or Node.js is missing, it offers to install them. Press **Y** and accept.
   - If Windows SmartScreen warns, click *More info* → *Run anyway*.
3. In the window:
   1. **Root SSH key:** click *Browse...* and pick your `id_rsa`. The IP fills in automatically
      when `connect.txt` is next to it (default `169.254.36.47`).
   2. Click **Test connection**. It must say *Connection OK*.
   3. Tick the apps you want and pick the language.
   4. Click **Install / Update apps** and wait for *Install finished*. Don't turn off the ignition.
4. On the iDrive: **Apps** → the new tiles (after 1-2 minutes, or after the reboot).

## macOS / Linux

Install Python 3 and Node.js (macOS: `brew install python node python-tk`; Ubuntu:
`sudo apt install python3 python3-tk nodejs openssh-client`), then double-click
**`Install Bimmhacks Apps.command`** (macOS) or run `./install.sh` (Linux).

Without a window (any OS):

```sh
python3 installer/bimmhacks_installer.py install --key /path/to/id_rsa --ip 169.254.36.47
python3 installer/bimmhacks_installer.py install --key id_rsa --language ko redline service
python3 installer/bimmhacks_installer.py status  --key id_rsa
python3 installer/bimmhacks_installer.py restore --key id_rsa
```

## Updating

Run the installer again with the new version and click **Install / Update apps**. A new backup is
made each time; the first (stock) backup is always kept.

## Back to stock

Click **Restore to stock**. It removes all custom apps and puts every changed file back exactly as it
was before the first install.

**After a BMW firmware update** the apps are hidden (the car runs stock). Run **Install / Update apps**
again to bring them back.

## Good to know

- **Engine Config** and **Transmission Config** are experimental and are not ticked by default.
  Engine Config can send control requests to the engine (DME) once a tune (BM3/MHD) is selected in its settings.
- Driving apps show a **DEMO** badge when there is no live data from the car.
- If an app crashes repeatedly, the car removes it by itself. Run the install again.

## Troubleshooting

| Message | What to do |
|---|---|
| *Could not log in to the head unit* | Ignition on? PC connected to the car? Correct IP? Run the root tool's *Enable SSH* again. |
| *Not a valid head unit address* | Type only the IP, e.g. `169.254.36.47`. |
| *MainApp ... a version the patch does not know* | This firmware isn't supported yet. Nothing was changed. Contact us. |
| *Node.js is not installed* | Install the LTS version from <https://nodejs.org/> and start again. |
| *OpenSSH client is missing* | Windows: Settings → Apps → Optional features → add *OpenSSH Client*. |

## Credits

The apps are **MGU Custom Apps** by cauliflowermossgrape, MIT licence, free original at
<https://github.com/cauliflowermossgrape/idrive7-apps>. Korean, Russian and Chinese translations
by their credited translators. Doom: doomgeneric (GPL-2.0) with Freedoom game data. BMW's app SDK is
never included: the installer reads it from your own car each time and deletes it from the PC afterwards.
