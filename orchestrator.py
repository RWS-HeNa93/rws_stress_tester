from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil

from eventlog import (
    HardwareEvent,
    HardwareEventWatcher,
    EventLogUnavailable,
    latest_record_id,
    query_hardware_events,
)
from report_gen import build_stability_report
from telemetry import (
    CPU_TEMP_LIMIT_C,
    GPU_TEMP_LIMIT_C,
    DEFAULT_TELEMETRY_MAPPINGS,
    TelemetryMonitor,
    TelemetrySample,
)


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ERROR_PATTERN = re.compile(r'\b(error|failed|hardware failure|fatal|fault|corruption|mismatch)\b', re.IGNORECASE)

# y-cruncher reports failed stress rounds with lines like "Failed - Loss of Precision"
# and sometimes "Result: Failed" or "FAILED" in capitalised output.
YCRUNCHER_FAIL_PATTERN = re.compile(
    r'\b(failed\s*-|result\s*:\s*failed|hardware\s+error|compute\s+error|'  # failure verdicts
    r'illegal\s+instruction|segfault|signal\s+\d+|exception)\b',
    re.IGNORECASE,
)

# DiskSpd error indicators in its summary/output.
DISKSPD_ERROR_PATTERN = re.compile(
    r'\b(error\s+count|i/o\s+error|i/o failed|access\s+denied|invalid\s+handle)\b',
    re.IGNORECASE,
)

# Phrases that are NOT actual errors (log noise / informational lines).
ERROR_FALSE_POSITIVE = re.compile(
    r'(stop on error|error.handling|error.log|error.report|error.check|no errors|errors:\s*0|zero errors|error free)',
    re.IGNORECASE,
)

YCRUNCHER_TEST_KEYS = frozenset({"cpu", "ram", "psu"})
DISKSPD_TEST_KEYS = frozenset({"storage_read", "storage_write"})
FURMARK_TEST_KEYS = frozenset({"gpu", "vram", "psu"})

# FurMark's artifact scanner plugin reports corrupted frames as
# "*** Artifact found at position: [x ; y] - total artifacts: N - num frames scanned: M ***".
FURMARK_ARTIFACT_PATTERN = re.compile(r'\bartifact found\b', re.IGNORECASE)
# FurMark logs the GPU its OpenGL context runs on at startup, e.g. "GL_RENDERER: NVIDIA GeForce 940MX/PCIe/SSE2".
FURMARK_RENDERER_PATTERN = re.compile(r'GL_RENDERER:\s*(.+)')

# DiskSpd -L latency table row, e.g. "    max |    12.345 |       N/A |    12.345" (milliseconds).
DISKSPD_MAX_LATENCY_PATTERN = re.compile(r'^\s*max\s*\|(.*)$', re.IGNORECASE)
# A single I/O this slow means the drive or controller stalled (link resets, timeouts, failing media),
# even when no I/O error is reported. Far above what a healthy drive needs even at queue depth 128.
STORAGE_STALL_MS = 10_000.0

# y-cruncher stress schedules, ranked by how much each test contributes. Short phases keep only the
# top-ranked tests so each one still gets YCRUNCHER_MIN_SLICE_SECONDS (see select_ycruncher_tests).
YCRUNCHER_SCHEDULES: dict[str, tuple[str, ...]] = {
    # Core/voltage stability:
    # SFTv4      in-cache FFT, maximum heat and power; catches the most core instability
    # BKT        light scalar work at peak boost clocks, where undervolts / Curve Optimizer fail
    # VT3        larger working set, vector float
    # SNT, SVT   more in-cache maximum-power work with different instruction mixes
    # NTT63      larger working set, vector integer
    # Switching between tests also produces load transients that expose weak voltage regulation.
    "cpu": ("SFTv4", "BKT", "VT3", "SNT", "SVT", "NTT63"),
    # Memory/IMC stability: VT3 comes first because it fills the whole -M allocation, so even a short
    # phase reaches every gigabyte under test. FFTv4 is the most sensitive to unstable RAM timings,
    # memory-controller and fabric settings, but floating-point precision caps its working set at a
    # few GB whatever -M is (about 2 GB measured with -M:11571m); NTT63 adds another access pattern.
    "ram": ("VT3", "FFTv4", "NTT63"),
    # Maximum sustained CPU power alongside FurMark: in-cache tests only, no memory stalls.
    "psu": ("SFTv4", "SNT", "SVT"),
}
# RAM phase size: y-cruncher gets enough memory to bring the whole system to RAM_FILL_PERCENT of
# physical memory, worked out when the phase starts, but never so much that less than the safety
# margin stays free for Windows and this app (running out pages to disk and stalls or fails the test).
RAM_FILL_PERCENT = 90.0
RAM_FILL_PERCENT_RANGE = (10.0, 100.0)
RAM_SAFETY_MARGIN_GB = 2.0
RAM_SAFETY_MARGIN_MIN_GB = 0.5
RAM_TEST_MIN_GB = 1.0
# Each test needs several verified iterations (and some heat soak) to be worth running; large-memory
# RAM iterations take much longer than in-cache CPU ones.
YCRUNCHER_MIN_SLICE_SECONDS = {"cpu": 60, "ram": 120, "psu": 60}
# The order y-cruncher itself runs enabled tests in; used to list the selected tests in run order.
YCRUNCHER_RUN_ORDER = ("BKT", "BBP", "SFTv4", "SNT", "SVT", "FFTv4", "NTT63", "VT3")

# FurMark --furmark-vram-test-gb sizes (OpenGL demo only). The VRAM phase fills this much video
# memory with FurMark data while rendering, so faulty memory shows up as artifacts or a driver crash.
FURMARK_VRAM_TEST_SIZES_GB = (2, 4, 6, 8, 10, 12, 16, 20, 24, 32)
# Left free for the driver, the desktop and FurMark's own buffers. Data that does not fit spills
# into system RAM over PCIe, which then gets tested instead of the VRAM.
VRAM_HEADROOM_GB = 1.0

# DiskSpd test file: large enough to get past SSD DRAM/SLC caches, bounded by free space.
DISKSPD_MAX_FILE_GIB = 8
DISKSPD_FREE_SPACE_FRACTION = 0.2

# A failing engine can print an error on every line; list the first few per phase, count the rest.
MAX_ENGINE_ERRORS_LISTED = 10

THERMAL_ABORT_MESSAGE = "Thermal Failure - Aborted"
CRASH_STATUS = "FAILED (System Crash)"
STDERR_PREFIX = "[STDERR] "
# Log lines for things worth checking that do not fail the test, shown in yellow.
WARNING_NOTE_PREFIX = "[APP] WARNING: "

# y-cruncher -D is the per-algorithm duration; -TL bounds the whole stress run.
YCRUNCHER_PER_TEST_SECONDS = 300
# Engines finish on their own slightly after the phase deadline (startup time, DiskSpd warmup,
# final summaries). Give them this long to exit cleanly before they are killed.
PROCESS_EXIT_GRACE_SECONDS = 60
# Per-engine override; a phase waits for the longest of its engines.
# - DiskSpd prints its whole result summary (including error counts) only at the end, after a 5 s warm-up.
# - FurMark's --max-time starts counting after its own startup, so it exits a few seconds late.
# - y-cruncher does not stop at -TL (it starts another test), so waiting longer only adds unplanned load.
ENGINE_EXIT_GRACE_SECONDS = {
    "diskspd.exe": 120,  # also covers creating the multi-GB test file on slow drives
    "furmark.exe": 30,
    "y-cruncher.exe": 10,
}
# An engine that exits this many seconds before the phase deadline did not run the full test.
PREMATURE_EXIT_TOLERANCE_SECONDS = 5
# How long the log writer keeps draining engine output after the phase has been stopped.
MONITOR_DRAIN_SECONDS = 5.0
PROCESS_KILL_WAIT_SECONDS = 5.0

RUN_ID_PATTERN = re.compile(r"^\d{8}_\d{6}$")

# Windows status codes a process ends with when it crashes or is killed, rather than reporting
# a failed test itself. Exit codes are shown as negative numbers or huge unsigned ones otherwise.
WINDOWS_EXIT_CODES = {
    0xC0000005: "access violation - the program crashed reading or writing invalid memory",
    0xC0000006: "in-page error - the program could not be read from disk (failing drive or USB stick?)",
    0xC000001D: "illegal instruction - the CPU ran an instruction it does not support, or the CPU is unstable",
    0xC0000094: "integer divide by zero",
    0xC0000096: "privileged instruction",
    0xC00000FD: "stack overflow",
    0xC0000135: "a required DLL was not found",
    0xC0000139: "a DLL function was not found (DLL version mismatch)",
    0xC0000142: "a DLL failed to initialize",
    0xC000013A: "terminated by Ctrl+C or console close",
    0xC0000374: "heap corruption",
    0xC0000409: "the program aborted itself after detecting memory corruption (fail-fast)",
    0xC0000417: "invalid parameter passed to the C runtime",
    0xC0000420: "assertion failure",
    0x80000003: "breakpoint - the program stopped itself deliberately",
}


def describe_exit_code(rc: int | None) -> str:
    """Readable form of a process exit code, e.g. '0xC0000005 (access violation - ...)'."""
    if rc is None:
        return "none (still running when stopped)"
    unsigned = rc & 0xFFFFFFFF
    if unsigned in WINDOWS_EXIT_CODES:
        return f"0x{unsigned:08X} ({WINDOWS_EXIT_CODES[unsigned]})"
    if unsigned >= 0xC0000000:
        return f"0x{unsigned:08X} (crashed with an unrecognized Windows error)"
    if rc == 0:
        return "0 (success)"
    return f"{rc} (error code reported by the engine)"

EVENT_LOG = "log"
EVENT_STATUS = "status"
EVENT_TELEMETRY = "telemetry"
EVENT_PHASE = "phase"
EVENT_FINISHED = "finished"
EVENT_ABORTED = "aborted"
EVENT_ALERT = "alert"
EVENT_WARNING = "warning"
EVENT_PHASE_LOGS = "phase_logs"


def is_error_line(line: str, test_key: str) -> bool:
    """Determine whether a stdout/stderr line represents a genuine error.

    Uses a layered approach:
    1. Reject known false-positive phrases first.
    2. Apply engine-specific patterns for the active test.
    3. Fall back to the generic ERROR_PATTERN.
    """
    lowered = line.lower().strip()
    if not lowered:
        return False

    if ERROR_FALSE_POSITIVE.search(lowered):
        return False

    if test_key in YCRUNCHER_TEST_KEYS and YCRUNCHER_FAIL_PATTERN.search(lowered):
        return True
    if test_key in DISKSPD_TEST_KEYS and DISKSPD_ERROR_PATTERN.search(lowered):
        return True
    if test_key in DISKSPD_TEST_KEYS and _is_storage_stall(lowered):
        return True
    if test_key in FURMARK_TEST_KEYS and FURMARK_ARTIFACT_PATTERN.search(lowered):
        return True

    return bool(ERROR_PATTERN.search(lowered))


def select_ycruncher_tests(test_key: str, duration_seconds: float) -> tuple[str, ...]:
    """Highest-ranked tests that fit the phase at the minimum slice each, in y-cruncher's run order."""
    ranked = YCRUNCHER_SCHEDULES[test_key]
    fit = int(duration_seconds // YCRUNCHER_MIN_SLICE_SECONDS[test_key])
    selected = ranked[: max(1, min(len(ranked), fit))]
    return tuple(sorted(selected, key=YCRUNCHER_RUN_ORDER.index))


def ram_test_size_gb(fill_percent: float, safety_margin_gb: float) -> float:
    """Memory for y-cruncher so the system reaches fill_percent in use, leaving safety_margin_gb free."""
    memory = psutil.virtual_memory()
    gib = 1024 ** 3
    total_gb = memory.total / gib
    in_use_gb = (memory.total - memory.available) / gib
    return max(0.0, min(total_gb * fill_percent / 100 - in_use_gb, total_gb - in_use_gb - safety_margin_gb))


def _is_storage_stall(line: str) -> bool:
    match = DISKSPD_MAX_LATENCY_PATTERN.match(line)
    if not match:
        return False
    for cell in match.group(1).split("|"):
        try:
            if float(cell.strip()) >= STORAGE_STALL_MS:
                return True
        except ValueError:
            continue
    return False


def detect_vram_gb() -> float | None:
    """Dedicated video memory of the largest display adapter, from the driver's registry entries."""
    if os.name != "nt":
        return None
    import winreg

    class_key = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    largest = 0
    try:
        adapters = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_key)
    except OSError:
        return None
    with adapters:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(adapters, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(adapters, name) as adapter:
                    # qwMemorySize is exact; the older MemorySize value tops out at 4 GB.
                    for value_name in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                        try:
                            value, _ = winreg.QueryValueEx(adapter, value_name)
                        except OSError:
                            continue
                        if isinstance(value, bytes):
                            value = int.from_bytes(value[:8], "little")
                        if isinstance(value, int) and value > 0:
                            largest = max(largest, value)
                            break
            except OSError:
                continue
    return largest / (1024 ** 3) if largest else None


def detect_cpu_name() -> str:
    """Processor brand string, e.g. "AMD Ryzen 7 5800X 8-Core Processor" ("" if unavailable)."""
    if os.name != "nt":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
    except OSError:
        return ""
    return " ".join(str(value).split())


def recommended_vram_test_gb(detected_gb: float | None) -> int:
    """Largest FurMark VRAM test size that leaves VRAM_HEADROOM_GB free (the smallest size if unknown)."""
    if not detected_gb:
        return FURMARK_VRAM_TEST_SIZES_GB[0]
    fitting = [size for size in FURMARK_VRAM_TEST_SIZES_GB if size <= detected_gb - VRAM_HEADROOM_GB]
    return fitting[-1] if fitting else FURMARK_VRAM_TEST_SIZES_GB[0]


@dataclass
class TestDefinition:
    key: str
    label: str
    duration_minutes: int
    ram_fill_percent: float = RAM_FILL_PERCENT
    ram_safety_margin_gb: float = RAM_SAFETY_MARGIN_GB
    ram_gb: float = 0.0  # worked out when the RAM phase starts
    target_drive: str = ""
    gpu_resolution: str = "1280x720"
    vram_gb: int = 0


@dataclass
class SessionSummary:
    completed_at: datetime
    tests: list[dict[str, Any]]
    max_cpu_temp: float
    max_gpu_temp: float
    max_ram_load: float
    max_cpu_power: float
    max_gpu_power: float
    max_total_power: float
    issues: list[str]
    clean_run: bool
    verdict: str
    report_path: str = ""
    report_error: str = ""
    run_id: str = ""
    started_at: str = ""
    machine_name: str = ""
    event_log_monitored: bool = False
    # Windows event log findings worth checking; they do not affect the verdict or the certificate.
    warnings: list[str] = field(default_factory=list)


class StressOrchestrator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.engines_root = root / "Engines"
        self.state_path = root / "test_state.json"
        self.settings_path = root / "settings.json"
        self.presets_path = root / "presets.json"
        self.report_dir = root / "reports"
        self.event_queue: queue.Queue[dict[str, Any]] = queue.Queue()

        self.telemetry = TelemetryMonitor(self.engines_root)
        self.stop_event = threading.Event()
        self.telemetry_stop_event = threading.Event()
        self.session_active_event = threading.Event()
        self.session_thread: threading.Thread | None = None
        self.telemetry_thread: threading.Thread | None = None
        self.engine_monitor_thread: threading.Thread | None = None
        self.active_processes: list[subprocess.Popen[str]] = []
        self.active_test_key: str | None = None
        self.active_phase_index = 0
        self.planned_tests: list[TestDefinition] = []
        self.test_results: list[dict[str, Any]] = []
        self.issues: list[str] = []           # session-wide issues (crash recovery, telemetry, etc)
        self._phase_issues: list[str] = []    # per-test issues, reset before each phase
        self.warnings: list[str] = []         # worth checking, never fail a test
        self._phase_warnings: list[str] = []
        self._phase_engine_errors = 0         # error lines engines printed this phase, listed or not
        self.max_cpu_temp = 0.0
        self.max_gpu_temp = 0.0
        self.max_ram_load = 0.0
        self.max_cpu_power = 0.0
        self.max_gpu_power = 0.0
        self.max_total_power = 0.0
        self._phase_max: dict[str, float] = self._empty_phase_max()
        self.manual_abort = False
        self._monitor_lock = threading.Lock()
        # Guards issue lists and telemetry maxima, which are written by the telemetry,
        # engine-monitor and session threads concurrently.
        self._state_lock = threading.RLock()
        self.cpu_temp_limit_c = CPU_TEMP_LIMIT_C
        self.gpu_temp_limit_c = GPU_TEMP_LIMIT_C
        self.cpu_name = detect_cpu_name()
        self._phase_gpu_name = ""  # GPU FurMark renders on in the current phase, once its log reports it
        # Readings the loaded settings.json had no entry for, i.e. added since the assignment was saved.
        self.new_telemetry_readings: list[str] = []

        self.run_id = ""
        self.run_dir: Path | None = None
        self.logs_dir: Path | None = None
        self.started_at: datetime | None = None
        self.generate_report = True
        # Windows event log watching: each phase picks up after the last event the previous phase read,
        # so every event is reported once per session.
        self._event_record_id: int | None = None
        self.report_event_log_warnings = True
        self._event_log_unavailable_reported = False

    def load_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return default

    def save_json(self, path: Path, payload: Any) -> None:
        # Write-then-rename with fsync so a hard power-off (the PSU test's failure mode)
        # never leaves a truncated settings or crash-state file behind.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, default=str))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)

    @staticmethod
    def _normalize_temperature_limit(value: Any, default: float) -> float:
        # The hardcoded limits are hard ceilings: settings may lower the kill switch, never raise it.
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(parsed) or parsed <= 0:
            return default
        return min(parsed, default)

    def load_settings(self) -> dict[str, Any]:
        defaults = {
            "logo_path": "",
            "include_customer_name": False,
            "customer_name": "",
            "include_machine_name": False,
            "certificate_machine_name": "",
            "telemetry_mappings": DEFAULT_TELEMETRY_MAPPINGS.copy(),
            "sensor_assignment_confirmed": False,
            "sensors_reviewed_with_pawnio": True,
            "cpu_temp_limit_c": CPU_TEMP_LIMIT_C,
            "gpu_temp_limit_c": GPU_TEMP_LIMIT_C,
            "event_log_warnings": True,
            "ui_theme": "",  # empty = the UI's default skin
        }
        loaded = self.load_json(self.settings_path, defaults)
        if not isinstance(loaded, dict):
            return defaults

        settings = defaults.copy()
        settings["logo_path"] = str(loaded.get("logo_path", "") or "")
        settings["include_customer_name"] = bool(loaded.get("include_customer_name", False))
        settings["customer_name"] = str(loaded.get("customer_name", "") or "")
        settings["include_machine_name"] = bool(loaded.get("include_machine_name", False))
        settings["certificate_machine_name"] = str(loaded.get("certificate_machine_name", "") or "")
        settings["cpu_temp_limit_c"] = self._normalize_temperature_limit(loaded.get("cpu_temp_limit_c"), CPU_TEMP_LIMIT_C)
        settings["gpu_temp_limit_c"] = self._normalize_temperature_limit(loaded.get("gpu_temp_limit_c"), GPU_TEMP_LIMIT_C)
        settings["event_log_warnings"] = bool(loaded.get("event_log_warnings", True))
        settings["ui_theme"] = str(loaded.get("ui_theme", "") or "")

        mappings = loaded.get("telemetry_mappings")
        if isinstance(mappings, dict):
            self.new_telemetry_readings = [key for key in DEFAULT_TELEMETRY_MAPPINGS if key not in mappings]
            normalized_mappings = DEFAULT_TELEMETRY_MAPPINGS.copy()
            for key in normalized_mappings:
                value = mappings.get(key)
                normalized_mappings[key] = str(value).strip() if value else None
            settings["telemetry_mappings"] = normalized_mappings

        if "sensor_assignment_confirmed" in loaded:
            settings["sensor_assignment_confirmed"] = bool(loaded.get("sensor_assignment_confirmed"))
        else:
            # Settings from before the first-run review: a machine with sensors assigned was set up by hand.
            settings["sensor_assignment_confirmed"] = any(settings["telemetry_mappings"].values())
        settings["sensors_reviewed_with_pawnio"] = bool(loaded.get("sensors_reviewed_with_pawnio", True))

        return settings

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.save_json(self.settings_path, settings)

    def load_presets(self) -> dict[str, Any]:
        loaded = self.load_json(self.presets_path, {})
        if not isinstance(loaded, dict):
            return {}
        return {str(name): payload for name, payload in loaded.items() if isinstance(payload, dict)}

    def save_presets(self, presets: dict[str, Any]) -> None:
        self.save_json(self.presets_path, presets)

    def configure_telemetry(self, mappings: dict[str, Any] | None) -> dict[str, str | None]:
        return self.telemetry.set_sensor_mappings(mappings)

    def configure_thermal_limits(self, settings: dict[str, Any] | None) -> dict[str, float]:
        if isinstance(settings, dict):
            cpu_limit = self._normalize_temperature_limit(settings.get("cpu_temp_limit_c"), CPU_TEMP_LIMIT_C)
            gpu_limit = self._normalize_temperature_limit(settings.get("gpu_temp_limit_c"), GPU_TEMP_LIMIT_C)
        else:
            cpu_limit = CPU_TEMP_LIMIT_C
            gpu_limit = GPU_TEMP_LIMIT_C

        self.cpu_temp_limit_c = cpu_limit
        self.gpu_temp_limit_c = gpu_limit
        return {
            "cpu_temp_limit_c": cpu_limit,
            "gpu_temp_limit_c": gpu_limit,
        }

    def list_telemetry_sensors(self) -> list[dict[str, Any]]:
        return [
            {
                "sensor_id": sensor.sensor_id,
                "sensor_name": sensor.sensor_name,
                "sensor_type": sensor.sensor_type,
                "hardware_name": sensor.hardware_name,
                "hardware_type": sensor.hardware_type,
                "hardware_role": sensor.hardware_role,
                "display_name": sensor.display_name,
                "value": sensor.value,
            }
            for sensor in self.telemetry.list_available_sensors()
        ]

    def get_pawnio_status(self) -> dict[str, Any]:
        return self.telemetry.get_pawnio_status()

    def reload_telemetry(self) -> None:
        """Rebuild the sensor session, e.g. so a freshly installed PawnIO driver is used."""
        self.telemetry.reopen()

    def check_crash_recovery(self, report_warnings: bool = True) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        payload = self.load_json(self.state_path, None)
        if isinstance(payload, dict) and payload.get("status") != "running":
            self.clear_state_file()
            return None
        if not isinstance(payload, dict):
            # The state file is only ever left behind by an interrupted run; an unreadable one
            # means the machine went down while it was being written.
            payload = {"status": "running", "current_test": "", "run_id": ""}

        failures, warnings = self._crash_windows_events(payload)
        payload["windows_events"] = failures
        payload["windows_warnings"] = warnings if report_warnings else []
        payload["summary_path"] = self._reconstruct_crashed_run(payload)
        self.clear_state_file()
        return payload

    @staticmethod
    def _crash_windows_events(state: dict[str, Any]) -> tuple[list[str], list[str]]:
        """What Windows logged since the crashed phase started, as (failures, warnings worth checking):
        blue screens, power loss and uncorrected hardware errors first, then milder events."""
        record_id = state.get("event_record_id")
        if not isinstance(record_id, int) or isinstance(record_id, bool):
            return [], []
        try:
            events = query_hardware_events(record_id, include_after_restart=True).events
        except EventLogUnavailable:
            return [], []
        gpu_under_load = str(state.get("current_test", "") or "") in FURMARK_TEST_KEYS
        failures: list[str] = []
        warnings: list[str] = []
        for event in events:
            failure = event.is_failure(gpu_under_load)
            (failures if failure else warnings).append(event.describe(failure))
        return failures, warnings

    def _reconstruct_crashed_run(self, state: dict[str, Any]) -> str:
        run_id = str(state.get("run_id", "") or "")
        if not RUN_ID_PATTERN.match(run_id):
            return ""
        run_dir = self.root / "results" / run_id
        logs_dir = run_dir / "logs"
        if not logs_dir.is_dir():
            return ""

        test_key = str(state.get("current_test", "") or "")
        label = self._test_catalog().get(test_key, test_key or "Unknown test")
        crash_note = f"System crash detected during {label}. Run summary reconstructed on next application launch."

        completed = self.load_json(logs_dir / "engine_meta.json", [])
        tests = [
            {
                "test_name": entry.get("test_name", ""),
                "engine_name": entry.get("engine_name", ""),
                "status": entry.get("status", ""),
                "duration_seconds": entry.get("duration_seconds", 0),
                "command_line": entry.get("command_line", ""),
                "log_file_path": entry.get("log_file_path", ""),
                "native_engine_log_file_path": entry.get("native_engine_log_file_path"),
                "selected_ram_gb": entry.get("selected_ram_gb", 0.0),
                "selected_vram_gb": entry.get("selected_vram_gb", 0),
            }
            for entry in (completed if isinstance(completed, list) else [])
            if isinstance(entry, dict)
        ]
        crashed_log = logs_dir / f"{test_key}_stdout.log" if test_key else None
        tests.append(
            {
                "test_name": label,
                "engine_name": "",
                "status": CRASH_STATUS,
                "duration_seconds": 0,
                "command_line": "",
                "log_file_path": str(crashed_log) if crashed_log else "",
                "native_engine_log_file_path": None,
                "selected_ram_gb": 0.0,
            }
        )

        windows_events = [str(line) for line in state.get("windows_events") or []]
        windows_warnings = [str(line) for line in state.get("windows_warnings") or []]
        if crashed_log is not None:
            try:
                with open(crashed_log, "a", encoding="utf-8") as handle:
                    handle.write(f"[APP] {crash_note}\n")
                    for line in windows_events:
                        handle.write(f"[APP] {line}\n")
                    for line in windows_warnings:
                        handle.write(f"{WARNING_NOTE_PREFIX}{line}\n")
            except OSError:
                pass

        summary_path = run_dir / "run_summary.json"
        try:
            self.save_json(
                summary_path,
                {
                    "run_id": run_id,
                    "machine_name": socket.gethostname(),
                    "started_at": "",
                    "completed_at": datetime.now().isoformat(),
                    "final_verdict": "UNSTABLE",
                    "verdict": "UNSTABLE",
                    "clean_run": False,
                    "crash_recovered": True,
                    "issues": [crash_note, *windows_events],
                    "warnings": windows_warnings,
                    "tests": tests,
                },
            )
        except OSError:
            return ""
        return str(summary_path)

    def clear_state_file(self) -> None:
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def start_session(self, selections: dict[str, int], settings: dict[str, Any], ram_fill_percent: float = RAM_FILL_PERCENT, ram_safety_margin_gb: float = RAM_SAFETY_MARGIN_GB, target_drive: str = "", gpu_resolution: str = "1280x720", generate_report: bool = True, vram_gb: int = 0) -> None:
        if self.session_thread and self.session_thread.is_alive():
            raise RuntimeError("A diagnostics session is already running.")

        planned_tests = self._build_plan(selections, ram_fill_percent, ram_safety_margin_gb, target_drive, gpu_resolution, vram_gb)

        self.configure_telemetry(settings.get("telemetry_mappings"))
        self.configure_thermal_limits(settings)

        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = self.root / "results" / self.run_id
        self.logs_dir = self.run_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.started_at = datetime.now()
        self.report_event_log_warnings = bool(settings.get("event_log_warnings", True))
        self._event_log_unavailable_reported = False
        try:
            self._event_record_id = latest_record_id()
        except EventLogUnavailable:
            self._event_record_id = None  # the first phase tries again and reports it

        self.stop_event.clear()
        self.manual_abort = False
        with self._state_lock:
            self.issues = []
            self._phase_issues = []
            self.warnings = []
            self._phase_warnings = []
            self.max_cpu_temp = 0.0
            self.max_gpu_temp = 0.0
            self.max_ram_load = 0.0
            self.max_cpu_power = 0.0
            self.max_gpu_power = 0.0
            self.max_total_power = 0.0
            self._phase_max = self._empty_phase_max()
        self.test_results = []
        self.active_test_key = None
        self.generate_report = generate_report
        self.planned_tests = planned_tests
        self.session_active_event.set()
        self._ensure_telemetry_thread()

        self.session_thread = threading.Thread(
            target=self._run_session,
            args=(settings,),
            name="session-orchestrator",
            daemon=True,
        )
        self.session_thread.start()

    def abort_session(self) -> None:
        if not (self.session_thread and self.session_thread.is_alive()):
            return
        self.manual_abort = True
        self.stop_event.set()
        self.session_active_event.clear()
        self._emit(EVENT_STATUS, message="Abort requested")
        self._stop_active_processes()

    def is_session_running(self) -> bool:
        return bool(self.session_thread and self.session_thread.is_alive())

    def shutdown(self) -> None:
        self.abort_session()
        if self.session_thread and self.session_thread.is_alive():
            # Let the session thread flush logs and clear the crash-state file.
            self.session_thread.join(timeout=PROCESS_KILL_WAIT_SECONDS + MONITOR_DRAIN_SECONDS + 2)
        self.telemetry_stop_event.set()
        if self.telemetry_thread and self.telemetry_thread.is_alive():
            self.telemetry_thread.join(timeout=2.5)
        self.telemetry.close()

    def _run_session(self, settings: dict[str, Any]) -> None:
        summary: SessionSummary | None = None
        try:
            total = len(self.planned_tests)
            for index, test in enumerate(self.planned_tests, start=1):
                if self.stop_event.is_set():
                    break
                self.active_test_key = test.key
                self.active_phase_index = index
                upcoming_seconds = sum(t.duration_minutes * 60 for t in self.planned_tests[index:])
                self._emit(
                    EVENT_PHASE,
                    phase_index=index,
                    total_phases=total,
                    label=test.label,
                    duration_minutes=test.duration_minutes,
                    remaining_seconds=test.duration_minutes * 60,
                    total_remaining_seconds=test.duration_minutes * 60 + upcoming_seconds,
                    tested_hardware=self._tested_hardware(test),
                )
                result = self._run_test(test)
                self.test_results.append(result)

                if result["status"] in ("ABORTED", "THERMAL_ABORT"):
                    break
        except Exception as exc:
            if not self.manual_abort:
                self._record_issue(f"Session error: {exc}", phase=False)
                self._emit(EVENT_ALERT, message=f"Session error: {exc}")
        finally:
            self.session_active_event.clear()
            self.stop_event.set()
            self._stop_active_processes()
            try:
                summary = self._finalize_session(settings, aborted=self.manual_abort)
            except Exception as exc:
                self._emit(EVENT_ALERT, message=f"Unable to write run summary: {exc}")
            self.clear_state_file()
            # Always emit a terminal event, otherwise the dashboard stays locked in "running".
            if self.manual_abort:
                self._emit(EVENT_ABORTED, message="Diagnostics session aborted. No report generated.")
            elif summary is not None:
                self._emit(EVENT_FINISHED, summary=asdict(summary))
            else:
                self._emit(EVENT_ABORTED, message="Diagnostics session ended unexpectedly. See the console for details.")

    def _run_test(self, test: TestDefinition) -> dict[str, Any]:
        status = "PASSED"
        with self._state_lock:
            self._phase_issues = []  # reset per-test issues — do NOT inherit from prior phases
            self._phase_warnings = []
            self._phase_engine_errors = 0
            self._phase_max = self._empty_phase_max()
        self._phase_gpu_name = ""
        self._write_state_file(test.key)

        test_started_at = datetime.now()
        app_log_path = self.logs_dir / f"{test.key}_stdout.log"
        native_log_path = self.logs_dir / f"{test.key}_ycruncher.log" if test.key in YCRUNCHER_TEST_KEYS else None
        commands: list[list[str]] = []
        processes: list[subprocess.Popen[str]] = []
        collected_exit_codes: list[int | None] = []
        app_notes: list[str] = []
        line_queue: queue.Queue[tuple[str, str] | None] = queue.Queue()
        monitor_done = threading.Event()
        self.engine_monitor_thread = None
        event_watcher: HardwareEventWatcher | None = None

        try:
            commands = self._build_commands(test, str(native_log_path) if native_log_path else "")
            event_watcher = self._start_event_watcher(test, app_notes)
            processes = self._launch_test_processes(commands)

            # Start our background thread to drain logs and write to file
            self.engine_monitor_thread = threading.Thread(
                target=self._monitor_process_output,
                args=(processes, app_log_path, test.key, line_queue, monitor_done),
                name="engine-monitor",
                daemon=True,
            )
            self.engine_monitor_thread.start()

            phase_seconds = test.duration_minutes * 60
            launch_time = time.monotonic()
            deadline = launch_time + phase_seconds
            grace_seconds = max(
                ENGINE_EXIT_GRACE_SECONDS.get(Path(command[0]).name.lower(), PROCESS_EXIT_GRACE_SECONDS)
                for command in commands
            )
            grace_deadline = deadline + grace_seconds
            grace_announced = False
            exit_checked: set[int] = set()

            while not self.stop_event.is_set():
                now = time.monotonic()
                remaining = max(0, int(math.ceil(deadline - now)))
                upcoming_seconds = sum(t.duration_minutes * 60 for t in self.planned_tests[self.active_phase_index:])
                finishing = now >= deadline
                self._emit(
                    EVENT_PHASE,
                    phase_index=self.active_phase_index,
                    total_phases=len(self.planned_tests),
                    label=test.label,
                    duration_minutes=test.duration_minutes,
                    remaining_seconds=remaining,
                    total_remaining_seconds=remaining + upcoming_seconds,
                    finishing=finishing,
                    finishing_remaining_seconds=max(0, int(math.ceil(grace_deadline - now))) if finishing else 0,
                    tested_hardware=self._tested_hardware(test),
                )

                # One poll per process per pass, so an engine that exits during the pass is still checked.
                exit_codes = [process.poll() for process in processes]
                for process, rc in zip(processes, exit_codes):
                    if rc is None or process.pid in exit_checked or self.stop_event.is_set():
                        continue
                    exit_checked.add(process.pid)
                    engine = Path(process.args[0]).name if isinstance(process.args, list) else str(process.args)
                    if rc != 0:
                        if now >= deadline:
                            when = f"{int(now - deadline)}s after completing the full {phase_seconds}s test"
                        else:
                            when = f"after {int(now - launch_time)}s of the planned {phase_seconds}s test"
                        self._flag_phase_issue(
                            f"{test.label}: {engine} exited {when} with code {describe_exit_code(rc)}.",
                            app_notes,
                        )
                    elif deadline - now > PREMATURE_EXIT_TOLERANCE_SECONDS:
                        ran_for = int(now - launch_time)
                        self._flag_phase_issue(
                            f"{test.label}: {engine} exited after {ran_for}s, before the planned {phase_seconds}s test duration.",
                            app_notes,
                        )

                if all(rc is not None for rc in exit_codes):
                    break
                if now >= grace_deadline:
                    break
                if now >= deadline and not grace_announced:
                    grace_announced = True
                    self._emit(
                        EVENT_STATUS,
                        message=f"{test.label}: duration reached, finishing (waiting up to {grace_seconds}s for engines to exit)...",
                    )
                time.sleep(1)

            if event_watcher is not None:
                self._stop_event_watcher(event_watcher)
                if event_watcher.suppressed_failures and not self.manual_abort:
                    self._flag_phase_issue(
                        f"{test.label}: Windows logged {event_watcher.suppressed_failures} more hardware error(s) "
                        "or driver reset(s) not listed here; see Event Viewer > Windows Logs > System.",
                        app_notes,
                    )
                if event_watcher.suppressed_warnings and not self.manual_abort:
                    self._flag_phase_warning(
                        f"{test.label}: Windows logged {event_watcher.suppressed_warnings} more event(s) worth "
                        "checking not listed here; see Event Viewer > Windows Logs > System.",
                        app_notes,
                    )
                event_watcher = None

            for process in processes:
                collected_exit_codes.append(process.poll())

            # ---- Determine status from per-test issues ONLY ----
            with self._state_lock:
                phase_issues = self._phase_issues[:]
            if self.manual_abort:
                status = "ABORTED"
            elif THERMAL_ABORT_MESSAGE in phase_issues:
                status = "THERMAL_ABORT"
            elif self.stop_event.is_set():
                # Stopped by a safety mechanism other than the thermal switch (e.g. telemetry loss).
                status = "ABORTED"
            elif phase_issues:
                status = "FAILED"

        except Exception as exc:
            status = "ABORTED" if self.manual_abort else "FAILED"
            if not self.manual_abort:
                self._flag_phase_issue(f"{test.label} could not run: {exc}", app_notes)

        finally:
            self._stop_active_processes()
            if event_watcher is not None:
                self._stop_event_watcher(event_watcher)
            with self._state_lock:
                if THERMAL_ABORT_MESSAGE in self._phase_issues:
                    app_notes.append(THERMAL_ABORT_MESSAGE)
            if self.engine_monitor_thread is not None:
                for note in app_notes:
                    line_queue.put(("note", note))
                monitor_done.set()
                self.engine_monitor_thread.join(timeout=MONITOR_DRAIN_SECONDS + 2.0)
            else:
                self._append_notes_to_log(app_log_path, app_notes)
            if test.key in DISKSPD_TEST_KEYS:
                self._cleanup_diskspd_target(test.target_drive)
            self.clear_state_file()

        # Counted once the output monitor has finished, so every engine line is included.
        with self._state_lock:
            unlisted_engine_errors = self._phase_engine_errors - MAX_ENGINE_ERRORS_LISTED
        if unlisted_engine_errors > 0 and not self.manual_abort:
            engine_notes: list[str] = []
            self._flag_phase_issue(
                f"{test.label}: the engine output had {unlisted_engine_errors} more error line(s) not listed here; "
                "see the phase log.",
                engine_notes,
            )
            self._append_notes_to_log(app_log_path, engine_notes)
            if status == "PASSED":
                status = "FAILED"

        if test.key in FURMARK_TEST_KEYS and commands:
            furmark_log_path, artifact_lines = self._collect_furmark_log(test.key, test_started_at)
            if test.key in ("gpu", "vram") and furmark_log_path is not None:
                native_log_path = furmark_log_path
            with self._state_lock:
                already_flagged = any(FURMARK_ARTIFACT_PATTERN.search(issue) for issue in self._phase_issues)
            if artifact_lines and not already_flagged and not self.manual_abort:
                late_notes: list[str] = []
                self._flag_phase_issue(
                    f"{test.label}: FurMark artifact scanner reported rendering errors "
                    f"({len(artifact_lines)} report(s)): {artifact_lines[-1]}",
                    late_notes,
                )
                self._append_notes_to_log(app_log_path, late_notes)
                if status == "PASSED":
                    status = "FAILED"

        test_finished_at = datetime.now()
        duration_seconds = (test_finished_at - test_started_at).total_seconds()

        # Use the worst real exit code, fall back to -1 for error / 0 for success
        real_exit_codes = [c for c in collected_exit_codes if c is not None]
        if real_exit_codes:
            worst_exit_code = max(real_exit_codes, key=lambda c: abs(c))
        else:
            worst_exit_code = -1 if status != "PASSED" else 0

        with self._state_lock:
            phase_issues = self._phase_issues[:]
            phase_warnings = self._phase_warnings[:]
            phase_max = dict(self._phase_max)

        if not commands:
            engine_name = ""
        elif len(commands) > 1:
            engine_name = "Multiple"
        else:
            engine_name = commands[0][0]

        meta = {
            "test_name": test.label,
            "test_key": test.key,
            "engine_name": engine_name,
            "command_line": " | ".join(subprocess.list2cmdline(c) for c in commands),
            "started_at": test_started_at.isoformat(),
            "finished_at": test_finished_at.isoformat(),
            "duration_seconds": duration_seconds,
            "exit_code": worst_exit_code,
            "status": status,
            "log_file_path": str(app_log_path),
            "native_engine_log_file_path": str(native_log_path) if native_log_path else None,
            "selected_ram_gb": test.ram_gb if test.key == "ram" else 0.0,
            "selected_vram_gb": test.vram_gb if test.key == "vram" else 0,
            "tested_hardware": dict(self._tested_hardware(test)),
            "phase_issues": phase_issues,
            "phase_warnings": phase_warnings,
        }

        try:
            self._append_engine_meta(meta)
        except OSError as exc:
            self._emit(EVENT_ALERT, message=f"Unable to write engine metadata: {exc}")

        self._emit(EVENT_PHASE_LOGS, meta=meta)

        return {
            "test_name": test.label,
            "engine_name": meta["engine_name"],
            "status": status,
            "duration_seconds": duration_seconds,
            "command_line": meta["command_line"],
            "log_file_path": meta["log_file_path"],
            "native_engine_log_file_path": meta["native_engine_log_file_path"],
            "max_cpu_temp_c": phase_max["cpu_temp"],
            "max_gpu_temp_c": phase_max["gpu_temp"],
            "max_ram_percent": phase_max["ram_load"],
            "selected_ram_gb": meta["selected_ram_gb"],
            "selected_vram_gb": meta["selected_vram_gb"],
            "key": test.key,
            "label": test.label,
            "duration_minutes": test.duration_minutes
        }

    def _tested_hardware(self, test: TestDefinition) -> list[tuple[str, str]]:
        """The processor and/or graphics card a phase runs on, as (role, name) pairs for the dashboard."""
        devices: list[tuple[str, str]] = []
        if test.key in YCRUNCHER_TEST_KEYS:
            devices.append(("CPU", self.cpu_name or self.telemetry.mapped_hardware_name("cpu_temp") or "Unknown processor"))
        if test.key in FURMARK_TEST_KEYS:
            if not self._phase_gpu_name:
                self._phase_gpu_name = self._read_furmark_renderer(test.key)
                if self._phase_gpu_name:
                    self._emit(EVENT_STATUS, message=f"{test.label}: FurMark is rendering on {self._phase_gpu_name}")
            # Until FurMark has started, show the GPU whose temperature sensor is assigned.
            devices.append(("GPU", self._phase_gpu_name or self.telemetry.mapped_hardware_name("gpu_temp") or "Detecting..."))
        return devices

    def _read_furmark_renderer(self, test_key: str) -> str:
        suffix = self._furmark_log_suffix(test_key)
        for path in (self.engines_root / "FurMark").glob(f"*{suffix}*.txt"):
            try:
                match = FURMARK_RENDERER_PATTERN.search(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if match:
                # NVIDIA appends the bus and CPU extensions: "NVIDIA GeForce 940MX/PCIe/SSE2".
                return match.group(1).split("/")[0].strip()
        return ""

    def _start_event_watcher(self, test: TestDefinition, app_notes: list[str]) -> HardwareEventWatcher | None:
        def on_event(event: HardwareEvent, failure: bool) -> None:
            if self.manual_abort:
                return
            if failure:
                self._flag_phase_issue(f"{test.label}: {event.describe(failure)}", app_notes)
            else:
                self._flag_phase_warning(f"{test.label}: {event.describe(failure)}", app_notes)

        def on_unavailable(reason: str) -> None:
            if self._event_log_unavailable_reported:
                return
            self._event_log_unavailable_reported = True
            message = f"Windows event log check unavailable, hardware errors logged by Windows are not monitored: {reason}"
            app_notes.append(f"[APP] {message}")
            self._emit(EVENT_STATUS, message=message)

        if self._event_record_id is None:
            try:
                self._event_record_id = latest_record_id()
            except EventLogUnavailable as exc:
                on_unavailable(str(exc))
                return None
        watcher = HardwareEventWatcher(
            after_record_id=self._event_record_id,
            on_event=on_event,
            on_unavailable=on_unavailable,
            gpu_under_load=test.key in FURMARK_TEST_KEYS,
            report_warnings=self.report_event_log_warnings,
            query=query_hardware_events,
        )
        watcher.start()
        return watcher

    def _stop_event_watcher(self, watcher: HardwareEventWatcher) -> None:
        # The next phase picks up from here, so events between phases are not missed.
        watcher.stop()
        self._event_record_id = watcher.after_record_id

    def _flag_phase_issue(self, message: str, app_notes: list[str]) -> None:
        self._record_issue(message)
        app_notes.append(f"[APP] {message}")
        self._emit(EVENT_ALERT, message=message)

    def _flag_phase_warning(self, message: str, app_notes: list[str]) -> None:
        """Something worth checking that does not fail the test or reach the certificate."""
        with self._state_lock:
            self.warnings.append(message)
            self._phase_warnings.append(message)
        app_notes.append(f"{WARNING_NOTE_PREFIX}{message}")
        self._emit(EVENT_WARNING, message=message)

    def _record_issue(self, message: str, phase: bool = True) -> None:
        with self._state_lock:
            self.issues.append(message)
            if phase:
                self._phase_issues.append(message)

    @staticmethod
    def _append_notes_to_log(log_path: Path, notes: list[str]) -> None:
        if not notes:
            return
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                for note in notes:
                    handle.write(note + "\n")
        except OSError:
            pass

    def _append_engine_meta(self, meta: dict[str, Any]) -> None:
        meta_path = self.logs_dir / "engine_meta.json"
        existing = self.load_json(meta_path, [])
        if not isinstance(existing, list):
            existing = []
        existing.append(meta)
        self.save_json(meta_path, existing)

    def _launch_test_processes(self, commands: list[list[str]]) -> list[subprocess.Popen[str]]:
        processes: list[subprocess.Popen[str]] = []
        for command in commands:
            executable = Path(command[0])
            if not executable.is_file():
                raise FileNotFoundError(f"Engine executable not found: {executable}")
            self._emit(EVENT_LOG, message=f"$ {subprocess.list2cmdline(command)}")
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(executable.parent),
                creationflags=CREATE_NO_WINDOW,
            )
            processes.append(process)
            # Register immediately so an abort or thermal trip during launch also kills it.
            with self._monitor_lock:
                self.active_processes.append(process)
        return processes

    def _monitor_process_output(
        self,
        processes: list[subprocess.Popen[str]],
        log_path: Path,
        test_key: str,
        line_queue: queue.Queue[tuple[str, str] | None],
        monitor_done: threading.Event,
    ) -> None:
        # One blocking reader per pipe feeds a queue; this thread is the single writer
        # for the log file, the UI queue and issue flagging.
        open_streams = 0
        for process in processes:
            for stream_name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                if stream is None:
                    continue
                threading.Thread(
                    target=self._pump_stream,
                    args=(stream, stream_name, line_queue),
                    name=f"engine-{stream_name}-reader",
                    daemon=True,
                ).start()
                open_streams += 1

        with open(log_path, "a", encoding="utf-8") as f:
            drain_deadline: float | None = None
            while open_streams > 0 or not monitor_done.is_set():
                if monitor_done.is_set():
                    if drain_deadline is None:
                        drain_deadline = time.monotonic() + MONITOR_DRAIN_SECONDS
                    elif time.monotonic() > drain_deadline:
                        break
                try:
                    item = line_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is None:
                    open_streams -= 1
                    continue
                self._write_monitor_item(item, f, test_key)

            while True:
                try:
                    item = line_queue.get_nowait()
                except queue.Empty:
                    break
                if item is not None:
                    self._write_monitor_item(item, f, test_key)

    def _write_monitor_item(self, item: tuple[str, str], f: Any, test_key: str) -> None:
        stream_name, text = item
        if stream_name == "note":
            f.write(text + "\n")
            f.flush()
            return
        self._handle_engine_line(text.rstrip("\r\n"), f, stream_name, test_key)

    @staticmethod
    def _pump_stream(stream: Any, stream_name: str, line_queue: queue.Queue[tuple[str, str] | None]) -> None:
        try:
            for line in stream:
                line_queue.put((stream_name, line))
        except (OSError, ValueError):
            pass
        finally:
            line_queue.put(None)

    def _handle_engine_line(self, line: str, f: Any, stream_name: str, test_key: str = "") -> None:
        if not line.strip():
            return

        full_line = f"{STDERR_PREFIX}{line}" if stream_name == "stderr" else line
        is_error = is_error_line(line, test_key)

        self._emit(EVENT_LOG, message=full_line, is_error=is_error)
        f.write(full_line + "\n")
        f.flush()

        if is_error:
            # Every error line stays red in the console and the log; only the first few become issues.
            with self._state_lock:
                self._phase_engine_errors += 1
                listed = self._phase_engine_errors <= MAX_ENGINE_ERRORS_LISTED
            if listed:
                self._record_issue(f"Engine output flagged issue: {line}")
                self._emit(EVENT_ALERT, message=f"Engine output error: {line}")

    @staticmethod
    def _empty_phase_max() -> dict[str, float]:
        return {"cpu_temp": 0.0, "gpu_temp": 0.0, "ram_load": 0.0}

    def _handle_telemetry_sample(self, sample: TelemetrySample) -> None:
        self._emit(EVENT_TELEMETRY, sample=asdict(sample))
        if not self.session_active_event.is_set():
            return

        cpu_temp = sample.cpu_temp or 0.0
        gpu_temp = sample.gpu_temp or 0.0
        ram_load = sample.ram_load or 0.0
        cpu_power = sample.cpu_power or 0.0
        gpu_power = sample.gpu_power or 0.0
        total_power = cpu_power + gpu_power

        with self._state_lock:
            self.max_cpu_temp = max(self.max_cpu_temp, cpu_temp)
            self.max_gpu_temp = max(self.max_gpu_temp, gpu_temp)
            self.max_ram_load = max(self.max_ram_load, ram_load)
            self.max_cpu_power = max(self.max_cpu_power, cpu_power)
            self.max_gpu_power = max(self.max_gpu_power, gpu_power)
            self.max_total_power = max(self.max_total_power, total_power)
            self._phase_max["cpu_temp"] = max(self._phase_max["cpu_temp"], cpu_temp)
            self._phase_max["gpu_temp"] = max(self._phase_max["gpu_temp"], gpu_temp)
            self._phase_max["ram_load"] = max(self._phase_max["ram_load"], ram_load)

        if (
            (sample.cpu_temp is not None and sample.cpu_temp > self.cpu_temp_limit_c)
            or (sample.gpu_temp is not None and sample.gpu_temp > self.gpu_temp_limit_c)
        ):
            with self._state_lock:
                first_trip = THERMAL_ABORT_MESSAGE not in self.issues
                if first_trip:
                    self._record_issue(THERMAL_ABORT_MESSAGE)
            if first_trip:
                self._emit(EVENT_ALERT, message=THERMAL_ABORT_MESSAGE)
            self.session_active_event.clear()
            self.stop_event.set()
            self._stop_active_processes()

    def _handle_telemetry_error(self, exc: Exception) -> None:
        message = f"Telemetry error: {exc}"
        session_running = self.session_active_event.is_set()
        with self._state_lock:
            if message not in self.issues:
                self._record_issue(message, phase=session_running)
        self._emit(EVENT_ALERT, message=message)
        # Without telemetry the thermal kill switch is blind, so stop any running test.
        self.session_active_event.clear()
        self.stop_event.set()
        self._stop_active_processes()

    def _finalize_session(self, settings: dict[str, Any], aborted: bool = False) -> SessionSummary:
        if aborted and not any(result["status"] == "ABORTED" for result in self.test_results):
            if self.active_test_key:
                label = self._test_catalog()[self.active_test_key]
                self.test_results.append(
                    {"key": self.active_test_key, "test_name": label, "label": label, "duration_minutes": 0, "status": "ABORTED", "selected_ram_gb": 0.0}
                )

        with self._state_lock:
            issues = self.issues[:]
            warnings = self.warnings[:]
            maxima = {
                "max_cpu_temp": self.max_cpu_temp,
                "max_gpu_temp": self.max_gpu_temp,
                "max_ram_load": self.max_ram_load,
                "max_cpu_power": self.max_cpu_power,
                "max_gpu_power": self.max_gpu_power,
                "max_total_power": self.max_total_power,
            }

        clean_run = (
            not aborted
            and bool(self.test_results)
            and not issues
            and all(result["status"] == "PASSED" for result in self.test_results)
        )
        if aborted:
            verdict = "ABORTED"
        else:
            verdict = "CERTIFIED STABLE" if clean_run else "UNSTABLE"
        completed_at = datetime.now()

        # Build the final run summary JSON artifact
        summary = SessionSummary(
            run_id=self.run_id,
            started_at=self.started_at.isoformat() if self.started_at else completed_at.isoformat(),
            completed_at=completed_at,
            machine_name=socket.gethostname(),
            tests=self.test_results,
            issues=issues or ["No issues recorded."],
            clean_run=clean_run,
            verdict=verdict,
            event_log_monitored=os.name == "nt" and not self._event_log_unavailable_reported,
            warnings=warnings,
            **maxima,
        )

        if self.generate_report and not aborted:
            report_path = self._build_report_path(settings, completed_at)
            try:
                build_stability_report(report_path, settings, asdict(summary))
                summary.report_path = str(report_path)
            except Exception as exc:
                # A report failure (locked file, bad logo, full disk) must not lose the run results.
                summary.report_error = f"Certificate generation failed: {exc}"
                self._emit(EVENT_ALERT, message=summary.report_error)

        summary_payload = asdict(summary)
        summary_payload["completed_at"] = completed_at.isoformat()
        summary_payload["final_verdict"] = verdict

        if self.run_dir is not None:
            self.save_json(self.run_dir / "run_summary.json", summary_payload)
            self.save_json(
                self.run_dir / "telemetry_summary.json",
                {
                    "run_id": self.run_id,
                    **maxima,
                    "cpu_temp_limit_c": self.cpu_temp_limit_c,
                    "gpu_temp_limit_c": self.gpu_temp_limit_c,
                    "phases": [
                        {
                            "test_name": result.get("test_name", result.get("label", "")),
                            "max_cpu_temp_c": result.get("max_cpu_temp_c", 0.0),
                            "max_gpu_temp_c": result.get("max_gpu_temp_c", 0.0),
                            "max_ram_percent": result.get("max_ram_percent", 0.0),
                        }
                        for result in self.test_results
                    ],
                },
            )

        return summary

    def _build_report_path(self, settings: dict[str, Any], completed_at: datetime) -> Path:
        customer_name = ""
        if settings.get("include_customer_name") and settings.get("customer_name"):
            customer_name = self._sanitize_report_filename_component(str(settings["customer_name"]))

        machine_name = ""
        if settings.get("include_machine_name") and settings.get("certificate_machine_name"):
            machine_name = self._sanitize_report_filename_component(str(settings["certificate_machine_name"]))

        if not customer_name:
            stem = f"stability_certificate_{completed_at.strftime('%Y%m%d_%H%M%S')}"
        elif machine_name:
            stem = f"{customer_name}_{machine_name}_{completed_at.strftime('%d%m%Y_%H%M')}"
        else:
            stem = f"{customer_name}_{completed_at.strftime('%d%m%Y_%H%M')}"

        # Never overwrite an earlier certificate (or fail on one open in a PDF viewer).
        candidate = self.report_dir / f"{stem}.pdf"
        suffix = 2
        while candidate.exists():
            candidate = self.report_dir / f"{stem}_{suffix}.pdf"
            suffix += 1
        return candidate

    @staticmethod
    def _sanitize_report_filename_component(value: str) -> str:
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip()
        sanitized = re.sub(r"\s+", "", sanitized)
        sanitized = sanitized.rstrip(".")
        return sanitized

    def _build_plan(self, selections: dict[str, int], ram_fill_percent: float, ram_safety_margin_gb: float, target_drive: str, gpu_resolution: str, vram_gb: int = 0) -> list[TestDefinition]:
        catalog = self._test_catalog()
        plan = []
        for key in ("cpu", "ram", "gpu", "vram", "storage_read", "storage_write", "psu"):
            duration = int(selections.get(key, 0) or 0)
            if duration > 0:
                plan.append(TestDefinition(
                    key=key, label=catalog[key], duration_minutes=duration,
                    ram_fill_percent=ram_fill_percent, ram_safety_margin_gb=ram_safety_margin_gb,
                    target_drive=target_drive, gpu_resolution=gpu_resolution, vram_gb=vram_gb,
                ))
        if not plan:
            raise RuntimeError("Select at least one test with a duration greater than zero.")
        if any(test.key == "ram" for test in plan):
            low, high = RAM_FILL_PERCENT_RANGE
            if not (math.isfinite(ram_fill_percent) and low <= ram_fill_percent <= high):
                raise RuntimeError(f"RAM fill must be between {low:g} and {high:g}%.")
            if not (math.isfinite(ram_safety_margin_gb) and ram_safety_margin_gb >= RAM_SAFETY_MARGIN_MIN_GB):
                raise RuntimeError(f"RAM safety margin must be at least {RAM_SAFETY_MARGIN_MIN_GB:g} GB.")
        if any(test.key == "vram" for test in plan) and vram_gb not in FURMARK_VRAM_TEST_SIZES_GB:
            sizes = ", ".join(str(size) for size in FURMARK_VRAM_TEST_SIZES_GB)
            raise RuntimeError(f"VRAM to test must be one of: {sizes} GB.")
        return plan

    @staticmethod
    def _test_catalog() -> dict[str, str]:
        return {
            "cpu": "CPU Stress",
            "ram": "RAM Stress",
            "gpu": "GPU Stress",
            "vram": "GPU Memory (VRAM) Stress",
            "storage_read": "Storage Stress (Read)",
            "storage_write": "Storage Stress (Write)",
            "psu": "PSU / System Stress",
        }

    def _build_commands(self, test: TestDefinition, native_log: str) -> list[list[str]]:
        seconds = test.duration_minutes * 60
        if test.key == "cpu":
            return [self._build_ycruncher_command(seconds, native_log, memory_mb=None, algorithms=select_ycruncher_tests("cpu", seconds))]
        if test.key == "ram":
            # Sized now rather than at Start, from the memory Windows and other programs use at this moment.
            test.ram_gb = round(ram_test_size_gb(test.ram_fill_percent, test.ram_safety_margin_gb), 1)
            if test.ram_gb < RAM_TEST_MIN_GB:
                raise RuntimeError(
                    f"only {test.ram_gb:g} GB could be tested at {test.ram_fill_percent:g}% with a "
                    f"{test.ram_safety_margin_gb:g} GB safety margin; too much memory was already in use"
                )
            self._emit(
                EVENT_STATUS,
                message=f"{test.label}: testing {test.ram_gb:g} GB (fill to {test.ram_fill_percent:g}%, {test.ram_safety_margin_gb:g} GB safety margin)",
            )
            return [self._build_ycruncher_command(seconds, native_log, memory_mb=int(test.ram_gb * 1024), algorithms=select_ycruncher_tests("ram", seconds))]
        if test.key == "gpu":
            return [self._build_furmark_command(seconds, test.gpu_resolution, self._furmark_log_suffix(test.key))]
        if test.key == "vram":
            return [self._build_furmark_command(seconds, test.gpu_resolution, self._furmark_log_suffix(test.key), vram_gb=test.vram_gb)]
        if test.key == "storage_read":
            return [self._build_diskspd_command(seconds, is_write=False, target_drive=test.target_drive)]
        if test.key == "storage_write":
            return [self._build_diskspd_command(seconds, is_write=True, target_drive=test.target_drive)]
        if test.key == "psu":
            return [
                self._build_ycruncher_command(seconds, native_log, memory_mb=None, algorithms=select_ycruncher_tests("psu", seconds)),
                self._build_furmark_command(seconds, test.gpu_resolution, self._furmark_log_suffix(test.key)),
            ]
        raise RuntimeError(f"Unsupported test: {test.key}")

    def _build_ycruncher_command(self, duration_seconds: int, native_log: str, memory_mb: int | None, algorithms: tuple[str, ...]) -> list[str]:
        executable = self.engines_root / "y-cruncher" / "y-cruncher.exe"
        command = [
            str(executable),
            "skip-warnings",
            "pause:-1",
            "status:none",
        ]
        if native_log:
            command.append(f"logfile:{native_log}")

        # -D is per algorithm. Split the phase across all of them so short phases run the whole
        # schedule instead of spending the entire run in the first test.
        per_test_seconds = max(1, min(YCRUNCHER_PER_TEST_SECONDS, duration_seconds // max(1, len(algorithms))))
        command.extend([
            "stress",
            f"-D:{per_test_seconds}",
            f"-TL:{duration_seconds}",
        ])

        if memory_mb is not None:
            command.append(f"-M:{memory_mb}m")
        else:
            command.append("-M:2g")

        command.extend(algorithms)
        return command

    @staticmethod
    def parse_resolution(resolution: str) -> tuple[int, int] | None:
        match = re.fullmatch(r"\s*(\d{3,5})\s*[xX]\s*(\d{3,5})\s*", resolution or "")
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    def _furmark_log_suffix(self, test_key: str) -> str:
        return f"_{self.run_id}_{test_key}"

    def _build_furmark_command(self, duration_seconds: int, resolution: str, log_suffix: str, vram_gb: int = 0) -> list[str]:
        executable = self.engines_root / "FurMark" / "furmark.exe"
        width, height = self.parse_resolution(resolution) or (1280, 720)

        # The artifact scanner checks rendered frames for corruption: the GPU equivalent of
        # y-cruncher's result verification. Its reports can go to FurMark's own log file rather
        # than stdout, so traces stay enabled (with a per-run suffix) and the log is collected.
        command = [
            str(executable),
            "--demo",
            "furmark-gl",
            "--width",
            str(width),
            "--height",
            str(height),
            "--max-time",
            str(duration_seconds),
            "--no-score-box",
            "--artifact-scanner",
            "--logfile-suffix",
            log_suffix,
            "--gpu-monitor-print",
            "--no-osi",
            "--no-resize",
            "--hpgfx",
            "1",
        ]
        if vram_gb:
            # Fills that much video memory with FurMark data, read back every frame; corrupted memory
            # shows up as artifacts. Only the OpenGL demo supports it.
            command.extend(["--furmark-vram-test-gb", str(vram_gb)])
        return command

    @staticmethod
    def _diskspd_target_dir(target_drive: str) -> Path:
        drive_path = Path(target_drive) if target_drive else Path(tempfile.gettempdir())
        return drive_path / "stress_tester_diskspd"

    def _build_diskspd_command(self, duration_seconds: int, is_write: bool, target_drive: str) -> list[str]:
        executable = self.engines_root / "DiskSpd" / "amd64" / "diskspd.exe"
        temp_dir = self._diskspd_target_dir(target_drive)
        temp_dir.mkdir(parents=True, exist_ok=True)
        target = temp_dir / "diskspd_testfile.dat"

        # A small file is served from the drive's DRAM/SLC cache and never loads the flash or
        # the controller for long; size it up to DISKSPD_MAX_FILE_GIB within the free space.
        free_gib = shutil.disk_usage(temp_dir).free / (1024 ** 3)
        file_gib = int(max(1, min(DISKSPD_MAX_FILE_GIB, free_gib * DISKSPD_FREE_SPACE_FRACTION)))

        command = [
            str(executable),
            "-b64K",
            # 4 threads x 32 outstanding I/Os saturates NVMe drives (the old 2 x 4 left them idle),
            # which is what brings out thermal throttling and controller or link instability.
            "-o32",
            "-t4",
            "-r",
            f"-d{duration_seconds}",
            "-Sh",
            "-w100" if is_write else "-w0",
            "-L",
            f"-c{file_gib}G",
        ]
        if is_write:
            # Random, incompressible data so compressing/deduplicating controllers do real work.
            command.append("-Z1M")
        command.append(str(target))
        return command

    def _collect_furmark_log(self, test_key: str, started_at: datetime) -> tuple[Path | None, list[str]]:
        """Move FurMark's own log for this phase into the run's logs and return any artifact reports."""
        furmark_dir = self.engines_root / "FurMark"
        suffix = self._furmark_log_suffix(test_key)
        started = started_at.timestamp()

        def modified(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        candidates = [path for path in furmark_dir.glob("*.txt") if suffix in path.name]
        per_run_file = bool(candidates)
        if not candidates:
            # Fall back to the default log name in case the suffix was not applied.
            candidates = [path for path in furmark_dir.glob("_furmark_log*.txt") if modified(path) >= started]
        if not candidates:
            return None, []

        source = max(candidates, key=modified)
        destination = self.logs_dir / f"{test_key}_furmark.log"
        try:
            if per_run_file:
                shutil.move(str(source), str(destination))  # don't pile per-run logs up in Engines\FurMark
            else:
                shutil.copyfile(source, destination)
            text = destination.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._emit(EVENT_STATUS, message=f"Could not collect the FurMark log: {exc}")
            return None, []
        return destination, [line.strip() for line in text.splitlines() if FURMARK_ARTIFACT_PATTERN.search(line)]

    def _cleanup_diskspd_target(self, target_drive: str) -> None:
        # Don't leave a multi-GB scratch file on the customer's drive.
        temp_dir = self._diskspd_target_dir(target_drive)
        try:
            (temp_dir / "diskspd_testfile.dat").unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._emit(EVENT_STATUS, message=f"Could not remove DiskSpd test file: {exc}")
            return
        try:
            temp_dir.rmdir()
        except OSError:
            pass

    def _write_state_file(self, current_test: str) -> None:
        self.save_json(
            self.state_path,
            {
                "status": "running",
                "current_test": current_test,
                "run_id": self.run_id,
                # Crash recovery lists the Windows events logged after this one.
                "event_record_id": self._event_record_id,
            },
        )

    def _emit(self, event_type: str, **payload: Any) -> None:
        self.event_queue.put({"type": event_type, **payload})

    def _ensure_telemetry_thread(self) -> None:
        if self.telemetry_thread and self.telemetry_thread.is_alive():
            return

        self.telemetry_stop_event.clear()
        self.telemetry_thread = threading.Thread(
            target=self.telemetry.run_polling_loop,
            args=(self.telemetry_stop_event, self._handle_telemetry_sample),
            kwargs={"error_callback": self._handle_telemetry_error},
            name="telemetry-worker",
            daemon=True,
        )
        self.telemetry_thread.start()

    def _stop_active_processes(self) -> None:
        with self._monitor_lock:
            processes = self.active_processes
            self.active_processes = []

        for process in processes:
            if process.poll() is not None:
                continue
            try:
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
            except psutil.Error:
                pass

            try:
                process.kill()
            except OSError:
                pass

        for process in processes:
            try:
                process.wait(timeout=PROCESS_KILL_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
