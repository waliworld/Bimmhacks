# Bimmhacks Tuner

Live gauges, a CSV datalogger, the engine unit's fault codes and — for a car that **already runs a
multi-map tune** — engine map switching. By Bimmhacks (Mr. Wali), built on the idrive7-apps kit.

**It does not flash, write or change a tune.** Everything it does is either a read, or one of the two
switch commands the tuner's own code in the DME already answers. It is not affiliated with MHD, BM3 or
any tuning company.

| Page | What it does | Sends to the car |
|---|---|---|
| **Gauges** | engine speed, boost, load, ignition timing, coolant, oil, intake air, torque; session peaks / mins; the last 60 s of boost | reads only |
| **Datalog** | records 13 channels to a CSV at 1 / 2 / 5 Hz; keeps the newest 20 logs; each file stops at 4 MiB | reads only |
| **Faults** | the engine unit's (DME 0x12) fault codes and their state; **Clear** needs two presses | read: `19 02 0C`; clear: `14 FF FF FF` (after the second press) |
| **Maps** | Map 1–4 and Max Cooling for an already-tuned car | `2F 01 <map-1>`, `2F 03 01\|00` — **ships OFF** |

## Safety rules (each is covered by `kit/tools/test_bimmhacks_tuner.js`)

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
- Engine-unit values (boost, load, timing, intake air) go through `kit/readonly-diag.js`, which cannot send
  anything but reads.
- **Demo data is never logged.** With no car, the demo drive is shown (badged DEMO) but *Start log* refuses
  and a running log stops. A value the car did not give is a blank cell in the CSV, never a 0.

## The datalog

`tuner-YYYYMMDD-HHMMSS.csv` in the app's data folder, `logs/`. Columns:

```
time_s, rpm, speed_kmh, torque_nm, coolant_c, oil_c, g_long, g_lat,
boost_bar, boost_abs_kpa, boost_target_kpa, load_pct, timing_deg, iat_c
```

`boost_bar` is gauge boost (absolute minus the outside air pressure the engine unit reports). The engine-unit
columns are blank when the car does not answer them — see `config.dme` in `app.json`, the candidate lists are
the ones M Vitals ships (first plausible answer wins). A value older than 3 s is blank rather than repeated.
Copy logs off the car over SSH from the app's `logs/` folder.

## Build, test, preview

```sh
node kit/tools/test_bimmhacks_tuner.js                       # logger, faults, maps, channels
python kit/tools/build.py bimmhacks_tuner                    # -> dist/bimmhacks_tuner@1.0.0.mgu
node kit/tools/preview.js dist/bimmhacks_tuner out wait=10 render BTN_PAGE render   # PC preview (demo drive)
node kit/tools/preview.js dist/bimmhacks_tuner out --set maps=mhd BTN_PAGE BTN_PAGE BTN_PAGE render
```

## What is not verified

Built and tested against in-memory diagnostic links and the emulator, **not on a car**. The engine-unit
identifiers are M Vitals' (themselves "first plausible answer wins"); a car may answer none of them, and the
gauges then show `--`. The two map commands are the ones Engine Config already uses, validated on one MHD
multi-map MG1; they have not been exercised by this app on a car. Please report what your car does.

Modules: `channels.js` (channel table) · `logger.js` · `faults.js` · `maps.js` · `main.js` (the screen).
