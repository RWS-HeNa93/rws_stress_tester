from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


CPU_TEMP_LIMIT_C = 105.0
GPU_TEMP_LIMIT_C = 95.0

SENSOR_TYPE_TEMPERATURE = "Temperature"
SENSOR_TYPE_LOAD = "Load"
SENSOR_TYPE_POWER = "Power"
SENSOR_TYPE_CLOCK = "Clock"


@dataclass(frozen=True)
class TelemetryCategory:
    key: str
    label: str
    sensor_type: str
    hardware_roles: tuple[str, ...]
    unit: str


@dataclass(frozen=True)
class TelemetrySensor:
    sensor_id: str
    sensor_name: str
    sensor_type: str
    hardware_name: str
    hardware_type: str
    hardware_role: str
    value: float | None = None

    @property
    def display_name(self) -> str:
        hardware_label = self.hardware_name or self.hardware_type or "Unknown Hardware"
        return f"{hardware_label} | {self.sensor_name}"


# Grouped by device: the sensor window lists them in this order.
TELEMETRY_CATEGORIES: tuple[TelemetryCategory, ...] = (
    TelemetryCategory("cpu_temp", "CPU Temperature", SENSOR_TYPE_TEMPERATURE, ("cpu",), " C"),
    TelemetryCategory("cpu_load", "CPU Load", SENSOR_TYPE_LOAD, ("cpu",), "%"),
    TelemetryCategory("cpu_clock", "CPU Clock", SENSOR_TYPE_CLOCK, ("cpu",), " MHz"),
    TelemetryCategory("cpu_power", "CPU Power", SENSOR_TYPE_POWER, ("cpu",), " W"),
    TelemetryCategory("gpu_temp", "GPU Temperature", SENSOR_TYPE_TEMPERATURE, ("gpu",), " C"),
    TelemetryCategory("gpu_load", "GPU Load", SENSOR_TYPE_LOAD, ("gpu",), "%"),
    TelemetryCategory("gpu_clock", "GPU Core Clock", SENSOR_TYPE_CLOCK, ("gpu",), " MHz"),
    TelemetryCategory("vram_clock", "GPU Memory Clock", SENSOR_TYPE_CLOCK, ("gpu",), " MHz"),
    TelemetryCategory("gpu_power", "GPU Power", SENSOR_TYPE_POWER, ("gpu",), " W"),
    TelemetryCategory("ram_load", "RAM Load", SENSOR_TYPE_LOAD, ("ram",), "%"),
    TelemetryCategory("storage_load", "Storage Load", SENSOR_TYPE_LOAD, ("storage",), "%"),
)
TELEMETRY_CATEGORY_MAP = {category.key: category for category in TELEMETRY_CATEGORIES}
DEFAULT_TELEMETRY_MAPPINGS = {category.key: None for category in TELEMETRY_CATEGORIES}


# LibreHardwareMonitor sensor names to pick automatically for each reading, best first.
# Matching is exact and ignores case; a reading with no matching sensor stays unassigned.
AUTO_ASSIGN_SENSOR_NAMES: dict[str, tuple[str, ...]] = {
    "cpu_temp": (
        "CPU Package", "Core (Tctl/Tdie)", "Core (Tdie)", "Core (Tctl)", "CCDs Max (Tdie)", "Core Max", "CPU Cores", "Core Average",
    ),
    "gpu_temp": ("GPU Core", "GPU Hot Spot"),
    "cpu_load": ("CPU Total",),
    # Per-core clocks are a fallback for CPUs without the average sensor.
    "cpu_clock": ("Cores (Average)", "Cores (Average Effective)", "CPU Core #1", "P-Core #1", "Core #1", "CPU Core"),
    "gpu_load": ("GPU Core", "GPU Render/Compute", "D3D 3D"),
    "gpu_clock": ("GPU Core", "GPU Shader"),
    "vram_clock": ("GPU Memory",),
    "ram_load": ("Memory",),
    "storage_load": ("Total Activity",),
    "gpu_power": ("GPU Package", "GPU Power", "GPU PPT", "GPU Total", "GPU Core"),
    "cpu_power": ("CPU Package", "Package", "CPU Cores"),
}
# Hardware never picked automatically: page-file usage is not RAM load.
AUTO_ASSIGN_EXCLUDED_HARDWARE = ("virtual memory",)
GPU_VENDOR_RANK = {"gpunvidia": 3, "gpuamd": 2, "gpuintel": 1}
PLAUSIBLE_TEMPERATURE_C = (1.0, 150.0)

PAWNIO_UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"
PAWNIO_DEVICE_PATH = r"\\?\GLOBALROOT\Device\PawnIO"


@dataclass
class TelemetrySample:
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    cpu_load: float | None = None
    gpu_load: float | None = None
    ram_load: float | None = None
    storage_load: float | None = None
    gpu_power: float | None = None
    cpu_power: float | None = None
    cpu_clock: float | None = None
    gpu_clock: float | None = None
    vram_clock: float | None = None
    timestamp: float = field(default_factory=time.time)


class TelemetryMonitor:
    def __init__(self, engines_root: Path) -> None:
        self.engines_root = engines_root
        self.lhm_source_dir = engines_root / "LibreHardwareMonitor"
        self.lhm_library_name = "LibreHardwareMonitorLib.dll"
        self._computer: Any = None
        self._hardware_module: Any = None
        self._dll_directory_handle: Any = None
        self._staged_library_dir: Path | None = None
        self._telemetry_lock = threading.RLock()
        self._sensor_mappings: dict[str, str | None] = DEFAULT_TELEMETRY_MAPPINGS.copy()
        self._last_sensor_inventory: dict[str, TelemetrySensor] = {}

    def close(self) -> None:
        with self._telemetry_lock:
            computer = self._computer
            self._computer = None
            dll_directory_handle = self._dll_directory_handle
            self._dll_directory_handle = None

            if computer is not None:
                try:
                    computer.Close()
                except Exception:
                    pass

            if dll_directory_handle is not None:
                try:
                    dll_directory_handle.close()
                except Exception:
                    pass

    def set_sensor_mappings(self, mappings: dict[str, Any] | None) -> dict[str, str | None]:
        normalized = DEFAULT_TELEMETRY_MAPPINGS.copy()
        if isinstance(mappings, dict):
            for key in normalized:
                value = mappings.get(key)
                normalized[key] = str(value).strip() if value else None

        with self._telemetry_lock:
            self._sensor_mappings = normalized
        return normalized.copy()

    def get_sensor_mappings(self) -> dict[str, str | None]:
        with self._telemetry_lock:
            return self._sensor_mappings.copy()

    def mapped_hardware_name(self, category_key: str) -> str:
        """Name of the hardware behind the sensor assigned to a reading, as of the last poll."""
        # No lock: both dicts are replaced wholesale, never mutated, and a poll can hold the lock for a while.
        sensor_id = self._sensor_mappings.get(category_key)
        sensor = self._last_sensor_inventory.get(sensor_id) if sensor_id else None
        return sensor.hardware_name if sensor else ""

    def list_available_sensors(self) -> list[TelemetrySensor]:
        with self._telemetry_lock:
            self._open_computer()
            inventory = self._collect_sensor_inventory()
            self._last_sensor_inventory = inventory
            return sorted(inventory.values(), key=lambda sensor: (sensor.sensor_type, sensor.hardware_name, sensor.sensor_name))

    def validate_sensor_mappings(self, mappings: dict[str, Any] | None = None) -> dict[str, list[str]]:
        with self._telemetry_lock:
            self._open_computer()
            inventory = self._collect_sensor_inventory()
            self._last_sensor_inventory = inventory
            effective_mappings = self._normalize_mappings(mappings if mappings is not None else self._sensor_mappings)

        unassigned: list[str] = []
        invalid: list[str] = []
        valid: list[str] = []

        for category in TELEMETRY_CATEGORIES:
            sensor_id = effective_mappings.get(category.key)
            if not sensor_id:
                unassigned.append(category.label)
                continue

            sensor = inventory.get(sensor_id)
            if (
                sensor is None
                or sensor.sensor_type != category.sensor_type
                or sensor.hardware_role not in category.hardware_roles
            ):
                invalid.append(category.label)
                continue

            valid.append(category.label)

        return {"valid": valid, "invalid": invalid, "unassigned": unassigned}

    def get_pawnio_status(self) -> dict[str, Any]:
        return detect_pawnio_status()

    def reopen(self) -> None:
        """Close the LibreHardwareMonitor session so the next read opens a fresh one.

        Hardware objects load their PawnIO modules when the session opens, so a driver
        installed after that stays unused until the session is rebuilt.
        """
        with self._telemetry_lock:
            computer = self._computer
            self._computer = None
            self._last_sensor_inventory = {}
            # Close under the lock: Computer.Open/Close share static mutexes, so a new
            # session must not open while the old one is still closing.
            if computer is not None:
                try:
                    computer.Close()
                except Exception:
                    pass

    def sample(self) -> TelemetrySample:
        with self._telemetry_lock:
            self._open_computer()
            inventory = self._collect_sensor_inventory()
            self._last_sensor_inventory = inventory
            values: dict[str, float | None] = {}

            for category in TELEMETRY_CATEGORIES:
                sensor_id = self._sensor_mappings.get(category.key)
                sensor = inventory.get(sensor_id) if sensor_id else None
                if (
                    sensor is None
                    or sensor.sensor_type != category.sensor_type
                    or sensor.hardware_role not in category.hardware_roles
                ):
                    values[category.key] = None
                    continue
                values[category.key] = sensor.value

            return TelemetrySample(**values)

    def run_polling_loop(
        self,
        stop_event: threading.Event,
        callback: Callable[[TelemetrySample], None],
        error_callback: Callable[[Exception], None] | None = None,
        interval_seconds: float = 2.0,
    ) -> None:
        try:
            with self._telemetry_lock:
                self._open_computer()

            while not stop_event.is_set():
                callback(self.sample())
                stop_event.wait(interval_seconds)
        except Exception as exc:
            if error_callback is not None:
                error_callback(exc)
                return
            raise
        finally:
            self.close()

    def _open_computer(self) -> None:
        if self._computer is not None:
            return

        hardware_module = self._load_hardware_module()
        computer = hardware_module.Computer()
        computer.IsCpuEnabled = True
        computer.IsGpuEnabled = True
        computer.IsMemoryEnabled = True
        computer.IsPowerMonitorEnabled = True
        computer.IsMotherboardEnabled = True
        computer.IsPsuEnabled = True
        computer.IsStorageEnabled = True
        computer.Open()
        self._computer = computer

    def _load_hardware_module(self):
        if self._hardware_module is not None:
            return self._hardware_module

        staged_library = self._stage_lhm_binaries()

        try:
            import pythonnet
        except ImportError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError("The 'pythonnet' package is required for LibreHardwareMonitor telemetry.") from exc

        if hasattr(pythonnet, "load"):
            try:
                pythonnet.load("netfx")
            except RuntimeError:
                pass

        try:
            import clr
        except ImportError as exc:  # pragma: no cover - depends on local environment
            raise RuntimeError("pythonnet is installed, but the 'clr' module could not be imported.") from exc

        library_dir = str(staged_library.parent.resolve())
        if library_dir not in sys.path:
            sys.path.append(library_dir)

        if self._dll_directory_handle is None and hasattr(os, "add_dll_directory"):
            self._dll_directory_handle = os.add_dll_directory(library_dir)

        clr.AddReference(staged_library.stem)
        from LibreHardwareMonitor import Hardware

        self._hardware_module = Hardware
        return self._hardware_module

    def _stage_lhm_binaries(self) -> Path:
        source_library = self.lhm_source_dir / self.lhm_library_name

        if not source_library.exists():
            raise RuntimeError(f"LibreHardwareMonitor DLL not found at {source_library}")

        if self._staged_library_dir is not None:
            return self._staged_library_dir / self.lhm_library_name

        stage_candidates: list[Path] = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            stage_candidates.append(Path(local_app_data) / "StressTester" / "LibreHardwareMonitor")
        stage_candidates.append(Path(tempfile.gettempdir()) / "StressTester" / "LibreHardwareMonitor")

        last_error: Exception | None = None
        for candidate_dir in stage_candidates:
            staged_library = candidate_dir / self.lhm_library_name
            try:
                candidate_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(self.lhm_source_dir, candidate_dir, dirs_exist_ok=True)
                self._strip_mark_of_the_web(candidate_dir)
                self._staged_library_dir = candidate_dir
                return staged_library
            except OSError as exc:
                last_error = exc

        raise RuntimeError(f"Unable to stage LibreHardwareMonitor binaries locally. Last error: {last_error}")

    @staticmethod
    def _strip_mark_of_the_web(directory: Path) -> None:
        """Remove the Zone.Identifier stream Windows attaches to files unzipped from a download.

        .NET Framework refuses to load an assembly carrying it (HRESULT 0x80131515,
        "load an assembly from a network location"), and copying the files keeps the stream.
        """
        if os.name != "nt":
            return

        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            try:
                os.remove(f"{path}:Zone.Identifier")
            except OSError:
                pass

    def _collect_sensor_inventory(self) -> dict[str, TelemetrySensor]:
        if self._computer is None:
            raise RuntimeError("LibreHardwareMonitor has not been initialized.")

        inventory: dict[str, TelemetrySensor] = {}
        for hardware in self._iter_hardware(self._computer.Hardware):
            hardware_name = str(getattr(hardware, "Name", ""))
            hardware_type = str(getattr(hardware, "HardwareType", ""))
            hardware_role = self._classify_hardware_role(hardware_type)
            for sensor in hardware.Sensors:
                sensor_type = str(getattr(sensor, "SensorType", ""))
                sensor_id = self._get_sensor_id(sensor, hardware_name, hardware_type)
                inventory[sensor_id] = TelemetrySensor(
                    sensor_id=sensor_id,
                    sensor_name=str(getattr(sensor, "Name", "")),
                    sensor_type=sensor_type,
                    hardware_name=hardware_name,
                    hardware_type=hardware_type,
                    hardware_role=hardware_role,
                    value=self._read_sensor_value(sensor),
                )
        return inventory

    def _iter_hardware(self, hardware_items):
        for hardware in hardware_items:
            hardware.Update()
            yield hardware
            yield from self._iter_hardware(hardware.SubHardware)

    @staticmethod
    def _get_sensor_id(sensor: Any, hardware_name: str, hardware_type: str) -> str:
        identifier = getattr(sensor, "Identifier", None)
        if identifier is not None:
            identifier_text = str(identifier).strip()
            if identifier_text:
                return identifier_text

        sensor_type = str(getattr(sensor, "SensorType", "")).strip()
        sensor_name = str(getattr(sensor, "Name", "")).strip()
        return "|".join(part for part in (hardware_type, hardware_name, sensor_type, sensor_name) if part)

    @staticmethod
    def _classify_hardware_role(hardware_type: str) -> str:
        normalized = hardware_type.strip().lower()
        if normalized == "cpu":
            return "cpu"
        if normalized in {"gpunvidia", "gpuamd", "gpuintel"}:
            return "gpu"
        if normalized == "memory":
            return "ram"
        if normalized in {"storage", "hdd", "ssd"}:
            return "storage"
        return "other"

    @staticmethod
    def _normalize_mappings(mappings: dict[str, Any] | None) -> dict[str, str | None]:
        normalized = DEFAULT_TELEMETRY_MAPPINGS.copy()
        if not isinstance(mappings, dict):
            return normalized

        for key in normalized:
            value = mappings.get(key)
            normalized[key] = str(value).strip() if value else None
        return normalized

    @staticmethod
    def _read_sensor_value(sensor: Any) -> float | None:
        try:
            value = getattr(sensor, "Value", None)
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None


def detect_pawnio_status() -> dict[str, Any]:
    """Check the PawnIO driver directly, fresh on every call.

    LibreHardwareMonitor's PawnIo.IsInstalled is read once per process, so it keeps
    reporting a driver installed while the app runs as missing.
    "is_running" is whether the driver's device exists, i.e. sensors can use it now.
    """
    if os.name != "nt":
        return {"is_installed": None, "is_running": None, "version": "", "error": "PawnIO is only available on Windows."}
    try:
        version = _read_pawnio_version()
        is_running = _pawnio_device_present()
    except Exception as exc:
        return {"is_installed": None, "is_running": None, "version": "", "error": str(exc)}
    return {"is_installed": bool(version) or is_running, "is_running": is_running, "version": version, "error": ""}


def _read_pawnio_version() -> str:
    import winreg

    for access in (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, winreg.KEY_READ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, PAWNIO_UNINSTALL_KEY, 0, access) as key:
                value, _value_type = winreg.QueryValueEx(key, "DisplayVersion")
        except OSError:
            continue
        if value:
            return str(value).strip()
    return ""


def _pawnio_device_present() -> bool:
    import ctypes
    from ctypes import wintypes

    file_share_read_write = 0x1 | 0x2
    open_existing = 3
    error_access_denied = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    # Zero access only asks whether the device exists; reading sensors is left to LibreHardwareMonitor.
    handle = create_file(PAWNIO_DEVICE_PATH, 0, file_share_read_write, None, open_existing, 0, None)
    if handle not in (None, ctypes.c_void_p(-1).value):
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == error_access_denied


def auto_assign_sensors(
    sensors: Iterable[Mapping[str, Any]],
    keep: Mapping[str, Any] | None = None,
) -> dict[str, str | None]:
    """Pick a sensor for each reading from a sensor inventory (dicts as the orchestrator lists them).

    The device is chosen first: GPU readings prefer the GPU with the most dedicated memory,
    so a discrete card beats integrated graphics even while one of its sensors reads nothing;
    storage prefers the lowest-numbered drive, usually the system disk. On that device a sensor
    reporting a live value wins over a better-named one that reads nothing.
    Mappings in `keep` that still point to a compatible sensor with a live value are kept.
    """
    sensors = list(sensors)
    sensors_by_id = {sensor.get("sensor_id"): sensor for sensor in sensors}
    hardware_order: dict[str, int] = {}
    gpu_memory_mb: dict[str, float] = {}
    for sensor in sensors:
        hardware_key = _hardware_key(sensor)
        hardware_order.setdefault(hardware_key, len(hardware_order))
        if sensor.get("hardware_role") == "gpu" and str(sensor.get("sensor_name", "")).lower() in {
            "gpu memory total", "d3d dedicated memory total",
        }:
            gpu_memory_mb[hardware_key] = max(gpu_memory_mb.get(hardware_key, 0.0), sensor.get("value") or 0.0)

    def hardware_preference(sensor: Mapping[str, Any]) -> tuple[float, ...]:
        hardware_key = _hardware_key(sensor)
        role = sensor.get("hardware_role")
        if role == "gpu":
            vendor = GPU_VENDOR_RANK.get(str(sensor.get("hardware_type", "")).lower(), 0)
            return (gpu_memory_mb.get(hardware_key, 0.0), vendor, -hardware_order[hardware_key])
        if role == "storage":
            return (-_device_index(sensor), -hardware_order[hardware_key])
        return (-hardware_order[hardware_key],)

    mappings: dict[str, str | None] = DEFAULT_TELEMETRY_MAPPINGS.copy()
    for category in TELEMETRY_CATEGORIES:
        kept_id = (keep or {}).get(category.key)
        kept = sensors_by_id.get(kept_id) if kept_id else None
        if kept is not None and _sensor_fits(kept, category) and _has_live_value(kept, category):
            mappings[category.key] = kept_id
            continue

        names = [name.lower() for name in AUTO_ASSIGN_SENSOR_NAMES.get(category.key, ())]
        candidates = [
            sensor
            for sensor in sensors
            if _sensor_fits(sensor, category)
            and str(sensor.get("sensor_name", "")).lower() in names
            and str(sensor.get("hardware_name", "")).lower() not in AUTO_ASSIGN_EXCLUDED_HARDWARE
        ]
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda sensor: (
                hardware_preference(sensor),
                _has_live_value(sensor, category),
                -names.index(str(sensor.get("sensor_name", "")).lower()),
            ),
        )
        mappings[category.key] = best.get("sensor_id")
    return mappings


def _sensor_fits(sensor: Mapping[str, Any], category: TelemetryCategory) -> bool:
    return sensor.get("sensor_type") == category.sensor_type and sensor.get("hardware_role") in category.hardware_roles


def _has_live_value(sensor: Mapping[str, Any], category: TelemetryCategory) -> bool:
    value = sensor.get("value")
    if value is None:
        return False
    if category.sensor_type == SENSOR_TYPE_TEMPERATURE:
        low, high = PLAUSIBLE_TEMPERATURE_C
        return low <= value <= high
    if category.sensor_type == SENSOR_TYPE_CLOCK:
        return value > 0  # clocks read 0 when the driver needed to read them is missing
    return True


def _hardware_key(sensor: Mapping[str, Any]) -> str:
    """Hardware part of a sensor identifier ("/gpu-nvidia/0/temperature/0" -> "/gpu-nvidia/0")."""
    sensor_id = str(sensor.get("sensor_id", ""))
    if sensor_id.startswith("/") and sensor_id.count("/") >= 3:
        return sensor_id.rsplit("/", 2)[0]
    return f"{sensor.get('hardware_type', '')}|{sensor.get('hardware_name', '')}"


def _device_index(sensor: Mapping[str, Any]) -> int:
    match = re.match(r"/[^/]+/(\d+)(?:/|$)", str(sensor.get("sensor_id", "")))
    return int(match.group(1)) if match else 1_000_000
