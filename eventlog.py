from __future__ import annotations

import os
import re
import subprocess
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EVENT_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
QUERY_TIMEOUT_SECONDS = 20
QUERY_MAX_EVENTS = 200
POLL_SECONDS = 30.0
# Some systems log a corrected PCIe error every second; report the first few, count the rest.
MAX_REPORTED_PER_WATCHER = 10
MESSAGE_MAX_CHARS = 300

FAILURE_NOTE = "This points to instability, which can come from the hardware or from software such as drivers or BIOS settings."
WARNING_NOTE = "Worth checking, but not by itself a sign of a hardware or software failure."


@dataclass(frozen=True)
class EventSource:
    provider: str
    meaning: str
    max_level: int = 3  # 1 critical, 2 error, 3 warning
    event_ids: frozenset[int] | None = None
    # Events at or below this level fail the phase; the rest are only warnings worth checking.
    failure_max_level: int = 0
    failure_meaning: str = ""
    # A failure only while the phase loads the GPU, otherwise a warning.
    failure_needs_gpu_load: bool = False
    # Logged on the boot after a crash, so only crash recovery looks for it.
    after_restart: bool = False


# System-log sources that record hardware trouble even when the stress engine itself notices nothing:
# CPU / memory / PCIe errors, GPU driver resets, storage controller resets and I/O retries.
WATCHED_EVENT_SOURCES = (
    EventSource(
        "Microsoft-Windows-WHEA-Logger",
        "corrected hardware error (WHEA) reported by the CPU, memory or PCIe",
        failure_max_level=2,
        failure_meaning="uncorrected hardware error (WHEA) reported by the CPU, memory or PCIe",
    ),
    EventSource(
        "Display",
        "the display driver stopped responding and was reset (TDR)",
        event_ids=frozenset({4101}),
        failure_max_level=3,
        failure_needs_gpu_load=True,
    ),
    EventSource("nvlddmkm", "NVIDIA graphics driver error", max_level=2),
    EventSource("amdkmdag", "AMD graphics driver error", max_level=2),
    EventSource("disk", "disk I/O error or retry"),
    EventSource("stornvme", "NVMe controller error or reset"),
    EventSource("storahci", "SATA controller error or reset"),
    EventSource("Ntfs", "file system error", max_level=2),
    EventSource("Microsoft-Windows-Ntfs", "file system error", max_level=2),
    EventSource(
        "Microsoft-Windows-Kernel-Power",
        "the system restarted without shutting down cleanly",
        event_ids=frozenset({41}),
        failure_max_level=3,
        after_restart=True,
    ),
    EventSource(
        "Microsoft-Windows-WER-SystemErrorReporting",
        "the system crashed with a blue screen",
        event_ids=frozenset({1001}),
        failure_max_level=3,
        after_restart=True,
    ),
)
SOURCES_BY_PROVIDER = {source.provider.lower(): source for source in WATCHED_EVENT_SOURCES}


@dataclass(frozen=True)
class HardwareEvent:
    record_id: int
    time_created: datetime  # UTC
    provider: str
    event_id: int
    level: int
    message: str

    @property
    def source(self) -> EventSource:
        return SOURCES_BY_PROVIDER[self.provider.lower()]

    def is_failure(self, gpu_under_load: bool) -> bool:
        """True when the event shows the hardware failing under test rather than something worth checking."""
        source = self.source
        if self.level > source.failure_max_level:
            return False
        return gpu_under_load or not source.failure_needs_gpu_load

    def describe(self, failure: bool) -> str:
        local_time = self.time_created.astimezone().strftime("%H:%M:%S")
        meaning = (self.source.failure_meaning or self.source.meaning) if failure else self.source.meaning
        text = f"Windows logged at {local_time}: {meaning} ({self.provider} event {self.event_id})"
        if self.message:
            text = f"{text}: {self.message}"
        return f"{text.rstrip('.')}. {FAILURE_NOTE if failure else WARNING_NOTE}"


@dataclass(frozen=True)
class EventPage:
    events: list[HardwareEvent]  # watched events, oldest first
    last_record_id: int  # newest record the query returned, watched or not
    full: bool  # the query hit its event limit, so more events may follow


class EventLogUnavailable(Exception):
    pass


def _build_query(after_record_id: int, include_after_restart: bool) -> str:
    providers = " or ".join(
        f"@Name='{source.provider}'"
        for source in WATCHED_EVENT_SOURCES
        if include_after_restart or not source.after_restart
    )
    return (
        f"*[System[Provider[{providers}] and (Level=1 or Level=2 or Level=3)"
        f" and EventRecordID > {after_record_id}]]"
    )


def _run_wevtutil(arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["wevtutil", "qe", "System", *arguments],
            capture_output=True,
            timeout=QUERY_TIMEOUT_SECONDS,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EventLogUnavailable(str(exc)) from exc
    if completed.returncode != 0:
        detail = _decode(completed.stderr).strip() or f"wevtutil exit code {completed.returncode}"
        raise EventLogUnavailable(detail)
    return _decode(completed.stdout)


def latest_record_id() -> int:
    """Number of the newest System-log event. Later queries only look at events numbered after it,
    which, unlike timestamps, stays correct when the clock is wrong or changes."""
    if os.name != "nt":
        return 0
    record_ids = _record_ids(_run_wevtutil(["/rd:true", "/c:1", "/f:xml"]))
    return max(record_ids, default=0)


def query_hardware_events(after_record_id: int, include_after_restart: bool = False) -> EventPage:
    """Watched System-log events numbered after `after_record_id`, at most QUERY_MAX_EVENTS of them."""
    if os.name != "nt":
        return EventPage([], after_record_id, False)
    xml_text = _run_wevtutil(
        [
            f"/q:{_build_query(after_record_id, include_after_restart)}",
            "/f:RenderedXml",
            f"/c:{QUERY_MAX_EVENTS}",
        ]
    )
    record_ids = _record_ids(xml_text)
    return EventPage(
        events=parse_events(xml_text),
        last_record_id=max(record_ids, default=after_record_id),
        full=len(record_ids) >= QUERY_MAX_EVENTS,
    )


def _record_ids(xml_text: str) -> list[int]:
    return [int(value) for value in re.findall(r"<EventRecordID>(\d+)</EventRecordID>", xml_text)]


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    if b"\x00" in raw[:64]:
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_events(xml_text: str) -> list[HardwareEvent]:
    """Parse wevtutil /f:RenderedXml output (a run of <Event> elements without a root)."""
    body = re.sub(r"<\?xml[^>]*\?>", "", xml_text).strip()
    if not body:
        return []
    try:
        root = ET.fromstring(f"<Events>{body}</Events>")
    except ET.ParseError as exc:
        raise EventLogUnavailable(f"unreadable event log output: {exc}") from exc

    events: list[HardwareEvent] = []
    for element in root.iter(f"{EVENT_NS}Event"):
        event = _parse_event(element)
        if event is not None:
            events.append(event)
    events.sort(key=lambda e: e.record_id)
    return events


def _parse_event(element: ET.Element) -> HardwareEvent | None:
    system = element.find(f"{EVENT_NS}System")
    if system is None:
        return None
    provider_element = system.find(f"{EVENT_NS}Provider")
    provider = provider_element.get("Name", "") if provider_element is not None else ""
    source = SOURCES_BY_PROVIDER.get(provider.lower())
    try:
        event_id = int(system.findtext(f"{EVENT_NS}EventID", "0"))
        level = int(system.findtext(f"{EVENT_NS}Level", "0"))
        record_id = int(system.findtext(f"{EVENT_NS}EventRecordID", "0"))
    except ValueError:
        return None
    if source is None or not 1 <= level <= source.max_level:
        return None
    if source.event_ids is not None and event_id not in source.event_ids:
        return None
    time_element = system.find(f"{EVENT_NS}TimeCreated")
    created = _parse_system_time(time_element.get("SystemTime", "") if time_element is not None else "")
    if created is None:
        return None

    message = element.findtext(f"{EVENT_NS}RenderingInfo/{EVENT_NS}Message") or ""
    if not message.strip():
        data = [d.text.strip() for d in element.iter(f"{EVENT_NS}Data") if d.text and d.text.strip()]
        message = ", ".join(data)
    return HardwareEvent(
        record_id=record_id,
        time_created=created,
        provider=provider,
        event_id=event_id,
        level=level,
        message=_compact_message(message),
    )


def _parse_system_time(value: str) -> datetime | None:
    # e.g. 2026-10-19T18:17:32.4310000Z (seven fractional digits, which fromisoformat may reject)
    match = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?", value)
    if not match:
        return None
    created = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    fraction = match.group(2)
    if fraction:
        created = created.replace(microsecond=int(fraction[:6].ljust(6, "0")))
    return created


def _compact_message(message: str) -> str:
    lines = [
        line.strip()
        for line in message.splitlines()
        if line.strip() and not line.strip().lower().startswith("the details view of this entry")
    ]
    text = " | ".join(lines)
    return text if len(text) <= MESSAGE_MAX_CHARS else text[: MESSAGE_MAX_CHARS - 3] + "..."


class HardwareEventWatcher:
    """Polls the System log in the background while a phase runs and reports each new event once.

    Failures (uncorrected hardware errors, GPU driver resets under GPU load) are always reported;
    warnings worth checking only when `report_warnings` is set.
    """

    def __init__(
        self,
        after_record_id: int,
        on_event: Callable[[HardwareEvent, bool], None],
        on_unavailable: Callable[[str], None],
        gpu_under_load: bool,
        report_warnings: bool,
        query: Callable[[int], EventPage] = query_hardware_events,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.after_record_id = after_record_id
        self._on_event = on_event
        self._on_unavailable = on_unavailable
        self._gpu_under_load = gpu_under_load
        self._report_warnings = report_warnings
        self._query = query
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._unavailable_reported = False
        self._thread: threading.Thread | None = None
        self._reported = {True: 0, False: 0}  # keyed by is_failure
        self.suppressed_failures = 0
        self.suppressed_warnings = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="event-log-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling and run one last check, so events up to now are always reported."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=QUERY_TIMEOUT_SECONDS + 5)
            self._thread = None
        self.check()

    def check(self) -> None:
        with self._lock:
            while True:
                try:
                    page = self._query(self.after_record_id)
                except EventLogUnavailable as exc:
                    if not self._unavailable_reported:
                        self._unavailable_reported = True
                        self._on_unavailable(str(exc))
                    return
                for event in page.events:
                    self._report(event)
                if page.last_record_id <= self.after_record_id:
                    return
                self.after_record_id = page.last_record_id
                if not page.full:
                    return

    def _report(self, event: HardwareEvent) -> None:
        failure = event.is_failure(self._gpu_under_load)
        if not failure and not self._report_warnings:
            return
        if self._reported[failure] >= MAX_REPORTED_PER_WATCHER:
            if failure:
                self.suppressed_failures += 1
            else:
                self.suppressed_warnings += 1
            return
        self._reported[failure] += 1
        self._on_event(event, failure)

    def _run(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            self.check()
