# CivicWater Sentinel Data (P1)

Reference document for the BattLeDIM data used by the project: where it lives, what each
file contains, how it was processed and how to regenerate it. Owner: P1 (data and replay).

## 1. Where the files live

Data is **not** stored in Git. It lives on the VM under `~/shared/civicwater/`
(`SENTINEL_DATA_DIR` points to `~/shared/civicwater/data`).

```text
~/shared/civicwater/
├── data/                       ← project code reads from here
│   ├── raw/                    raw data, exactly as downloaded
│   │   ├── 2018_SCADA.xlsx
│   │   └── 2019_SCADA.xlsx
│   ├── v1/                     processed data, version 1 (sealed)
│   │   ├── scada_2018.csv.gz   training
│   │   ├── scada_2019.csv.gz   test (used only by the replay)
│   │   ├── sensors.csv         metadata of the 37 sensors
│   │   ├── leaks_2018.csv      2018 leaks (may be used for training)
│   │   └── MANIFEST.json       rows, period, NaN and SHA-256 of each file
│   └── replay/v1/              API-ready windows (JSONL)
│       ├── windows_2019.jsonl                         full year (105,120)
│       └── windows_2019_2019-03-01_2019-03-07.jsonl   one week (2,016)
└── eval/v1/
    └── leaks_2019.csv          2019 GROUND TRUTH — read by evaluation (P6) only
```

## 2. Source

- **BattLeDIM**, simulated L-Town network (Vrachimis et al., 2022), loaded with `epyt-flow`
  in Colab. `epyt-flow` does not install on LinuxONE (s390x needs GDAL), so loading runs
  off-platform and only the files are uploaded to the VM.
- **Warning:** in `battledim.load_data`, `return_test_scenario=False` returns **2019** and
  `True` returns **2018**. Always identify the year from the timestamp column.
- Notebook that produced v1: `notebooks/p1_data/p1_dados_battledim.ipynb`.
- SHA-256 hashes of the original Excel files are in `MANIFEST.json` (`raw_files`).

## 3. SCADA series (`scada_<year>.csv.gz`)

Wide table: one row every 5 minutes, one column per sensor.

| Item | Value |
|---|---|
| Index | `timestamp` column, timezone-naive |
| Period | `<year>-01-01 00:00` to `<year>-12-31 23:55` |
| Rows | 105,120 per year (365 × 288), regular grid, no duplicates |
| Columns | 37: 33 `Pressure_n<node>`, 3 `Flow_*`, 1 `Level_T1` |
| Units | pressure in **m**, flow in **m³/h**, level in **m** (sheet names of the original Excel: `Pressures (m)`, `Flows (m3_h)`, `Levels (m)`) |
| NaN | 0 in both years |

- Each value is the **mean of the previous 5 minutes**: the timestamp marks the **end** of
  the window.
- BattLeDIM has no timezone. By convention, simulated time is treated as **UTC** in the
  windows sent to the API (`...Z`).
- Flows: `Flow_p227` and `Flow_p235` are the two network inlets; `Flow_PUMP_1` is the pump
  (0 = pump off, not an error).

## 4. Sensors and official IDs

- `config/sensors.battledim.json` is the **official registry** (DEP-DATA-01,
  `provisional: false`). It replaces `config/sensors.provisional.json` (`P01..P33`, kept for
  tests only).
- Each sensor ID is the **L-Town network element**: node `n<N>` (pressure), pipe `p<N>` and
  pump `PUMP_1` (flow), tank `T1` (level). These are the topology IDs, useful for the
  sensor → region mapping (P4).
- CSV column ↔ ID: `Pressure_n1` ↔ `n1`, `Flow_p227` ↔ `p227`, `Level_T1` ↔ `T1`.
- `sensors.csv`: `sensor_id`, `tipo` (type), `unidade` (unit), `elemento_rede` (network
  element), `papel` (role).
- To make the API use the official registry:
  `SENTINEL_SENSOR_REGISTRY=config/sensors.battledim.json`.

## 5. Cleaning rules (v1)

1. The 82 `Demand_*` columns (consumption meters) are dropped; the design does not use them.
2. Values outside the physical range become NaN:

   | Type | Accepted range |
   |---|---|
   | Pressure | 0 to 100 m |
   | Flow | 0 to 500 m³/h |
   | Level | 0 to 10 m |

3. Gaps of up to **3 windows** (15 min) are interpolated; longer gaps stay NaN.

In v1 no rule was triggered: 0 out-of-range values and 0 NaN. The ranges are a team
decision (they do not come from the dataset) and act as a safeguard.

## 6. Leak labels

| File | Location | Who may read it |
|---|---|---|
| `leaks_2018.csv` | `data/v1/` | everyone (training year) |
| `leaks_2019.csv` | `eval/v1/` | **evaluation (P6) only** |

Columns: `pipe`, `start`, `end` (repair), `leak_size`, `type` (`abrupt` or `incipient`),
`peak`. `leaks_2018.csv` also has `repaired_in_2018`.

- `end = <year>-12-31 23:55` means **not repaired** within the year.
- `leak_size` is probably the leak diameter in metres (BattLeDIM generator format) —
  **not confirmed**.
- Labels come from the simulation configuration bundled with `epyt-flow`, which may contain
  **more** leaks than the official 2018 repair report given to BattLeDIM participants.
- 2018: 14 leaks, 10 repaired. 2019: 23 leaks.
- 4 leaks (p257, p427, p654, p810) were never repaired in 2018 and are **already active at
  2019-01-01 00:00**. Evaluation must decide whether they count.

**Ground-truth rules:** the detector, training and calibration never read
`leaks_2019.csv`. It is kept outside `data/` on purpose, and a test fails if any
`leaks_2019*` file appears inside `data/`. Replay windows never carry labels (the API
rejects unknown fields).

## 7. 2018 training mask

`src/sentinel/data/clean_mask.py` tells which 2018 windows may be used for training.

```python
from sentinel.data.clean_mask import carregar_vazamentos, mascara_limpa
df = pd.read_csv(".../v1/scada_2018.csv.gz", index_col="timestamp", parse_dates=True)
train = df[mascara_limpa(df.index, carregar_vazamentos(), politica="reparados")]
```

| Policy | Excludes | Remaining 2018 |
|---|---|---|
| `reparados` (default) | the period of each repaired leak | 43.5% (~159 days) |
| `reparados+crescimento` | + growth phase (start → peak) of unrepaired leaks | 21.1% (~77 days) |
| `todos` | any active leak | 2.1% (~7.5 days) |

- `reparados` is the default because the 4 unrepaired leaks are small, incipient and still
  active in 2019: they are part of the "normal" state the detector will face.
- `margem_janelas` excludes extra windows after each repair.
- The mask has **gaps**: sequence models (GRU, 12–24 h) must build sequences only inside
  contiguous runs of clean windows.
- The high-residual filter (design doc §2) can be applied on top by P2.

## 8. Replay

`src/sentinel/data/replay_windows.py` turns the processed SCADA into windows following the
`sentinel.window v1.0` contract (one per line, JSONL), with official IDs and UTC
timestamps. Every window is validated with `SensorWindow` before being written.

```bash
# generate (full year or a slice)
python -m sentinel.data.replay_windows --year 2019
python -m sentinel.data.replay_windows --year 2019 --start 2019-03-01 --end 2019-03-07

# send to the API (which must be running with the official registry)
python scripts/replay.py $SENTINEL_DATA_DIR/replay/v1/windows_2019.jsonl \
    --url http://127.0.0.1:$API_PORT --speed 2
```

- `--speed` is in windows per second; `0` = as fast as possible. Real time
  (1 window every 5 min) is `--speed 0.00333`.
- Checkpoint 1 (2026-10-17, mock forecaster and detector): 2,016 windows, 100% accepted
  (201), 352 windows/s, client latency p50 1.6 ms and p99 17 ms. The first 144 windows
  return `insufficient_history` (12 h of history required).

## 9. Versioning and integrity

- Each data version (`v1`, `v2`, ...) is **sealed** by its `MANIFEST.json`.
  A change in the data becomes a new version; an existing one is never overwritten
  (the manifest script refuses to).
- `tests/test_p1_data.py` checks: regular grid, 105,120 rows, columns = official registry,
  physical ranges, SHA-256 equal to the manifest, and ground truth outside `data/`.
  Tests that read data are skipped when `SENTINEL_DATA_DIR` does not exist (CI).

## 10. How to regenerate a version

1. Run `notebooks/p1_data/p1_dados_battledim.ipynb` in Colab and download the files.
2. Upload the Excel files to `data/raw/`, the processed files to `data/vN/` and
   `leaks_2019.csv` to `eval/vN/`.
3. On the VM, inside the `start.sh` environment:

```bash
python -m sentinel.data.manifest --version vN
python -m sentinel.data.build_registry --version vN
python -m sentinel.data.replay_windows --year 2019 --version vN
python -m pytest -q tests/test_p1_data.py
```

## 11. Notes and limitations

- **Low-variance sensors:** `n215` (0.21 m range in 2018) and `n740` (0.87 m).
  With a small σ, the z-score inflates and may cause false alarms (P2/P4 take note).
- **Simulated network:** results show the method with exact labels, not field performance.
- **Topology:** the L-Town network file (needed for the sensor → region mapping) is not yet
  in `raw/`; it is available on the BattLeDIM Zenodo record.