# Bimmhacks MGU: Client Guide (iDrive 7 & 8)

Three things, in order, on BMWs with the **MGU head unit (iDrive 7 & 8)**:

1. **Root access** over an ENET cable (one flash per car), with a **coding backup** taken first.
2. **Coding restore (experimental)** — put the captured coding back after a flash, if you need it.
3. An **on-car App Store** — once it is installed, you browse, install and update every app from the
   car's own screen (RideTube, cluster/HUD maps, IterioDev's car-apps, and your own repo).

Apps are **not** built on the PC any more: they come from the on-car Store. The installer window has
three tabs — **Root access → App Store → Credits.** The **Car** panel at the top (VIN, head unit, root
SSH key, IP address) is shared, and the **Activity** log at the bottom shows what every button did.

> Flashing app and installer developed by **Mr. Wali**. Root tool and Store are open source — see the
> **Credits** tab.

## Before you start

- **The PC connected to the car.** For root access this must be a **wired ENET cable** (a BMW
  ENET-to-Ethernet cable). For an already-rooted car you can also use the car's Wi-Fi hotspot.
- **Car parked, ignition on, a battery charger connected.** The screen reloads during installs and
  the head unit may reboot.
- **Windows: run as Administrator** for the root-access step (the launcher offers to relaunch
  elevated). This is needed so Windows uses the car's `169.254.x.x` ENET address.
- **Internet on the PC** the first time (Python/Node.js setup). The **App Store** step also needs
  internet **at the car** (USB Ethernet adapter, LTE stick, tethered phone or Wi-Fi).

## Windows

1. Unzip `Bimmhacks-MGU-Installer-x.y.z.zip` (right-click → *Extract All*).
2. Double-click **`Install Bimmhacks Apps.cmd`**.
   - If Python or Node.js is missing, it offers to install them. Press **Y** and accept.
   - It offers to relaunch as Administrator — say **Y** for the root-access step.
   - If Windows SmartScreen warns, click *More info* → *Run anyway*.

## Root access (once per car)

Only needed if the car is **not already rooted**. Already have root SSH and the `id_rsa` key? Pick it
with *Browse* (the IP address is filled in from the key's `connect.txt`).

> ⚠️ **Flashing reprograms the head unit and wipes its coding and BMW Digital Key pairings.**
> Back up the coding first, and plan to re-pair digital keys. This is a one-time step; do not repeat it.

**Back up the coding first.** Click **Back up coding**. In one pass over ENET it saves, under
`coding-backups/`:

- a **read-only coding backup** — the unit's coding store and identification, read twice and verified,
  written as `coding_MGU.txt` with a `RESTORE.txt` that tells you how to put the coding back after a
  flash (re-code HU_MGU **by vehicle order / "code by FA"** in E-Sys or BimmerCode — the reliable way);
- which **CAFD coding versions**, VIN, IP and faults are on the car; and
- where the unit hands it over, the **coding data (NCD)** as E-Sys-compatible `.ncd` files, for the
  experimental restore. (Some units do not expose the NCD; the backup still succeeds.)

Having BimmerCode / E-Sys / Autel as a fallback is strongly advised.

Then:

1. Connect the ENET cable, wake the car.
2. Click **Detect VIN** (or type the 17-char VIN). Pick the **Head unit** (id7 for iDrive 7).
3. Click **Flash + enable SSH**. **It will not flash without a coding backup:** a dialog asks you to
   either **back it up now** (recommended — it runs the backup above, then flashes) or, if you choose to
   skip, to **confirm a warning** that you already have a backup from BimmerCode / E-Sys / Autel.
4. The unit reboots; this takes a few minutes.
5. When it finishes, the car's SSH key **and** its IP address are filled into the Car panel
   automatically (the root tool reads the head unit's address from the car itself).
   - **Enable (repair)**: re-opens SSH on an already-flashed car whose SSH stopped — no flashing.
   - **Fix refusing key**: only if SSH opens but login is refused (re-flashes, so it forces a backup too).
   - **Fix blue screen**: recovery — if a unit is stuck on the blue programming screen, this leaves
     programming mode and reboots it to normal. No flash, no write.
   - **Check state**: read-only; changes nothing.
   - **Disable**: closes root SSH again.

### Coding restore (experimental) — the right-hand card

A flash wipes the head unit's coding. If you took a backup above that captured the NCD, this card can
write it back:

- **Verify coding** — read-only. Reads the car and tells you whether each coding block still matches the
  backup. Writes nothing.
- **Dry run** — shows exactly what a restore *would* write, block by block. Writes nothing.
- **Restore coding (experimental)** — writes the captured coding back and reads every block back to
  verify. It is safe-by-construction: it only writes the exact coding it read, only to the identifiers
  it read, refuses unless the car's CAFD versions and VIN match the backup, and asks for a typed
  confirmation.

> **Experimental.** A wrong coding write can leave the HMI partly-coded. Keep **BimmerCode / E-Sys /
> Autel** ready — the saved `.ncd` files load straight into E-Sys — and prefer them if you are unsure.
> Pick the backup with **Browse** (the latest is filled in for you).

## App Store

Installs the on-car **MGU App Store**, so you browse, install and update apps from the car screen
afterwards (**Apps → Store**). The car needs internet for this (see above). Two options sit above the
button, both ticked by default:

- **Renderer** — the graphics part. It draws the Store and other apps with the head unit's graphics
  chip, so they are smooth and get the on-screen keyboard. Without it the Store falls back to simpler
  screens. Leave it ticked.
- **Internet uplink** — gives the car its own internet through a USB Ethernet adapter, an LTE stick,
  phone tethering or Wi-Fi, so the Store can download apps. Untick it only if you will install from a
  USB stick and never need the car online.

1. **Test connection** — it must say *Connection OK*.
2. Click **Install the App Store** and confirm. The installer installs the Store, **restarts the head
   unit** and waits for it to come back (about two minutes — the Store only starts after a restart).
   Then open **Apps → Store** on the car.

Unticked nothing restarts? Click **Restart head unit** next to the install button when you are ready.
It asks the car to restart exactly as the Store's own *Restart head unit* does, and refuses while a
Store install is still running.

### Add apps to the Store

The right-hand card adds sources to the on-car Store:

- **idrive7-apps**, the **cluster / HUD maps** (idrive-apps) and **IterioDev's car-apps** are **built
  in** — they appear in the car's Store as soon as it is installed. Nothing to click.
- **Add RideTube (YouTube, unofficial)** — adds the RideTube source to the Store, so it is a normal
  download under **Apps → Store** on the car. RideTube is an unofficial YouTube client for the MGU
  (GPL-3, by JustAnotherPU): it plays over the car's internet, shows the picture **only while parked**,
  and drops to a sound-only view while driving. Not affiliated with YouTube/Google. iDrive 7.
- **Add your repo to the Store** — type a GitHub `owner/repo` and add it as a source. **How to publish
  an app** opens the full guide (`docs/MAKE-YOUR-OWN-APP.md`).

If the Store has not started yet, the head unit is restarted once for you (about two minutes) and the
installer waits for the Store's updater before it adds a source.

**More app repos:** `docs/Store-Sources.pdf` (in this zip) lists the recommended app repos that do
**not** come with the Store, grouped by **iDrive 7 & 8 / iDrive 7 only**. For each one it gives the
exact text to type on the car (**Apps → Store → Settings → Add source**), what to follow (Releases or a
branch), and the repo's link so you can read about it on your PC.

> **First time on a car?** A Store that was installed a minute ago only starts its updater with the
> head unit. If the installer says *the Store's updater is not running yet*, restart the head unit
> (car **Settings**, or power-cycle), wait a minute, and click the button again.

## Phone instead of a laptop: Bimmhacks Pocket

Bimmhacks Pocket is a **separate package** — a small box (a Raspberry Pi) with an ENET cable. With one
you need no laptop at all:

1. Plug the cable into the car's OBD port, power the box from the car's USB port, ignition on.
2. On your phone, join the Wi-Fi named on the box (e.g. **Bimmhacks-Pocket**) and open the address
   shown on it (Safari: *Share → Add to Home Screen* keeps it as an icon).
3. The page has the same steps: **Root access** (with the same backup confirmation before a flash), the
   **coding backup / restore**, and the **App Store**. The activity log shows what the box is doing; it
   keeps working if the phone's screen goes off.
4. When root access finishes, press **Download key bundle** to keep a copy of the car's SSH key on the
   phone; coding backups can be downloaded from their list.

Building and setting up such a box from a Raspberry Pi is described in `docs/POCKET.md` (it ships in the
`Bimmhacks-Pocket-x.y.z.zip` package).

## macOS / Linux

Install Python 3 and Node.js (macOS: `brew install python node python-tk`; Ubuntu:
`sudo apt install python3 python3-tk nodejs openssh-client`), then double-click
**`Install Bimmhacks Apps.command`** (macOS) or run `./install.sh` (Linux).

Without a window (any OS):

```sh
# root access (ENET cable; once per car)
python3 installer/bimmhacks_installer.py root-status                 # read-only check
python3 installer/bimmhacks_installer.py backup-coding --out coding-backups     # snapshot + NCD backup
python3 installer/bimmhacks_installer.py root-flash --vin <17-CHAR-VIN> --confirm-backup   # flash + enable SSH
python3 installer/bimmhacks_installer.py root-enable                 # repair, no flashing

# coding restore (experimental)
python3 installer/bimmhacks_installer.py verify-coding  --backup coding-backups/<backup>.json   # read-only
python3 installer/bimmhacks_installer.py restore-coding --backup coding-backups/<backup>.json --dry-run
python3 installer/bimmhacks_installer.py restore-coding --backup coding-backups/<backup>.json --confirm-restore

# app store + sources
python3 installer/bimmhacks_installer.py store-install --key <id_rsa> --restart     # install the Store, restart the head unit
python3 installer/bimmhacks_installer.py restart-head-unit --key <id_rsa>           # the Store only starts after a restart
python3 installer/bimmhacks_installer.py install-ridetube --key <id_rsa>            # add RideTube (unofficial YouTube)
python3 installer/bimmhacks_installer.py install-idrive-apps --key <id_rsa>         # add cluster/HUD maps
python3 installer/bimmhacks_installer.py install-car-apps --key <id_rsa>            # add IterioDev car-apps (iDrive 8)
python3 installer/bimmhacks_installer.py install-our-apps --key <id_rsa> --repo owner/repo
python3 installer/bimmhacks_installer.py usb-repo --stage /path/to/usb --repo robertnisipeanu/idrive-apps

# which identifiers a logger read from your car (from a Wireshark capture of the ENET link)
python3 installer/pid_capture.py drive.pcapng          # -> drive.pids.json + drive.custom_pids.json
```

The root key is written to `tools/idrive7-8-ssh-root/data/enrollments/<VIN>/id_rsa`.

## Updating

Apps update **from the on-car Store** — open **Apps → Store** on the car. To add new sources or
re-install the Store, run the installer again. Coding backups are kept in `coding-backups/`.

## Troubleshooting

| Message | What to do |
|---|---|
| Root access can't reach the car | Use the **ENET cable** (not Wi-Fi), ignition on. Windows: run as Administrator. |
| *A valid 17-character VIN is needed* | Click **Detect VIN**, or type the VIN from the windshield. |
| *MainApp … a version the patch does not know* | This firmware isn't supported yet. Nothing was changed. Contact us. |
| *Could not log in to the head unit* | Do **Root access** first, or *Browse* to the right `id_rsa`. Check the IP and that the car is awake. |
| Coding restore says *do not match* / *refusing* | The car's CAFD versions or VIN differ from the backup — it will not write. Make sure it is the same car/unit, or restore with E-Sys/Autel from the saved `.ncd` files. |
| App Store step fails to download | The **car** needs internet (USB Ethernet / LTE / tethered phone / Wi-Fi). |
| *'cryptography' could not be installed* | Run `python -m pip install --user cryptography`, then retry. |
| *Node.js / OpenSSH missing* | Install Node LTS from nodejs.org; Windows OpenSSH: Settings → Apps → Optional features. |
| The Store opens on the car but asks you to restart the MGU, and cannot | A fresh Store only starts after a restart, and its own restart button needs it running. In the installer open **App Store** and click **Restart head unit** (about two minutes). Still no luck: switch the ignition off and on, or hold the volume knob for about 30 seconds until the screens restart. |
| *An install is still running on the head unit* | The restart was refused on purpose so it cannot cut an install short. Wait a minute or two and try again. |
| *The head unit did not answer on SSH again* | Give it a few more minutes. If it is up on the car but SSH stays closed, use **Root access → Enable (repair)** (no flashing). |

## Credits

Root access uses [idrive7-8-ssh-root](https://github.com/cauliflowermossgrape/idrive7-8-ssh-root)
(public domain). The App Store is [mgu-store](https://github.com/robertnisipeanu/mgu-store) by Robert
Nisipeanu and IterioDev (GPL-3.0). Apps installed from the Store — MGU Custom Apps (cauliflowermossgrape,
MIT), idrive-apps (Robert Nisipeanu, GPL-3.0), car-apps (IterioDev, PolyForm Strict), RideTube
(JustAnotherPU, GPL-3.0), Doom (GPL-2.0) — keep their own authors and licences. Charge for your
installation service and support, credit the originals, and tell clients the apps themselves are free.
