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
# Events are logged a moment after they happen and clocks are compared at second precision.
QUERY_MARGIN_SECONDS = 60
POLL_SECONDS = 30.0
# Some systems log a corrected PCIe error every second; report the first few, count the rest.
MAX_REPORTED_PER_WATCHER = 10
MESSAGE_MAX_CHARS = 300


@dataclass(frozen=True)
class EventSource:
    provider: str
    meaning: str
    max_level: int = 3  # 1 critical, 2 error, 3 warning
    event_ids: frozenset[int] | None = None


# System-log sources that record hardware trouble even when the stress engine itself notices nothing:
# corrected CPU / memory / PCIe errors, GPU driver resets, storage controller resets and I/O retries.
WATCHED_EVENT_SOURCES = (
    EventSource("Microsoft-Windows-WHEA-Logger", "hardware error (WHEA) reported by the CPU, memory or PCIe"),
    EventSource("Display", "the display driver stopped responding and was reset (TDR)", event_ids=frozenset({4101})),
    EventSource("nvlddmkm", "NVIDIA graphics driver error", max_level=2),
    EventSource("amdkmdag", "AMD graphics driver error", max_level=2),
    EventSource("disk", "disk I/O error or retry"),
    EventSource("stornvme", "NVMe controller error or reset"),
    EventSource("storahci", "SATA controller error or reset"),
    EventSource("Ntfs", "file system error", max_level=2),
    EventSource("Microsoft-Windows-Ntfs", "file system error", max_level=2),
    # Logged after the restart that follows a crash; found by crash recovery.
    EventSource("Microsoft-Windows-Kernel-Power", "the system restarted without shutting down cleanly", event_ids=frozenset({41})),
    EventSource("Microsoft-Windows-WER-SystemErrorReporting", "the system crashed with a blue screen", event_ids=frozenset({1001})),
)
SOURCES_BY_PROVIDER = {source.provider.lower(): source for source in WATCHED_EVENT_SOURCES}


@dataclass(frozen=True)
class HardwareEvent:
    record_id: int
    time_created: datetime  # UTC
    provider: str
    event_id: int
    level: int
    meaning: str
    message: str

    def describe(self) -> str:
        local_time = self.time_created.astimezone().strftime("%H:%M:%S")
        text = f"Windows logged at {local_time}: {self.meaning} ({self.provider} event {self.event_id})"
        return f"{text}: {self.message}" if self.message else text


class EventLogUnavailable(Exception):
    pass


def _build_query(window_ms: int) -> str:
    providers = " or ".join(f"@Name='{source.provider}'" for source in WATCHED_EVENT_SOURCES)
    return (
        f"*[System[Provider[{providers}] and (Level=1 or Level=2 or Level=3)"
        f" and TimeCreated[timediff(@SystemTime) <= {window_ms}]]]"
    )


def query_hardware_events(since: datetime) -> list[HardwareEvent]:
    """Watched System-log events created at or after `since` (aware or local-naive), oldest first."""
    if os.name != "nt":
        return []
    since_utc = since.astimezone(timezone.utc)
    window_ms = int(((datetime.now(timezone.utc) - since_utc).total_seconds() + QUERY_MARGIN_SECONDS) * 1000)
    try:
        completed = subprocess.run(
            [
                "wevtutil", "qe", "System",
                f"/q:{_build_query(max(window_ms, 1000))}",
                "/f:RenderedXml",
                f"/c:{QUERY_MAX_EVENTS}",
            ],
            capture_output=True,
            timeout=QUERY_TIMEOUT_SECONDS,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EventLogUnavailable(str(exc)) from exc
    if completed.returncode != 0:
        detail = _decode(completed.stderr).strip() or f"wevtutil exit code {completed.returncode}"
        raise EventLogUnavailable(detail)
    return [event for event in parse_events(_decode(completed.stdout)) if event.time_created >= since_utc]


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
    events.sort(key=lambda e: (e.time_created, e.record_id))
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
        meaning=source.meaning,
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
    """Polls the System log in the background while a phase runs and reports each new event once."""

    def __init__(
        self,
        since: datetime,
        on_event: Callable[[HardwareEvent], None],
        on_unavailable: Callable[[str], None],
        seen_record_ids: set[int],
        query: Callable[[datetime], list[HardwareEvent]] = query_hardware_events,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.since = since
        self._on_event = on_event
        self._on_unavailable = on_unavailable
        self._seen = seen_record_ids
        self._query = query
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._unavailable_reported = False
        self._thread: threading.Thread | None = None
        self.reported = 0
        self.suppressed = 0

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
            try:
                events = self._query(self.since)
            except EventLogUnavailable as exc:
                if not self._unavailable_reported:
                    self._unavailable_reported = True
                    self._on_unavailable(str(exc))
                return
            for event in events:
                if event.record_id in self._seen:
                    continue
                self._seen.add(event.record_id)
                if self.reported >= MAX_REPORTED_PER_WATCHER:
                    self.suppressed += 1
                    continue
                self.reported += 1
                self._on_event(event)

    def _run(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            self.check()
