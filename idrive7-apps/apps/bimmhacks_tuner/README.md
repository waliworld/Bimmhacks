# Bimmhacks Tuner

Live gauges, a CSV datalogger with **tuning, engine, per-cylinder, MHD-capture and your own custom PIDs**, the
engine unit's fault codes and — for a car that **already runs a multi-map tune** — engine map switching.
By Bimmhacks (Mr. Wali), built on the idrive7-apps kit.

**It does not flash, write or change a tune.** Everything it does is either a read, or one of the two switch
commands the tuner's own code in the DME already answers. It is not affiliated with MHD, BM3 or any tuning company.

| Page | What it does | Sends to the car |
|---|---|---|
| **Gauges** | engine speed, boost, load, ignition timing, lambda (with its target), knock retard, coolant, oil; session peaks / mins; the last 60 s of boost | reads only |
| **PIDs** | every value that is switched on, live, with whether the car answers it (*live*, *not on this car*, *no reply*, *out of range*) | reads only |
| **Datalog** | records every channel that is on to a CSV at 1 / 2 / 5 Hz; keeps the newest 20 logs; each file stops at 4 MiB | reads only |
| **Faults** | the engine unit's (DME 0x12) fault codes and their state; **Clear** needs two presses | read: `19 02 0C`; clear: `14 FF FF FF` (after the second press) |
| **Maps** | Map 1–4 and Max Cooling for an already-tuned car | `2F 01 <map-1>`, `2F 03 01\|00` — **ships OFF** |

## Log packs (Settings → Log pack)

| Pack | Default | Values | Where the numbers come from |
|---|---|---|---|
| **Basic** | on | boost (absolute and gauge), boost target, load, ignition timing, intake air | M Vitals' candidate lists (`config.dme` in `app.json`) |
| **Tuning** | on | lambda, lambda target, knock retard, ignition angle, charge pressure, boost actuator duty, pedal, boost deviation, MAP, wastegate position, throttle, air mass, short/long-term fuel trim, mixture adaptation, knock adaptation, rail pressure and target, low-pressure fuel, ethanol content | BMW's own SGBD for the DME8FF_R engine unit (B48 / B58 …) and standard OBD-II (SAE J1979) PIDs |
| **Engine** | off | VANOS intake/exhaust target and actual, oil pressure, exhaust temperature (model), fuel temperature, fuel flow, injector adaptation, DME torque, battery voltage, catalyst temperature, absolute load, fuel level, outside temperature | same |
| **Cylinders** | off | ignition angle, rough running and misfire count for cylinders 1–6 (a 4-cylinder car answers 1–4) | BMW's SGBD |
| **MHD capture** | off | the eight identifiers an MHD capture was seen reading — `22 3010 / 3020 / 3030 / 3031 / 3032 / 37FE / 586A / 4098` — logged **raw** | see below |
| **Custom** | on | the PIDs in your `custom_pids.json` | you |

Each value is tried as UDS `22 <DID>` and, for the standard PIDs, as OBD service `01` too (some engine units
refuse one form). A car answers what it answers: a value the engine unit does not have is shown as *not on this
car*, left alone for two minutes, and blank in the log. The Datalog page shows how many values of each pack the car
is actually answering, so you can switch off what yours does not have.

### The MHD capture pack — what it is and is not

The Engine Config README (`apps/live_adjustments/README.md`) records that MHD was seen reading those eight identifiers. **Neither BMW's
catalogue nor this repository says what they mean**, so this app does not invent names or scales for them: the
pack logs each one as the **exact bytes in hex** (`mhd_3010_raw` = `00A1FF12`), up to 16 bytes. Use it to line the
numbers up against what MHD's own logger shows for the same drive, work out the scaling, and then add a proper
entry for it to `custom_pids.json` (below). It is read-only like everything else, and a car that is not MHD-tuned
simply refuses them (blank cells). This is *not* MHD's own PID list and not a copy of any MHD feature.

### Your own PIDs: `custom_pids.json`

Put a file named `custom_pids.json` in the app's data folder on the head unit (the Datalog page prints the exact
path; copy it there over SSH). It is read when the app starts, when a log starts, and when you press **Reload PIDs**
on the Datalog page. A bad entry is skipped with a message that names it (also shown on the Datalog page); the good
ones still load. At most 24 entries, 64 KiB.

```json
{ "pids": [
    { "id": "boost_dev", "label": "Boost deviation", "unit": "kPa", "decimals": 1,
      "ecu": "0x12", "did": "4536", "type": "s16", "factor": 0.0125, "min": -300, "max": 300, "rate": "fast" },
    { "id": "unknown_x", "label": "Unknown value", "did": "37FE", "type": "hex", "len": 4 },
    { "id": "rpm_obd", "label": "RPM (OBD)", "obd": "0C", "type": "u16", "factor": 0.25 }
] }
```

| Field | |
|---|---|
| `id` | `a-z 0-9 _`, a letter first, up to 24; the CSV column is `c_<id>` |
| `label`, `unit`, `decimals` | shown on the PIDs page (decimals 0–6, default 1) |
| `ecu` | the control unit's diagnostic address: `18`, `"18"` or `"0x12"` (default `0x12`, the engine unit) |
| `did` **or** `obd` | `did`: 4 hex digits, read with UDS `22`; `obd`: 2 hex digits, read with OBD service `01` |
| `type` | `u8 s8 u16 s16 u32 s32` → value = raw × `factor` + `offset`; or `hex` → the raw bytes (`len` 1–16, `did` only) |
| `at`, `factor`, `offset` | byte offset into the answer (0–32), scale (non-zero), offset |
| `min`, `max` | an answer outside is "out of range", not a value |
| `rate` | `slow` (default) or `fast` |

Custom PIDs only ever **read**. The app's link physically cannot send anything else (`kit/readonly-diag.js`).

### How fast does it log?

One request at a time, over the head unit's own diagnostic link. Each round reads every **fast** value and a
few **slow** ones in rotation (so a long list of slow values cannot starve boost and lambda). The Datalog page
shows the measured rate ("fast engine values update about N times a second"); the more packs you switch on, the
slower a round. Switch on only the packs your car answers. A value is only written to the log while it is fresh
(younger than about two and a half of its own read intervals, never less than 4 s); otherwise the cell is blank.

If the engine unit stops answering (ignition off, car asleep) the app notices, waits, and asks again every
10 s by itself — no restart needed.

## Safety rules (each is covered by `kit/tools/test_bimmhacks_tuner.js` / `test_bimmhacks_pids.js`)

- **Map switching ships OFF** (*Settings → Map switching*). Off means not one byte is sent, for any button.
  Turn it on only if your car already runs an MHD multi-map tune.
- A map press counts only when the DME **echoes** the request. An untuned DME refuses it and the page says
  *Not available on this car*. There is no read-back of the active map, so the screen says *accepted*, never
  *confirmed*.
- **Max Cooling ON** only with the engine really running (live engine speed over 500 rpm; demo data never
  counts). OFF is always allowed.
- One command at a time; maps 1–4 only; nothing is sent while the diagnostic link is down.
- **Faults:** the first Clear press only arms it (nothing is sent, and it runs out after 8 s); the second sends.
  Clearing does not fix the cause — a fault that is still there comes back.
- Every engine-unit value goes through `kit/readonly-diag.js`, which cannot send anything but reads. Packs and
  custom PIDs cannot change that.
- **Demo data is never logged.** With no car, the demo drive is shown (badged DEMO) but *Start log* refuses
  and a running log stops. A value the car did not give is a blank cell in the CSV, never a 0.

## The datalog

`tuner-YYYYMMDD-HHMMSS.csv` in the app's data folder, `logs/`. Always first the car's own signals and boost,
then the basic values, then each pack that was on when the log started, then your custom PIDs. The columns
(`time_s` first) are fixed for that file:

```
time_s, rpm, speed_kmh, torque_nm, coolant_c, oil_c, g_long, g_lat, boost_bar,
boost_abs_kpa, boost_target_kpa, load_pct, timing_deg, iat_c,
lambda, lambda_target, knock_retard_deg, ignition_deg, charge_kpa, boost_duty_pct, pedal_pct, ...   (Tuning)
vanos_in_target_deg, ... oil_pressure_bar, ...                                                       (Engine)
ign_cyl1_deg ... rough_cyl1 ... misfire_cyl1 ...                                                     (Cylinders)
mhd_3010_raw, mhd_3020_raw, ...                                                                      (MHD capture, hex)
c_<your ids>                                                                                         (Custom)
```

`boost_bar` is gauge boost (absolute minus the outside air pressure the engine unit reports). Copy logs off the car
over SSH from the app's `logs/` folder.

## Build, test, preview

```sh
node kit/tools/test_bimmhacks_tuner.js                       # logger, faults, maps, channels
node kit/tools/test_bimmhacks_pids.js                        # PID tables vs BMW's catalogue, decoding, custom file, poller
python kit/tools/build.py bimmhacks_tuner                    # -> dist/bimmhacks_tuner@1.0.0.mgu
node kit/tools/preview.js dist/bimmhacks_tuner out wait=10 render BTN_PAGE render   # PC preview (demo drive)
```

The PID tests check every BMW-sourced identifier against `apps/live_data/builtin/DME8FF_R.json` (type, offset,
factor and unit conversion), run the poller against the real `ReadOnlyDiag` / `DidReader` with a fake transport,
and were mutation-checked: removing the "wake a silent unit" step turns them red.

## What is not verified

Built and tested against in-memory diagnostic links and the emulator, **not on a car**. A car may answer none,
some or all of a pack; the standard-PID formulas are SAE J1979 and the BMW ones come from BMW's own catalogue, but
how *your* engine unit responds is exactly what the PIDs page is for. The engine-unit identifiers in the Basic pack
are M Vitals', themselves "first plausible answer wins". The two map commands are the ones Engine Config already
uses, validated on one MHD multi-map MG1; they have not been exercised by this app on a car. Please report what
your car does.

Modules: `channels.js` · `pids.js` · `custom.js` · `poller.js` · `logger.js` · `faults.js` · `maps.js` · `main.js`.
