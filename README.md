# RWS Stress Tester

A Windows desktop app that stress-tests a PC's CPU, RAM, GPU, video memory, storage and power
supply, watches temperatures and errors while the tests run, and produces a PDF **System Stability
Certificate**.

It drives well-known tools ([y-cruncher](http://www.numberworld.org/y-cruncher/),
[FurMark](https://www.geeks3d.com/furmark/), [DiskSpd](https://github.com/microsoft/diskspd)),
reads sensors through [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor),
stops the run if anything overheats, and fails a test on calculation errors, rendering artifacts,
engine crashes or hardware errors logged by Windows.

> [!WARNING]
> Stress tests push hardware to its thermal and power limits. Run them only on machines you are
> responsible for, with working cooling, and keep the GPU, VRAM and PSU phases short unless you know
> the cooling can handle them. The software is provided as is, without warranty of any kind (see
> [License](#license)).

---

## Contents

- [Download](#download)
- [Quick start](#quick-start)
- [What it tests](#what-it-tests)
- [How a run passes or fails](#how-a-run-passes-or-fails)
- [Documentation](#documentation)
- [Building from source](#building-from-source)
- [License](#license)

---

## Download

Get the latest **`RWS_StressTester.zip`** from the
[Releases page](https://github.com/RWS-HeNa93/rws_stress_tester/releases/latest).

**Requirements:** Windows 10 or 11 (64-bit) and administrator rights. No Python or other software
needs to be installed.

## Quick start

1. **Unblock the ZIP before extracting it:** right-click it → **Properties** → tick **Unblock** →
   **OK**. Otherwise Windows may block the bundled engines.
2. Extract it and **keep the whole folder together**: `RWS_StressTester.exe` needs the `Engines\`
   folder next to it. It can run from a USB drive or any writable folder.
3. Right-click `RWS_StressTester.exe` → **Run as administrator**.
4. On first launch the app checks for the **PawnIO** driver and offers to install it (needed for
   CPU temperature; the running app picks it up, no restart needed). It then assigns the sensors
   automatically and opens **Review Sensor Assignment**: check each reading shows the right device
   and a live value, fix any that look wrong, and click **Approve**. **CPU Temperature** and
   **GPU Temperature** must be assigned, or the thermal kill switch cannot protect the hardware.
5. Switch on the tests you want, set the minutes for each, and press **Start diagnostics**.
6. When the run ends, read the verdict (**CERTIFIED STABLE** or **UNSTABLE**) and collect the PDF
   certificate from the `reports\` folder.

## What it tests

Phases always run in this order. Each one can be switched on or off and given its own duration.

| Phase | Engine | Finds |
|---|---|---|
| **CPU** | y-cruncher (SFTv4, BKT, VT3, SNT, SVT, N63) | Unstable core voltage, boost and Curve Optimizer / undervolt settings |
| **RAM** | y-cruncher (FFTv4, VT3, N63) on the amount of memory you choose | Unstable XMP/EXPO, timings, memory controller and SoC voltage |
| **GPU** | FurMark with its artifact scanner | GPU core instability, cooling problems, failing cards |
| **VRAM** | FurMark while filling the video memory you choose and reading it back every frame | Faulty or overheating video memory |
| **Storage (R) / (W)** | DiskSpd, random 64 KB I/O, 4 threads × 32 outstanding I/Os | Failing drives, cables and controllers |
| **PSU** | y-cruncher's maximum-power tests and FurMark at the same time | Power delivery under the highest combined load |

## How a run passes or fails

A phase fails if:
- y-cruncher reports a wrong result, or FurMark finds a rendering artifact
- Windows logs a WHEA hardware error, a display driver reset (TDR), an NVIDIA/AMD driver error, or
  a disk, NVMe or file system error (the System event log is checked every 30 seconds)
- an engine crashes or quits early
- a temperature goes over the thermal limit. The limits can be lowered but never raised above the
  built-in ceilings (CPU 105 °C, GPU 95 °C)
- the PC restarts or loses power. The next launch marks the phase **FAILED (System Crash)** and
  shows what Windows recorded, such as the blue screen code or WHEA errors

If every selected phase passes, the verdict is **CERTIFIED STABLE**, whatever the test durations,
so choosing sensible durations is up to the technician. Each run also saves per-phase logs and
JSON summaries in `results\`.

## Documentation

The full operator guide is [`README_DIST.txt`](README_DIST.txt), shipped in the package as
`README.txt`. It covers every test option, the screens, the output files and troubleshooting.

---

## Building from source

### Build it on a Windows PC

You need Windows 10 or 11 (64-bit) and about 1 GB of free space.

1. **Install Python 3.12 (64-bit)** from [python.org](https://www.python.org/downloads/windows/).
   During setup, tick **Add python.exe to PATH**. Avoid newer versions for now: the hardware-sensor
   library (pythonnet) may not support them yet. Use the python.org installer rather than the
   Microsoft Store version.

2. **Get the code**, either way:
   - `git clone https://github.com/RWS-HeNa93/rws_stress_tester.git`
   - or on GitHub: **Code → Download ZIP**. Unblock the ZIP before extracting it, as in the
     [Quick start](#quick-start).

3. **Open Command Prompt in the project folder** and run:
   ```batch
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   build.bat
   ```
   `build.bat` installs PyInstaller into `.venv`, builds a single `.exe`, and assembles the package.
   It waits for a key press at the end.

4. **The result is `dist\RWS_StressTester\`.** That whole folder is the app:
   ```
   dist\RWS_StressTester\
   ├── RWS_StressTester.exe     the app (asks for administrator rights when started)
   ├── README.txt               operator guide (copied from README_DIST.txt)
   ├── LICENSE.txt              MIT license for RWS Stress Tester's own code
   ├── THIRD_PARTY_NOTICES.md   licenses of the bundled engines and libraries
   ├── licenses\                third-party license texts
   ├── settings.json            empty defaults; filled in from the app
   ├── presets.json             empty; presets are saved from the app
   ├── Engines\                 y-cruncher, FurMark, DiskSpd, LibreHardwareMonitor, PawnIO installer
   ├── Logotypes\               certificate logos
   ├── results\                 per-run logs and summaries (written by the app)
   └── reports\                 PDF certificates (written by the app)
   ```

To see Python errors from the built app, change `--windowed` to `--console` in `build.bat` and
rebuild.

### Build it with GitHub Actions

Every push runs [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml) on a
Windows machine. It follows the same steps as above, checks that the package contains the `.exe`,
the engines and the license files, and uploads `dist\RWS_StressTester\` as an artifact (kept for 30
days).

To get it: **Actions** tab → the run → **Artifacts** at the bottom of the page. To build without
pushing, open **Build Windows package** in the Actions tab and press **Run workflow**. Unblock the
downloaded ZIP before extracting it.

### Run from source

After steps 1–3 above (`build.bat` is not needed), open Command Prompt **as administrator**
(right-click → *Run as administrator*), go to the project folder and run:

```batch
.venv\Scripts\python main.py
```

Administrator rights are needed to read hardware sensors. The app uses `Engines\` and writes
`settings.json`, `presets.json`, `results\` and `reports\` in the project folder; `results\` and
`reports\` are ignored by git.

### Deploy to a test machine

1. Copy the whole `dist\RWS_StressTester\` folder (or the extracted GitHub artifact) to the machine:
   Desktop, USB drive, `C:\Tools\`, anywhere writable. Never copy the `.exe` alone.
2. Run `RWS_StressTester.exe` as administrator. No Python is needed on the test machine.
3. Assign sensors and choose a logo in the app. See `README.txt` in the folder.

### Project layout

| Path | What it is |
|------|------------|
| `main.py` | Entry point: starts the UI |
| `ui.py` | CustomTkinter interface: setup screen, dashboard, dialogs, log viewer |
| `orchestrator.py` | Runs the session: builds engine commands, runs phases, reads engine output, decides pass/fail, writes logs and summaries |
| `telemetry.py` | Sensor readings through LibreHardwareMonitor (pythonnet); feeds the thermal kill switch |
| `eventlog.py` | Watches the Windows System log for hardware errors, driver resets and storage errors during each phase |
| `report_gen.py` | PDF certificate (reportlab) |
| `Engines\` | Bundled third-party tools, shipped unchanged except y-cruncher's runtime DLLs (see below) and without debugging symbols (`.pdb`) or LibreHardwareMonitor's saved GUI settings |
| `settings.json`, `presets.json` | Empty defaults copied into the package |
| `README_DIST.txt` | Operator guide, shipped as `README.txt` |
| `LICENSE`, `THIRD_PARTY_NOTICES.md`, `licenses\` | License for this project and for the bundled third-party software, shipped in the package |
| `build.bat` | Builds the `.exe` and assembles `dist\RWS_StressTester\` |
| `.github/workflows/build-windows.yml` | The same build on GitHub Actions |

Notes:
- `Engines\y-cruncher\` contains 32-bit `msvcp140.dll` and `vcruntime140.dll` (14.44). The
  y-cruncher launcher is 32-bit and crashes with `0xC0000005` on machines with an older Visual C++
  runtime; these copies avoid that. Keep them when updating y-cruncher.
- `.spec` files are generated by `build.bat` on every build and are not kept in git.
- `requirements.txt` does not pin versions, so each build installs the newest packages. When a
  dependency is added, update `THIRD_PARTY_NOTICES.md` and `licenses\`.

### Troubleshooting the build

**`Virtual environment not found at .venv`**: run step 3's first two commands in the project folder
first.

**`'python' is not recognized`**: Python is not on PATH. Reinstall it with **Add python.exe to
PATH** ticked, or use `py -3.12 -m venv .venv` instead.

**`pip install` fails on pythonnet**: the Python version is too new for pythonnet. Install Python
3.12, delete `.venv`, and repeat step 3.

**`No module named tkinter` or the UI does not open**: use the python.org installer, which includes
tkinter.

**The built app is flagged by anti-virus**: PyInstaller apps are sometimes flagged by mistake. Add an
exclusion for the `RWS_StressTester` folder.

**Problems running tests** (sensors, engines, failed phases): see Troubleshooting in
[`README_DIST.txt`](README_DIST.txt).

---

## License

RWS Stress Tester's own code is released under the [MIT License](LICENSE).

The bundled engines and libraries are **not** covered by the MIT License. Each keeps its own license,
listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md):

- **y-cruncher** by Alexander J. Yee: free for non-commercial use. Commercial use requires the
  developer's permission ([license](http://www.numberworld.org/y-cruncher/license.html)).
- **FurMark 2** by Geeks3D, freeware: <https://www.geeks3d.com/furmark/>
- **DiskSpd** by Microsoft: Microsoft Software License Terms (source code under MIT)
- **LibreHardwareMonitor**: Mozilla Public License 2.0
- **PawnIO** by namazso: GNU GPL 2.0 or later

RWS Stress Tester is not affiliated with or endorsed by the authors of these tools.
