RWS Stress Tester
Packaged Application Guide

Overview
RWS Stress Tester is a Windows desktop application for orchestrating hardware stress tests.
It is a control-and-monitoring wrapper around bundled third-party engines:
- y-cruncher for CPU and RAM stress
- FurMark for GPU stress
- DiskSpd for storage stress
- LibreHardwareMonitor for live telemetry

The app launches the engines, captures their stdout and stderr, watches live telemetry, applies
thermal abort rules, preserves per-phase logs, and can generate a PDF System Stability Certificate.

Keep This Folder Intact
Do not separate the executable from the folders and files shipped with it.

Downloaded ZIP / Archive Warning
If this software was downloaded from the internet as a ZIP or other archive, Windows may mark the archive as blocked.
If you extract it without removing that block first, Windows can propagate that security flag to the unpacked files, which may cause issues with the app, bundled engines, or supporting DLLs.

Recommended procedure before unpacking:
1. Right-click the downloaded archive.
2. Open Properties.
3. If an Unblock checkbox or button is shown, enable Unblock.
4. Click Apply.
5. Click OK.
6. Only then extract the archive.

Required package contents:
- RWS_StressTester.exe
- Engines\
- Logotypes\
- settings.json
- presets.json
- README.txt

Runtime folders created and used by the app:
- results\
- reports\

Important:
- Do not copy only RWS_StressTester.exe.
- Keep the entire RWS_StressTester folder together.
- Run the application as Administrator for reliable telemetry access.

Packaged Folder Layout
dist\RWS_StressTester\
  RWS_StressTester.exe
  README.txt
  settings.json
  presets.json
  Engines\
  Logotypes\
  results\
  reports\

About The Engine Readme Files
Several .txt files inside Engines\ are vendor manuals shipped with the underlying test tools.
They document the original engine binaries and are not the main operator guide for this app.
Use this README.txt for the RWS app workflow and UI.

Screen 1 - Setup
When the application opens, you start on the setup screen.

Top controls:
- Thermal Settings
- Assign Sensors

Select Test Phases area:
- CPU
- GPU
- RAM
- Storage (R)
- Storage (W)
- PSU

Each enabled phase has a duration field in minutes.

Additional phase-specific inputs:
- GPU: Res
- RAM: Cap (GB)
- Storage read/write: Drive selector

Phase behavior:
- CPU runs a y-cruncher CPU stress profile with fixed 2 GB engine memory usage.
- RAM runs a y-cruncher high-memory profile using the editable RAM amount.
- GPU runs FurMark at the selected resolution.
- Storage read/write runs DiskSpd against the selected drive.
- PSU runs y-cruncher and FurMark together for combined system load.

RAM field behavior:
- The RAM value is prefilled from currently available memory.
- You may edit it manually.
- The app blocks start if the RAM value is non-numeric, zero, or too large for the current system.

Preset manager:
- Preset dropdown
- Save As
- Delete
- Load Current
- Reset

Stability Certificate area:
- Generate certificate at the completion of the tests
- Logo path field
- Browse button
- Customer Name checkbox and input
- Machine Name checkbox and input

Start control:
- START

Extra launch behavior:
- FurMark-based GPU and PSU runs above 5 minutes trigger a warning prompt before launch.
- If the previous run ended in a system crash, the app shows a crash-recovery warning on startup.

Assign Sensors Window
The Assign Sensors window is opened from the setup screen.

It is used to bind detected LibreHardwareMonitor sensors to the app's fixed telemetry channels:
- CPU Temperature
- GPU Temperature
- CPU Load
- GPU Load
- RAM Load
- Storage Load
- CPU Power
- GPU Power

Available actions:
- Refresh Sensors
- Install PawnIO Driver, when an installer is bundled and the driver is needed

Telemetry notes:
- Run the app as Administrator.
- Sensor assignments are saved in settings.json.
- The app warns when mappings are missing, invalid, or returning no live data.

Thermal Settings Window
The Thermal Settings window is opened from the setup screen.

It lets you edit the active abort thresholds for:
- CPU
- GPU

The limits can only be lowered: the built-in safety ceilings (CPU 105 C, GPU 95 C) cannot be exceeded.

If a running phase exceeds the configured thermal limit, the app stops the active engine processes and
records a thermal abort. If the CPU or GPU temperature sensor is not assigned, the app warns before
starting because the thermal kill switch cannot protect that component.

Screen 2 - Diagnostics Dashboard
The dashboard appears after you press START.

Header area:
- Current phase label, for example Phase 2/4: RAM Stress
- Error indicator when issues are detected
- Phase Remaining timer
- Matrix Remaining timer

Live Telemetry area:
- CPU Temperature
- CPU Load
- GPU Temperature
- GPU Load
- RAM Load
- Storage Load
- CPU Power
- GPU Power
- Total Power

Live Engine Console:
- Shows the live engine transcript while a phase is running
- Includes stdout and stderr
- Alerts are highlighted when engine output indicates a problem

Completed Test Logs area:
- Finished phases remain available during the run and after the run ends
- Each completed entry can be opened without leaving the app
- The log viewer shows:
  - engine
  - status
  - exit code
  - runtime
  - started time
  - finished time
  - full command line
  - canonical app-captured transcript
- Log viewer actions:
  - Copy Log
  - Open Log Folder
  - Open Native Log for y-cruncher phases when a native engine log exists

Dashboard actions:
- Abort Session
- Refresh Review
- Back To Setup

End-of-run behavior:
- The verdict shown on the dashboard is either CERTIFIED STABLE or UNSTABLE.
- Completed phase logs remain browsable after the run finishes.
- The app shows the saved PDF path when report generation is enabled.

Logging And Artifacts
Each run creates a unique folder:
results\<run_id>\

Files written by the app:
- results\<run_id>\run_summary.json
- results\<run_id>\logs\engine_meta.json
- results\<run_id>\logs\cpu_stdout.log
- results\<run_id>\logs\ram_stdout.log
- results\<run_id>\logs\gpu_stdout.log
- results\<run_id>\logs\storage_read_stdout.log
- results\<run_id>\logs\storage_write_stdout.log
- results\<run_id>\logs\psu_stdout.log

Additional y-cruncher native logs, when those phases run:
- results\<run_id>\logs\cpu_ycruncher.log
- results\<run_id>\logs\ram_ycruncher.log
- results\<run_id>\logs\psu_ycruncher.log

PDF report location:
- reports\*.pdf

Notes:
- The app-captured *_stdout.log files are the primary execution transcripts used by the UI.
- stderr is preserved in the same app log with a [STDERR] prefix.
- engine_meta.json stores per-phase metadata such as status, exit code, timestamps, command line, and log paths.
- run_summary.json stores the final run verdict and the executed test summary.
- test_state.json is used internally for crash recovery detection between launches.

Certificate And Branding
If report generation is enabled, the application can create a PDF System Stability Certificate.

The certificate can include:
- Selected logo image
- Optional customer name
- Optional machine name
- Completion timestamp
- Overall verdict
- Test summary
- Maximum observed telemetry
- Recorded issues, when present

Logo usage:
- Put .png, .jpg, or .jpeg files into Logotypes\
- Use Browse in the setup screen to select the logo
- The selected path is stored in settings.json

If the selected logo file no longer exists, the PDF is still generated without the image.

Deploying To Another Machine
Before extracting a downloaded archive on the target PC:
1. Right-click the downloaded ZIP or archive.
2. Open Properties.
3. Enable Unblock if the option is shown.
4. Click Apply and OK.
5. Extract the archive only after that.

If you skip this step, Windows may block extracted files and cause issues with the app.

1. Copy the entire dist\RWS_StressTester\ folder to the target PC.
2. Keep the folder structure unchanged.
3. Add any certificate logos to Logotypes\ if needed.
4. Right-click RWS_StressTester.exe and choose Run as administrator.

Troubleshooting
Application does not start:
- Confirm RWS_StressTester.exe is still inside the full packaged folder.
- Confirm Engines\ exists next to the executable.
- If the app was downloaded as an archive, unblock the archive in Properties and extract it again.

Telemetry is missing or shows Unassigned:
- Run the app as Administrator.
- Open Assign Sensors and bind the required telemetry channels.
- If CPU temperature sensors exist but do not return live values, install the PawnIO driver if provided.

Thermal abort occurred:
- Review the live console and the saved phase log.
- Check the configured limits in Thermal Settings.

No PDF or missing logo:
- Confirm Generate certificate at the completion of the tests is enabled.
- Confirm the selected image still exists.
- Re-select the logo with Browse if the saved path is outdated.

Logs or reports are not appearing:
- Confirm the app can write to the packaged folder location.
- Avoid read-only folders or locked-down locations.

Operational Notes
- Manual abort stops the session and does not generate the final report.
- Anti-virus software may flag PyInstaller-built executables; add an exclusion if required.
- Do not modify or remove files inside Engines\ unless you intend to update the bundled test engines.

End of README
