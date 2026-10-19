# RWS Stress Tester

A Windows desktop app that runs hardware stress tests for PC diagnostics and produces a PDF System
Stability Certificate. It controls bundled tools (y-cruncher, FurMark, DiskSpd), reads temperatures
and power through LibreHardwareMonitor, stops the run if anything overheats, and fails a test on
engine errors, rendering artifacts or hardware errors logged by Windows.

This file is for building the app. **The operator guide is [`README_DIST.txt`](README_DIST.txt)**:
it explains the tests, the screens and the results, and `build.bat` ships it next to the `.exe` as
`README.txt`.

---

## Contents

- [Build it on a fresh PC](#build-it-on-a-fresh-pc)
- [Build it with GitHub Actions](#build-it-with-github-actions)
- [Run from source](#run-from-source)
- [Deploy to a test machine](#deploy-to-a-test-machine)
- [Project layout](#project-layout)
- [Troubleshooting the build](#troubleshooting-the-build)

---

## Build it on a fresh PC

You need Windows 10 or 11 (64-bit) and about 1 GB of free space.

1. **Install Python 3.12 (64-bit)** from [python.org](https://www.python.org/downloads/windows/).
   During setup, tick **Add python.exe to PATH**. Avoid newer versions for now: the hardware-sensor
   library (pythonnet) may not support them yet. Use the python.org installer rather than the
   Microsoft Store version.

2. **Get the code**, either way:
   - `git clone https://github.com/RWS-HeNa93/rws_stress_tester.git`
   - or on GitHub: **Code → Download ZIP**. Before extracting, right-click the ZIP →
     **Properties** → tick **Unblock** → **OK**. Otherwise Windows marks every extracted file as
     downloaded and may block the bundled engines.

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
   ├── RWS_StressTester.exe   the app (asks for administrator rights when started)
   ├── README.txt             operator guide (copied from README_DIST.txt)
   ├── settings.json          empty defaults; filled in from the app
   ├── presets.json           empty; presets are saved from the app
   ├── Engines\               y-cruncher, FurMark, DiskSpd, LibreHardwareMonitor, PawnIO installer
   ├── Logotypes\             certificate logos
   ├── results\               per-run logs and summaries (written by the app)
   └── reports\               PDF certificates (written by the app)
   ```

To see Python errors from the built app, change `--windowed` to `--console` in `build.bat` and
rebuild.

---

## Build it with GitHub Actions

Every push runs [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml) on a
Windows machine. It follows the same steps as above, checks that the package contains the `.exe`
and the engines, and uploads `dist\RWS_StressTester\` as an artifact (kept for 30 days).

To get it: **Actions** tab → the run → **Artifacts** at the bottom of the page. To build without
pushing, open **Build Windows package** in the Actions tab and press **Run workflow**. Unblock the
downloaded ZIP before extracting it.

---

## Run from source

After steps 1–3 above (`build.bat` is not needed), open Command Prompt **as administrator**
(right-click → *Run as administrator*), go to the project folder and run:

```batch
.venv\Scripts\python main.py
```

Administrator rights are needed to read hardware sensors. The app uses `Engines\` and writes
`settings.json`, `presets.json`, `results\` and `reports\` in the project folder; `results\` and
`reports\` are ignored by git.

---

## Deploy to a test machine

1. Copy the whole `dist\RWS_StressTester\` folder (or the extracted GitHub artifact) to the machine:
   Desktop, USB drive, `C:\Tools\`, anywhere writable. Never copy the `.exe` alone.
2. Run `RWS_StressTester.exe` as administrator. No Python is needed on the test machine.
3. Assign sensors and choose a logo in the app. See `README.txt` in the folder.

---

## Project layout

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
| `build.bat` | Builds the `.exe` and assembles `dist\RWS_StressTester\` |
| `.github/workflows/build-windows.yml` | The same build on GitHub Actions |

Notes:
- `Engines\y-cruncher\` contains 32-bit `msvcp140.dll` and `vcruntime140.dll` (14.44). The
  y-cruncher launcher is 32-bit and crashes with `0xC0000005` on machines with an older Visual C++
  runtime; these copies avoid that. Keep them when updating y-cruncher.
- `.spec` files are generated by `build.bat` on every build and are not kept in git.
- `requirements.txt` does not pin versions, so each build installs the newest packages.

---

## Troubleshooting the build

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
