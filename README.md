# Hardware Stress Tester

A Windows desktop application that orchestrates hardware stress testing for PC diagnostics. It wraps industry-standard compiled binaries (y-cruncher, FurMark, DiskSpd) and provides real-time telemetry monitoring via LibreHardwareMonitor, automated error detection, and branded PDF Stability Certificates.

---

## Table of Contents

- [Requirements](#requirements)
- [Running from Source](#running-from-source)
- [Building a Standalone Executable](#building-a-standalone-executable)
- [Compiled Folder Structure](#compiled-folder-structure)
- [Deploying to Another Machine](#deploying-to-another-machine)
- [Configuration Files](#configuration-files)
- [Troubleshooting](#troubleshooting)

---

## Requirements

### For Running from Source
- **Python 3.10+** (Windows)
- Python packages listed in `requirements.txt`:
  - `customtkinter` — UI framework
  - `psutil` — system memory and process utilities
  - `reportlab` — PDF certificate generation
  - `pythonnet` — .NET interop for LibreHardwareMonitor DLL

### For Running the Compiled Executable
- **Windows 10/11 (64-bit)** — no Python installation needed
- **Administrator privileges** — required for LibreHardwareMonitor kernel driver and hardware telemetry access

---

## Running from Source

1. Create a virtual environment and install dependencies:
   ```batch
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

2. Launch the application:
   ```batch
   start.bat
   ```
   Or manually:
   ```batch
   .venv\Scripts\python main.py
   ```

---

## Building a Standalone Executable

The project includes a `build.bat` script that automates the entire compilation process using [PyInstaller](https://pyinstaller.org/).

### Steps

1. **Ensure you have a working `.venv`** with all dependencies installed (see above).

2. **Run the build script**:
   ```batch
   build.bat
   ```

3. The script will:
   - Install/upgrade PyInstaller inside the virtual environment
   - Locate the `customtkinter` package for theme bundling
   - Run PyInstaller in `--onefile` mode with a UAC admin manifest
   - Copy the `Engines` folder, config files, `Logotypes`, and the packaged README, and create output directories
   - Produce the final distributable at `dist\RWS_StressTester\`

4. **Launch the compiled app**:
   ```batch
   dist\RWS_StressTester\RWS_StressTester.exe
   ```

> **Note:** The build uses `--windowed` mode so no console window appears. If you need to see console output for debugging, edit `build.bat` and change `--windowed` to `--console`.
>
> `build.bat` regenerates `RWS_StressTester.spec` on every build. `StressTester.spec` is a leftover from an older `--onedir` build with machine-specific paths and is not used.

---

## Compiled Folder Structure

After a successful build, the `dist\RWS_StressTester\` folder contains:

```
dist\RWS_StressTester\
│
├── RWS_StressTester.exe        ← Main application (self-contained, unpacks its
│                                  Python runtime to %TEMP% at launch)
├── README.txt                  ← Operator guide (from README_DIST.txt)
├── settings.json               ← User settings (logo path, telemetry mappings, thermal limits)
├── presets.json                ← Saved test presets
├── Logotypes\                  ← Certificate logos
│
├── Engines\                    ← Stress-test engine binaries (REQUIRED)
│   ├── DiskSpd\amd64\diskspd.exe
│   ├── FurMark\furmark.exe
│   ├── y-cruncher\y-cruncher.exe
│   ├── LibreHardwareMonitor\LibreHardwareMonitorLib.dll
│   └── PawnIO_Driver\PawnIO_setup.exe
│
├── results\                    ← Created at runtime — one folder per run
│   └── <run_id>\
│       ├── run_summary.json
│       ├── telemetry_summary.json
│       └── logs\
│           ├── <test>_stdout.log
│           ├── <test>_ycruncher.log
│           └── engine_meta.json
│
└── reports\                    ← Created at runtime — generated PDF certificates
```

### Key Points

| Item | Purpose |
|------|---------|
| `RWS_StressTester.exe` | Main application — double-click to launch |
| `Engines\` | All stress-test binaries. **Must be present alongside the .exe** |
| `settings.json` | Persisted user configuration (logo, sensor mappings, thermal limits) |
| `presets.json` | Saved diagnostic presets |
| `results\` | Auto-created — stores per-run logs and summaries |
| `reports\` | Auto-created — stores generated PDF certificates |

---

## Deploying to Another Machine

To run the application on a PC that **does not have Python installed**:

1. **Copy the entire `dist\RWS_StressTester\` folder** to the target machine.
   - You can put it anywhere (Desktop, USB drive, `C:\Tools\`, etc.)

2. **Run as Administrator**: Right-click `RWS_StressTester.exe` → *Run as administrator* (the exe also requests elevation itself).
   - Admin privileges are required for LibreHardwareMonitor to access hardware sensors.

3. **Update `settings.json`** on the target machine:
   - The `logo_path` field points to an image file on the build machine. Update it to a valid path on the target machine, or use the in-app file picker to select a new logo.
   - The `telemetry_mappings` may need to be reconfigured via the in-app sensor picker if the target hardware differs.

> **Important:** Do **not** copy only the `.exe` file. The entire `RWS_StressTester\` folder is required — the `.exe` depends on the `Engines\` folder and config files next to it.

---

## Configuration Files

### `settings.json`
Stores persistent user preferences:
- `logo_path` — Path to the company logo for PDF certificates
- `customer_name` — Customer name for the certificate
- `certificate_machine_name` — Machine identifier
- `telemetry_mappings` — Hardware sensor ID mappings

This file is auto-saved by the application. It can also be manually edited.

### `presets.json`
Stores saved test profiles (component selections and durations). Managed entirely through the UI.

### `test_state.json`
Auto-created at runtime to track the active test. Used for crash recovery detection on next launch. Automatically cleaned up after a successful run.

---

## Troubleshooting

### Application won't start
- Ensure you are running as **Administrator**.
- Check that the `Engines\` folder is present next to the `.exe`.

### "LibreHardwareMonitor DLL not found" error
- Verify that `Engines\LibreHardwareMonitor\LibreHardwareMonitorLib.dll` exists.
- The DLL is staged to a temp directory on first use — ensure the user has write access to `%LOCALAPPDATA%` or `%TEMP%`.

### Telemetry shows no data
- Hardware sensors require admin privileges to read.
- The sensor mappings in `settings.json` are hardware-specific. Use the in-app sensor configuration to remap sensors on new hardware.

### Anti-virus false positive
- PyInstaller executables are occasionally flagged by anti-virus software. This is a known false positive. Add an exclusion for the `RWS_StressTester\` folder if needed.

### Build fails with "hidden import not found"
- Ensure all packages in `requirements.txt` are installed in the `.venv`.
- Run `.venv\Scripts\pip install -r requirements.txt` before building.

### Console output for debugging
- Edit `build.bat` and change `--windowed` to `--console` to see stdout/stderr in a terminal window when running the compiled app.
