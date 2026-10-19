from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


CPU_TEMP_LIMIT_C = 105.0
GPU_TEMP_LIMIT_C = 95.0

SENSOR_TYPE_TEMPERATURE = "Temperature"
SENSOR_TYPE_LOAD = "Load"
SENSOR_TYPE_POWER = "Power"


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


TELEMETRY_CATEGORIES: tuple[TelemetryCategory, ...] = (
    TelemetryCategory("cpu_temp", "CPU Temperature", SENSOR_TYPE_TEMPERATURE, ("cpu",), " C"),
    TelemetryCategory("gpu_temp", "GPU Temperature", SENSOR_TYPE_TEMPERATURE, ("gpu",), " C"),
    TelemetryCategory("cpu_load", "CPU Load", SENSOR_TYPE_LOAD, ("cpu",), "%"),
    TelemetryCategory("gpu_load", "GPU Load", SENSOR_TYPE_LOAD, ("gpu",), "%"),
    TelemetryCategory("ram_load", "RAM Load", SENSOR_TYPE_LOAD, ("ram",), "%"),
    TelemetryCategory("storage_load", "Storage Load", SENSOR_TYPE_LOAD, ("storage",), "%"),
    TelemetryCategory("gpu_power", "GPU Power", SENSOR_TYPE_POWER, ("gpu",), " W"),
    TelemetryCategory("cpu_power", "CPU Power", SENSOR_TYPE_POWER, ("cpu",), " W"),
)
TELEMETRY_CATEGORY_MAP = {category.key: category for category in TELEMETRY_CATEGORIES}
DEFAULT_TELEMETRY_MAPPINGS = {category.key: None for category in TELEMETRY_CATEGORIES}


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
        with self._telemetry_lock:
            try:
                self._load_hardware_module()
                from LibreHardwareMonitor import PawnIo
            except Exception as exc:
                return {"is_installed": None, "version": "", "error": str(exc)}

            try:
                version = PawnIo.PawnIo.Version
                return {
                    "is_installed": bool(PawnIo.PawnIo.IsInstalled),
                    "version": str(version) if version is not None else "",
                    "error": "",
                }
            except Exception as exc:
                return {"is_installed": None, "version": "", "error": str(exc)}

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

            return TelemetrySample(
                cpu_temp=values["cpu_temp"],
                gpu_temp=values["gpu_temp"],
                cpu_load=values["cpu_load"],
                gpu_load=values["gpu_load"],
                ram_load=values["ram_load"],
                storage_load=values["storage_load"],
                gpu_power=values["gpu_power"],
                cpu_power=values["cpu_power"],
            )

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
