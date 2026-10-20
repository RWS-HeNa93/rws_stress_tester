"""CI check: the sensor session can be rebuilt inside a running process.

The app does this when a PawnIO driver is installed while it runs, so LibreHardwareMonitor
loads the driver's modules without an app restart. Closing and opening a Computer again
must work and find the same sensors (Open/Close share static state in the library).

    python check_telemetry_reopen.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from telemetry import TelemetryMonitor, auto_assign_sensors  # noqa: E402


def sensor_dicts(monitor: TelemetryMonitor) -> list[dict]:
    return [
        {
            "sensor_id": sensor.sensor_id,
            "sensor_name": sensor.sensor_name,
            "sensor_type": sensor.sensor_type,
            "hardware_name": sensor.hardware_name,
            "hardware_type": sensor.hardware_type,
            "hardware_role": sensor.hardware_role,
            "value": sensor.value,
        }
        for sensor in monitor.list_available_sensors()
    ]


def main() -> None:
    monitor = TelemetryMonitor(ROOT / "Engines")
    before = sensor_dicts(monitor)
    if not before:
        sys.exit("no sensors found on the first scan")

    monitor.reopen()
    after = sensor_dicts(monitor)
    missing = {sensor["sensor_id"] for sensor in before} - {sensor["sensor_id"] for sensor in after}
    if missing:
        sys.exit(f"sensors lost after reopening: {sorted(missing)}")

    print(f"reopen ok: {len(after)} sensors before and after")
    print(f"auto-assignment on this runner: {auto_assign_sensors(after)}")
    monitor.close()


if __name__ == "__main__":
    main()
