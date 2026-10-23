RWS Stress Tester - Operator Guide
==================================

RWS Stress Tester runs hardware stress tests on a Windows PC, watches temperatures and errors
while they run, and produces a PDF System Stability Certificate. It controls these bundled tools:
- y-cruncher        CPU, RAM and PSU load (checks its own results)
- FurMark           GPU and video memory (VRAM) load, with an artifact scanner
- DiskSpd           storage load
- LibreHardwareMonitor   temperature, load, clock and power readings

If every selected test passes, the verdict is CERTIFIED STABLE, whatever the test durations.
Choosing sensible durations is up to the technician.

WARNING: stress tests push hardware to its thermal and power limits. Run them only on machines
you are responsible for, with working cooling. The software is provided as is, without warranty
of any kind (see LICENSE.txt).


QUICK START
-----------
1. If you received this as a ZIP download: right-click the ZIP > Properties > tick Unblock >
   OK, then extract it. (Otherwise Windows may block the bundled engines.)
2. Keep the whole folder together. RWS_StressTester.exe needs the Engines\ folder next to it.
3. Right-click RWS_StressTester.exe > Run as administrator.
4. First time on a machine: the app offers to install the PawnIO driver if it is missing
   (needed for CPU temperature; no restart needed, the running app picks it up). It then
   assigns the sensors automatically and opens Review Sensor Assignment: check each reading
   shows the right device and a live value, fix any that look wrong, and click Approve.
   CPU Temperature and GPU Temperature must be assigned, or the thermal kill switch cannot
   protect the hardware.
5. Switch on the tests to run, set minutes for each, and press Start diagnostics.
6. When the run ends, read the verdict, open any failed phase's log, and collect the PDF
   from the reports\ folder.


THE TESTS
---------
Phases always run in this order: CPU, RAM, GPU, VRAM, Storage (R), Storage (W), PSU.
Each card has Time (min) plus the fields below.

CPU
  y-cruncher with 2 GB of memory. Tests, most valuable first:
  SFTv4, BKT, VT3, SNT, SVT, N63. Covers peak boost clocks, maximum heat and power, larger
  working sets, and the load changes between tests. Finds unstable core voltage, boost and
  Curve Optimizer / undervolt settings.

RAM  - Fill to (%) and Safety margin (GB): how full the memory gets during the test
  y-cruncher tests VT3, FFTv4, N63 with enough memory to bring the whole system's memory
  use to the Fill to percentage (default 90%), while always leaving at least the safety
  margin free for Windows and this app (default 2 GB). The amount is worked out when the
  phase starts, from what is in use at that moment, and shown in the console.
  The safety margin wins when the two disagree: on a 16 GB PC with a 2 GB margin, memory
  stops at about 88%. Finds unstable RAM, memory controller and fabric settings
  (XMP/EXPO, timings, SoC voltage). Start is blocked if Fill to is not 10-100%, the margin
  is below 0.5 GB, or less than 1 GB would be left to test. If other programs use up the
  memory before the phase starts, the phase fails with a note saying so.

GPU  - Resolution: render resolution, e.g. 1280x720 (also used by VRAM and PSU)
  FurMark at full load with its artifact scanner, which checks every frame for corruption.

VRAM - GB: how much video memory to fill
  The same FurMark load while filling the chosen amount of video memory with data that is
  read back every frame. Faulty or overheating video memory shows up as artifacts or as a
  driver crash or reset.
  Size is prefilled with the largest size that leaves at least 1 GB of the card's memory free.
  Choosing more asks for confirmation: data that does not fit spills into system RAM, which
  then gets tested instead. Integrated graphics have no separate video memory, so on those
  this phase mostly tests the system RAM shared with the GPU.

Storage (R) / Storage (W) - Drive: the drive to test
  DiskSpd random 64 KB reads or writes, 4 threads x 32 outstanding I/Os, on a test file of up
  to 8 GB (at most 20% of the free space). Writes use random data that cannot be compressed.
  The test file is deleted afterwards.

PSU
  y-cruncher's maximum-power tests (SFTv4, SNT, SVT) and FurMark at the same time, for the
  highest combined power draw.

Short tests run fewer y-cruncher tests
  Each y-cruncher test needs a minimum time to be useful: CPU and PSU 1 minute, RAM 2 minutes.
  Short phases keep only the most valuable tests that fit:
    CPU: 1 min = SFTv4 only, 6 min or more = all six
    RAM: under 4 min = VT3 only (it uses all the memory chosen), 4-5 min = VT3 and FFTv4,
         6 min or more = all three
  The time is shared evenly between the chosen tests (at most 5 minutes each, then they repeat).

End of a phase
  When the time is up, the timer shows FINISHING while the engines get a short grace period to
  exit on their own (y-cruncher 10 s, FurMark 30 s, DiskSpd up to 120 s) before being stopped.

Warnings before the run starts
  - GPU, VRAM or PSU longer than 5 minutes: FurMark is extremely heavy; confirm to continue.
  - CPU or GPU temperature sensor not assigned: the thermal kill switch cannot protect it.


WHAT FAILS A PHASE
------------------
A phase fails on any of the following. The reason is shown in the console, written to the
phase log and listed on the certificate.

  What happened                                Where to look
  -------------------------------------------  ------------------------------------------------
  y-cruncher reports a wrong result            CPU (cpu/psu phase) or RAM/memory controller
                                               (ram phase): overclock, undervolt, XMP/EXPO,
                                               voltages, cooling
  FurMark artifact found                       GPU core (gpu/psu) or video memory (vram):
                                               overclock, cooling, failing card
  Uncorrected WHEA hardware error in the       CPU, RAM or PCIe instability: overclock,
  event log                                    undervolt, XMP/EXPO, but also drivers or BIOS
                                               settings
  Display driver reset (TDR) during a GPU,     GPU instability, power delivery, but also the
  VRAM or PSU phase                            graphics driver
  DiskSpd reports an I/O error, or a single    Drive, cable, controller or failing media
  storage I/O takes 10 s or longer
  An engine crashes or quits early             The message gives the exit code in plain words;
                                               crashes under load usually mean instability
  Any other error line in an engine's output   The line, in red in the phase log, names the
  (error, fault, corruption, mismatch...)      problem
  Temperature above the thermal limit          Cooling. The run stops (THERMAL_ABORT)
  The PC restarts or loses power               Shown on the next launch as FAILED (System
                                               Crash), see below

If an engine keeps printing errors, the first 10 error lines per phase are listed and the rest
are counted; every one of them stays in red in the console and the phase log.

Event log monitoring
  During every phase the app checks the Windows System event log every 30 seconds and at the
  end of the phase. Each event is reported once, and only events logged after the run started
  count, even if the PC clock is wrong or changes. If the event log cannot be read, the run
  continues and the log says so, and the certificate does not claim the event log was
  monitored.

  What Windows logs falls into two groups:
  - Failures (red): an uncorrected WHEA hardware error, or a display driver reset (TDR) while
    the phase loads the GPU. These fail the phase and are listed on the certificate. They show
    the system was unstable, which can come from the hardware or from software such as
    drivers or BIOS settings.
  - Warnings worth checking (yellow): corrected WHEA errors, a TDR outside a GPU phase,
    NVIDIA/AMD driver errors, disk / NVMe / SATA errors, resets or retries, and file system
    errors. They are shown in yellow in the console and the phase log and saved under
    "warnings" in run_summary.json. They do not fail the phase, change the verdict or appear
    on the certificate: they are not evidence of a failure, only something the technician
    may want to look into, e.g. a misconfigured BIOS power setting or a flaky USB drive.
    Switch them off with "Show warnings worth checking" on the setup screen; failures are
    always checked.
  If a machine logs the same event constantly, the first 10 failures and 10 warnings per phase
  are listed with a count of the rest; see Event Viewer > Windows Logs > System for all of them.

After a crash or power loss
  On the next launch the app marks the interrupted phase FAILED (System Crash) and shows what
  Windows recorded since that phase started: blue screen code, unexpected power loss,
  uncorrected WHEA errors, followed by any warnings worth checking. A reconstructed
  run_summary.json is saved in that run's folder.


SCREENS AND WINDOWS
-------------------
Setup screen
  - Thermal settings and Assign sensors buttons (see below), with the sensor status next to them.
  - Retro / Modern: switches the look between the green terminal skin (default) and a neutral
    modern one. The choice is saved in settings.json; it cannot be changed during a run.
  - Test phases: one row per test. Switch a row on to include it; the total time is shown
    at the top of the list.
  - Presets: save the current setup under a name (Save as), re-apply the selected preset
    (Load), Delete it, or Reset to defaults. Presets are saved in presets.json.
  - Certificate: create a PDF certificate when the run ends, logo (from Logotypes\),
    optional customer name and machine name.
  - Windows event log: "Show warnings worth checking" turns the yellow event log warnings on
    or off (see Event log monitoring). Saved in settings.json.
  - Start diagnostics.

Assign Sensors
  Links the hardware sensors found on this PC to the app's readings: CPU temperature, load,
  clock and power; GPU temperature, load, core clock, memory clock and power; RAM load;
  storage load. Saved in settings.json.
  - Each reading shows the value its sensor read at the last scan.
  - Refresh sensors re-scans the hardware.
  - Auto-assign picks a sensor for every reading again (discrete GPU before integrated,
    system drive first).
  - Install PawnIO driver runs the bundled installer. The app watches for the driver and, once
    it is running, re-scans the sensors and asks you to review the assignment again.
  - Until an assignment is approved, it is made automatically and shown for review at every
    launch. It is also shown once more on the first launch with PawnIO running if the last
    approval was made without it. Readings added by an app update are assigned automatically;
    approved ones are kept.
  - The app warns when assignments are missing, invalid or return no live data.

Thermal Settings
  The CPU and GPU temperatures at which a running test is stopped. They can only be lowered:
  the built-in ceilings (CPU 105 C, GPU 95 C) cannot be exceeded.

Dashboard (after Start diagnostics)
  - Current phase, issue indicator, time left in the phase and in the whole run, and a
    progress bar for the phase.
  - Live telemetry, one card per device, each naming the hardware its sensors read:
      CPU      temperature, load, clock, power
      GPU      temperature, load, core clock, memory clock, power
      RAM      total load
      Storage  activity
    Temperature bars turn amber within 15 C of the thermal limit and red within 5 C.
  - Live console with the engines' output; problems are highlighted.
  - Completed phases: click a finished phase to see its status, exit code, run time,
    start/finish times, command line and full log. Copy log, Open log folder, and Open engine
    log for the engine's own log (y-cruncher, FurMark).
  - Abort session stops the run; no certificate is made. Refresh reloads the completed
    phases. Back to setup is available once the run has ended.
  - At the end the verdict is shown: CERTIFIED STABLE or UNSTABLE.


FILES
-----
Folder layout
  RWS_StressTester.exe
  README.txt          this guide
  LICENSE.txt         license for RWS Stress Tester's own code (MIT)
  THIRD_PARTY_NOTICES.md   licenses of the bundled tools and libraries
  licenses\           third-party license texts
  settings.json       logo, certificate details, sensor assignments, thermal limits, skin,
                      event log warnings on/off
  presets.json        saved presets
  Engines\            the test tools - do not change or remove
  Logotypes\          put certificate logos (.png, .jpg) here
  results\            one folder per run (created by the app)
  reports\            PDF certificates (created by the app)

Each run: results\<run_id>\  (run_id is the start date and time, e.g. 20261019_181729)
  run_summary.json            verdict, issues, warnings worth checking and per-phase results
  telemetry_summary.json      maximum temperatures and loads, per phase
  logs\engine_meta.json       per-phase status, exit code, times, command line, log paths
  logs\<phase>_stdout.log     the main log of each phase (engine output, stderr marked
                              [STDERR], app messages marked [APP])
  logs\<phase>_ycruncher.log  y-cruncher's own log (cpu, ram, psu)
  logs\<phase>_furmark.log    FurMark's own log (gpu, vram, psu)
  <phase> is cpu, ram, gpu, vram, storage_read, storage_write or psu.

test_state.json appears in the main folder only while a test is running; the app uses it to
detect a crash on the next launch.


TROUBLESHOOTING
---------------
The app or an engine does not start
  - Check that Engines\ is next to RWS_StressTester.exe.
  - If the package came as a ZIP, delete the folder, unblock the ZIP (Quick Start, step 1)
    and extract it again.
  - Anti-virus can flag apps built with PyInstaller; add an exclusion for this folder.

Telemetry shows nothing or Unassigned
  - Run as administrator.
  - Open Assign sensors and assign the readings. Assignments are per machine type.
  - If CPU temperature or clock sensors exist but read nothing, use Install PawnIO driver.
  - If they still read nothing after the driver is installed, the app offers to restart itself;
    if that does not help, restart Windows.

"Windows event log check unavailable" in the log
  - The run still works, but Windows-logged hardware errors are not checked. Run as
    administrator; if it persists, check those errors manually in Event Viewer.

A phase failed
  - Open it under Completed phases; the reasons are listed at the top of the log viewer
    and in the [APP] lines at the end of the log.
  - Use the table in WHAT FAILS A PHASE to see where to look.

No PDF or no logo on it
  - Check that "Create PDF certificate" is ticked.
  - Re-select the logo with Browse if the file was moved.
  - Aborted runs never produce a certificate.

Logs or reports are not saved
  - The folder must be writable. Do not run it from a read-only location such as a
    write-protected USB stick or a protected system folder.


LICENSES
--------
RWS Stress Tester's own code is released under the MIT License (LICENSE.txt). The bundled
tools keep their own licenses, which are listed in THIRD_PARTY_NOTICES.md:
  y-cruncher            free for non-commercial use; commercial use needs the developer's
                        permission (http://www.numberworld.org/y-cruncher/license.html)
  FurMark 2             freeware by Geeks3D (https://www.geeks3d.com/furmark/)
  DiskSpd               Microsoft Software License Terms (Engines\DiskSpd\EULA)
  LibreHardwareMonitor  Mozilla Public License 2.0
  PawnIO                GNU GPL 2.0 or later
