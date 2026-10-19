"""CI check: LibreHardwareMonitor must load even when its files carry Mark of the Web.

The workflow tags every file under Engines/LibreHardwareMonitor with a Zone.Identifier
stream first, the way Explorer does when extracting a browser-downloaded zip.

    python check_lhm_motw.py repro   # loading the marked DLL directly must fail
    python check_lhm_motw.py fixed   # TelemetryMonitor must still load it

Each mode runs in its own process so a failed load cannot affect the other.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

ENGINES = ROOT / "Engines"
LHM_DIR = ENGINES / "LibreHardwareMonitor"


def repro() -> None:
    if not os.path.exists(f"{LHM_DIR / 'LibreHardwareMonitorLib.dll'}:Zone.Identifier"):
        sys.exit("source DLL is not marked; the check would prove nothing")

    import pythonnet

    pythonnet.load("netfx")
    import clr

    sys.path.append(str(LHM_DIR))
    try:
        clr.AddReference("LibreHardwareMonitorLib")
    except Exception as exc:
        print(f"repro ok: marked DLL rejected as expected ({type(exc).__name__})")
        return
    sys.exit("marked DLL loaded directly; the Mark of the Web failure did not reproduce")


def fixed() -> None:
    from telemetry import TelemetryMonitor

    monitor = TelemetryMonitor(ENGINES)
    hardware = monitor._load_hardware_module()
    staged = monitor._staged_library_dir / monitor.lhm_library_name
    if os.path.exists(f"{staged}:Zone.Identifier"):
        sys.exit(f"staged DLL still carries Zone.Identifier: {staged}")
    print(f"fix ok: loaded {hardware.Computer} from {staged}")


if __name__ == "__main__":
    {"repro": repro, "fixed": fixed}[sys.argv[1]]()
