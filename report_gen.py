from __future__ import annotations

from pathlib import Path
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


METHODOLOGY_TEXT = (
    "This system was stress-tested using enterprise-grade workloads. "
    "The CPU was tested with a mix of y-cruncher computations that verify their own results, covering "
    "peak boost clocks, maximum heat and power, and load transitions. Memory and the memory controller "
    "were tested with y-cruncher's large-working-set tests. The GPU was stressed with FurMark while an "
    "artifact scanner checked rendered frames for corruption; the video memory test fills the selected "
    "amount of VRAM under the same load. Storage was tested with sustained high-queue-depth random I/O, "
    "monitored for I/O errors and stalls. The PSU was validated by stressing the CPU and GPU to maximum "
    "power simultaneously. "
    "Passing these tests indicates strong overall system stability."
)
EVENT_LOG_TEXT = (
    " Throughout every test, the Windows event log was monitored for uncorrected hardware errors (WHEA) "
    "and graphics driver resets."
)


def build_stability_report(output_path: Path, settings: dict, summary: dict) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(output_path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm)
    styles = getSampleStyleSheet()
    story = []

    logo = _load_logo(settings.get("logo_path"))
    if logo is not None:
        story.append(logo)
        story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("System Stability Certificate", styles["Title"]))
    story.append(Paragraph(f"Completed: {summary['completed_at'].strftime('%Y-%m-%d %H:%M:%S')}", styles["Normal"]))
    if summary.get("run_id"):
        story.append(Paragraph(f"Run ID: {_escape(str(summary['run_id']))}", styles["Normal"]))
    certificate_details = _certificate_details_rows(settings)
    if certificate_details:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph("Certificate Details", styles["Heading2"]))
        details_table = Table(certificate_details, colWidths=[50 * mm, 100 * mm])
        details_table.setStyle(_table_style())
        story.append(details_table)
    story.append(Spacer(1, 5 * mm))

    verdict = summary["verdict"]
    verdict_style = ParagraphStyle(
        "Verdict",
        parent=styles["Heading1"],
        textColor=colors.HexColor("#37c871") if verdict == "CERTIFIED STABLE" else colors.HexColor("#ff5b5b"),
    )
    story.append(Paragraph(verdict, verdict_style))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Test Pattern Summary", styles["Heading2"]))
    rows = [["Test", "Duration", "Memory Tested", "Status"]]
    for row in summary["tests"]:
        duration_minutes = row.get("duration_minutes") or 0
        ram_gb = row.get("selected_ram_gb") or 0
        vram_gb = row.get("selected_vram_gb") or 0
        if ram_gb:
            memory = f"{ram_gb:g} GB"
        elif vram_gb:
            memory = f"{vram_gb:g} GB VRAM"
        else:
            memory = "-"
        rows.append([
            row.get("label") or row.get("test_name", ""),
            f"{duration_minutes} min" if duration_minutes else "-",
            memory,
            row["status"],
        ])
    summary_table = Table(rows, colWidths=[70 * mm, 25 * mm, 25 * mm, 30 * mm])
    summary_table.setStyle(_table_style())
    story.append(summary_table)
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Maximum Telemetry", styles["Heading2"]))
    telemetry_rows = [
        ["Metric", "Observed Maximum"],
        ["CPU Temperature", _format_reading(summary.get("max_cpu_temp"), "C")],
        ["GPU Temperature", _format_reading(summary.get("max_gpu_temp"), "C")],
    ]
    if summary.get("max_cpu_power", 0) > 0 or summary.get("max_gpu_power", 0) > 0:
        telemetry_rows.extend([
            ["CPU Power", _format_reading(summary.get("max_cpu_power"), "W")],
            ["GPU Power", _format_reading(summary.get("max_gpu_power"), "W")],
            ["Max Combined Power", _format_reading(summary.get("max_total_power"), "W")],
        ])

    telemetry_table = Table(telemetry_rows, colWidths=[65 * mm, 85 * mm])
    telemetry_table.setStyle(_table_style())
    story.append(telemetry_table)
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Methodology", styles["Heading2"]))
    methodology = METHODOLOGY_TEXT + (EVENT_LOG_TEXT if summary.get("event_log_monitored") else "")
    story.append(Paragraph(methodology, styles["BodyText"]))
    story.append(Spacer(1, 4 * mm))

    if summary["clean_run"]:
        story.append(
            Paragraph(
                "Zero hardware errors, memory faults, or thermal anomalies were detected during the testing window.",
                styles["BodyText"],
            )
        )
    else:
        issues = "<br/>".join(_escape_lines(summary["issues"]))
        story.append(Paragraph(f"Observed issues:<br/>{issues}", styles["BodyText"]))

    doc.build(story)
    return output_path


def _table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#334155")),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8fafc"), colors.HexColor("#eef2f7")]),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]
    )


def _certificate_details_rows(settings: dict) -> list[list[str]]:
    rows = [["Field", "Value"]]
    if settings.get("include_customer_name") and settings.get("customer_name"):
        rows.append(["Customer Name", str(settings["customer_name"])])
    if settings.get("include_machine_name") and settings.get("certificate_machine_name"):
        rows.append(["Machine Name", str(settings["certificate_machine_name"])])
    return rows if len(rows) > 1 else []


def _load_logo(logo_path: str | None) -> Image | None:
    if not logo_path:
        return None
    logo = Path(logo_path)
    if not logo.is_file():
        return None
    try:
        # Read the size eagerly so a corrupt image is skipped here instead of failing doc.build().
        image = Image(str(logo), width=35 * mm, height=20 * mm, kind="proportional")
        image.wrap(35 * mm, 20 * mm)
        return image
    except Exception:
        return None


def _format_reading(value: float | None, unit: str) -> str:
    if not value:
        return "Not recorded"
    return f"{value:.1f} {unit}"


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_lines(values: Iterable[str]) -> list[str]:
    return [_escape(value) for value in values]
