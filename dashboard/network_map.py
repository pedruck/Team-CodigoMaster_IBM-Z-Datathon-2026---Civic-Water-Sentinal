"""L-Town network map as plain SVG: the alert's region, deviating sensors and candidate pipe
drawn on the network exported by ``sentinel.data.export_map`` (``config/l-town-map.json``).

No extra dependency, so the same picture renders in Streamlit, in the VM's JupyterLab
(ipywidgets ``HTML``) and as a ``.svg`` file for slides. Rendering only (ADR-009): every value
shown comes from the alert or the map file; nothing is inferred here.

    net = load_map()
    svg = map_svg(net, alert)  # alert=None draws the network alone
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from sentinel.schemas.alerts import Alert

DEFAULT_MAP = Path(__file__).resolve().parents[1] / "config" / "l-town-map.json"

BACKGROUND = "#fbfdfc"
INK = "#15343a"
MUTED = "#52696a"
PIPE = "#9eb5ae"
PIPE_FADED = "#dce8e3"
REGION = "#08796f"
ALERT = "#ad443a"
SENSOR = "#38585b"
LANDMARK = "#2e806e"
MOCK_BG = "#fff4ce"
MOCK_FG = "#5c4400"

PAD = 24.0
HEADER = 64.0
FOOTER = 34.0


@dataclass(frozen=True)
class MapLink:
    id: str
    type: str
    x1: float
    y1: float
    x2: float
    y2: float
    diameter: float | None
    region: str | None
    sensor: str | None = None  # pressure sensor whose influence area holds the link


@dataclass(frozen=True)
class MapPoint:
    id: str
    kind: str
    x: float
    y: float
    region: str | None


@dataclass(frozen=True)
class NetworkMap:
    links: tuple[MapLink, ...]
    sensors: tuple[MapPoint, ...]
    landmarks: tuple[MapPoint, ...]
    bbox: tuple[float, float, float, float]
    counts: dict[str, int]
    attribution: str


def parse_map(data: dict[str, Any]) -> NetworkMap:
    """Build the map from the exported JSON; raises ``ValueError`` on another format."""
    if data.get("schema_version") != "1.0":
        raise ValueError(f"unsupported map schema_version {data.get('schema_version')!r}")
    x0, y0, x1, y1 = (float(v) for v in data["bbox"])
    return NetworkMap(
        links=tuple(
            MapLink(
                id=str(lk["id"]),
                type=str(lk["type"]),
                x1=float(lk["x1"]),
                y1=float(lk["y1"]),
                x2=float(lk["x2"]),
                y2=float(lk["y2"]),
                diameter=None if lk.get("diameter") is None else float(lk["diameter"]),
                region=lk.get("region"),
                sensor=lk.get("sensor"),
            )
            for lk in data["links"]
        ),
        sensors=tuple(
            MapPoint(str(s["id"]), "pressure", float(s["x"]), float(s["y"]), s.get("region"))
            for s in data["sensors"]
        ),
        landmarks=tuple(
            MapPoint(str(m["id"]), str(m["type"]), float(m["x"]), float(m["y"]), None)
            for m in data.get("landmarks", [])
        ),
        bbox=(x0, y0, x1, y1),
        counts={str(k): int(v) for k, v in data.get("counts", {}).items()},
        attribution=str(data.get("attribution", "")),
    )


def load_map(path: Path | str = DEFAULT_MAP) -> NetworkMap:
    return parse_map(json.loads(Path(path).read_text(encoding="utf-8")))


class _Frame:
    """Network coordinates (metres, y up) → SVG pixels (y down)."""

    def __init__(self, bbox: tuple[float, float, float, float], width: float) -> None:
        x0, y0, x1, y1 = bbox
        self.x0, self.y1 = x0, y1
        self.scale = (width - 2 * PAD) / (x1 - x0)
        self.width = width
        self.height = HEADER + (y1 - y0) * self.scale + FOOTER + 2 * PAD

    def x(self, v: float) -> float:
        return PAD + (v - self.x0) * self.scale

    def y(self, v: float) -> float:
        return HEADER + PAD + (self.y1 - v) * self.scale


def _f(v: float) -> str:
    return f"{v:.1f}"


def _text(x: float, y: float, s: str, *, size: int = 12, fill: str = INK, weight: str = "") -> str:
    """Text with a light halo, so labels stay readable over the pipes."""
    bold = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{_f(x)}" y="{_f(y)}" font-size="{size}" fill="{fill}"{bold} '
        f'stroke="{BACKGROUND}" stroke-width="3" paint-order="stroke">{escape(s)}</text>'
    )


def _pipe_width(link: MapLink) -> float:
    if link.diameter is None:
        return 1.2
    return min(0.6 + link.diameter / 250.0, 2.6)


def _header(net: NetworkMap, alert: Alert | None, width: int) -> list[str]:
    factor = width / 900
    c = net.counts
    subtitle = (
        f"{c.get('nodes', '?')} junctions · {c.get('pipes', '?')} pipes · "
        f"{c.get('pressure_sensors', len(net.sensors))} pressure sensors"
    )
    if alert is not None:
        when = alert.window_end.strftime("%Y-%m-%d %H:%M UTC")
        subtitle = (
            f"Alert {alert.alert_id} · {when} · region {alert.region or 'n/a'} · "
            f"candidate pipe {alert.candidate_pipe or 'n/a'}"
        )
    out = [
        _text(
            PAD,
            26,
            "L-Town water network (simulated, BattLeDIM)",
            size=round(18 * factor),
            weight="bold",
        ),
        _text(PAD, 46, subtitle, size=round(13 * factor), fill=MUTED),
    ]
    if alert is not None and alert.is_mock:
        out.append(
            f'<rect x="{_f(width - PAD - 300 * factor)}" y="12" '
            f'width="{_f(300 * factor)}" height="{_f(24 * factor)}" rx="4" '
            f'fill="{MOCK_BG}"/>'
        )
        out.append(
            _text(
                width - PAD - 290 * factor,
                29,
                "SIMULATED: mock output, not leak detection",
                size=round(12 * factor),
                fill=MOCK_FG,
                weight="bold",
            )
        )
    return out


def _pipes(net: NetworkMap, fr: _Frame, highlight: str | None) -> list[str]:
    """Every link; outside the alert's region they fade, so the region stands out."""
    out = ['<g stroke-linecap="round">']
    for lk in net.links:
        in_region = highlight is not None and lk.region == highlight
        color = REGION if in_region else (PIPE_FADED if highlight else PIPE)
        w = (_pipe_width(lk) + (0.8 if in_region else 0.0)) * fr.width / 900
        title = f"{lk.id} ({lk.type})" + (f", region {lk.region}" if lk.region else "")
        out.append(
            f'<line x1="{_f(fr.x(lk.x1))}" y1="{_f(fr.y(lk.y1))}" x2="{_f(fr.x(lk.x2))}" '
            f'y2="{_f(fr.y(lk.y2))}" stroke="{color}" stroke-width="{w:.2f}">'
            f"<title>{escape(title)}</title></line>"
        )
    out.append("</g>")
    return out


def _candidate(link: MapLink, fr: _Frame) -> list[str]:
    xa, ya, xb, yb = fr.x(link.x1), fr.y(link.y1), fr.x(link.x2), fr.y(link.y2)
    out = [
        f'<line x1="{_f(xa)}" y1="{_f(ya)}" x2="{_f(xb)}" y2="{_f(yb)}" stroke="{ALERT}" '
        f'stroke-width="{w}" stroke-linecap="round" opacity="{opacity}">'
        f"<title>{escape(f'candidate pipe {link.id}')}</title></line>"
        for w, opacity in ((14.0 * fr.width / 900, 0.22), (4.5 * fr.width / 900, 1.0))
    ]
    label = f"Suspected leak (best guess): {link.id}"
    out.append(
        _text(
            max(xa, xb) + 10,
            (ya + yb) / 2 + 4,
            label,
            size=round(13 * fr.width / 900),
            fill=ALERT,
            weight="bold",
        )
    )
    return out


def _points(net: NetworkMap, fr: _Frame, alert: Alert | None) -> list[str]:
    """Tanks/reservoirs, then the pressure sensors (red and labelled when deviating)."""
    out: list[str] = []
    factor = fr.width / 900
    for m in net.landmarks:
        x, y = fr.x(m.x), fr.y(m.y)
        out.append(
            f'<rect x="{_f(x - 6 * factor)}" y="{_f(y - 6 * factor)}" '
            f'width="{_f(12 * factor)}" height="{_f(12 * factor)}" fill="{LANDMARK}">'
            f"<title>{escape(f'{m.id} ({m.kind})')}</title></rect>"
        )
        out.append(
            _text(
                x + 9 * factor, y + 4, m.id, size=round(11 * factor), fill=LANDMARK, weight="bold"
            )
        )
    deviating = {d.sensor: d for d in alert.deviating_sensors} if alert else {}
    for s in net.sensors:
        x, y = fr.x(s.x), fr.y(s.y)
        d = deviating.get(s.id)
        if d is None:
            out.append(
                f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(4.5 * factor)}" '
                f'fill="{SENSOR}" stroke="white" '
                f'stroke-width="1"><title>{escape(f"{s.id}, region {s.region}")}</title></circle>'
            )
            continue
        label = f"{s.id} {d.deviation_m:+.2f} m"
        out.append(
            f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(7 * factor)}" '
            f'fill="{ALERT}" stroke="white" '
            f'stroke-width="1.5"><title>{escape(label)}</title></circle>'
        )
        out.append(
            _text(
                x + 9 * factor,
                y - 8 * factor,
                label,
                size=round(12 * factor),
                fill=ALERT,
                weight="bold",
            )
        )
    return out


def _legend(net: NetworkMap, fr: _Frame, note: str) -> list[str]:
    ly = fr.height - FOOTER + 4
    factor = fr.width / 900
    items = [
        (PIPE, "pipe"),
        (REGION, "alert region"),
        (ALERT, "candidate pipe / sensor below expected pressure"),
    ]
    out: list[str] = []
    x = PAD
    for color, name in items:
        out.append(
            f'<line x1="{_f(x)}" y1="{_f(ly)}" x2="{_f(x + 22 * factor)}" y2="{_f(ly)}" '
            f'stroke="{color}" stroke-width="3"/>'
        )
        out.append(_text(x + 28 * factor, ly + 4, name, size=round(11 * factor), fill=MUTED))
        x += (40 + 5.6 * len(name)) * factor
    out.append(
        f'<rect x="{_f(x)}" y="{_f(ly - 5 * factor)}" '
        f'width="{_f(10 * factor)}" height="{_f(10 * factor)}" fill="{LANDMARK}"/>'
    )
    out.append(
        _text(x + 16 * factor, ly + 4, "tank / reservoir", size=round(11 * factor), fill=MUTED)
    )
    if note:
        out.append(_text(PAD, ly + 20, note, size=round(11 * factor), fill=ALERT))
    elif net.attribution:
        out.append(_text(PAD, ly + 20, net.attribution, size=round(10 * factor), fill=MUTED))
    return out


def map_svg(net: NetworkMap, alert: Alert | None = None, *, width: int = 900) -> str:
    """The whole network; with an alert, its region, deviating sensors and candidate pipe.
    A region or candidate pipe that is not in the map is reported, never guessed."""
    fr = _Frame(net.bbox, float(width))
    region = alert.region if alert else None
    highlight = region if region in {s.region for s in net.sensors} else None
    candidate = alert.candidate_pipe if alert else None
    link = next((lk for lk in net.links if lk.id == candidate), None) if candidate else None

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{_f(fr.height)}" '
        f'viewBox="0 0 {width} {_f(fr.height)}" font-family="IBM Plex Sans, Arial, sans-serif" '
        'role="img" aria-label="L-Town water network map">',
        f'<rect width="100%" height="100%" fill="{BACKGROUND}"/>',
        *_header(net, alert, width),
        *_pipes(net, fr, highlight),
        *(_candidate(link, fr) if link else []),
        *_points(net, fr, alert),
    ]
    notes = []
    if region and highlight is None:
        notes.append(f"region {region} is not in the map")
    if candidate and link is None:
        notes.append(f"candidate pipe {candidate} is not in the map")
    note = "; ".join(notes)
    out += _legend(net, fr, note)
    out.append("</svg>")
    return "".join(out)


# Regions mode: nine inks distinct from each other, at least 4.3:1 against BACKGROUND, and
# kept away from the coral of alerts and the green of the alert region.
REGION_PALETTE = (
    "#1a73c2",
    "#312e81",
    "#9a4bd4",
    "#b02f86",
    "#9e6a00",
    "#831843",
    "#2f3336",
    "#6c7a00",
    "#5b3a1a",
)
PAYLOAD_VERSION = "1"


def _when(value: datetime | None) -> str | None:
    return None if value is None else value.strftime("%Y-%m-%d %H:%M UTC")


def _alert_view(net: NetworkMap, alert: Alert) -> dict[str, Any]:
    """What the interactive map needs from one alert. Unknown IDs are flagged, never placed."""
    regions = {s.region for s in net.sensors if s.region}
    pipes = {lk.id for lk in net.links}
    sensors = {s.id for s in net.sensors}
    region_known = alert.region in regions if alert.region else False
    pipe_known = alert.candidate_pipe in pipes if alert.candidate_pipe else False
    warnings = []
    if alert.region and not region_known:
        warnings.append(f"Region {alert.region} is not in the map.")
    if alert.candidate_pipe and not pipe_known:
        warnings.append(f"Candidate pipe {alert.candidate_pipe} is not in the map.")
    unknown = [d.sensor for d in alert.deviating_sensors if d.sensor not in sensors]
    if unknown:
        warnings.append(f"Sensor {', '.join(unknown)} not in the map.")
    strongest = max(alert.deviating_sensors, key=lambda d: abs(d.deviation_m), default=None)
    return {
        "id": alert.alert_id,
        "time": alert.window_end.isoformat(),
        "time_label": _when(alert.window_end),
        "onset_label": _when(alert.suspected_onset),
        "region": alert.region,
        "region_known": region_known,
        "candidate_pipe": alert.candidate_pipe,
        "candidate_known": pipe_known,
        "is_mock": alert.is_mock,
        "cusum": (
            {"statistic": alert.cusum.statistic, "threshold": alert.cusum.threshold}
            if alert.cusum
            else None
        ),
        "sensors": [
            {"id": d.sensor, "deviation_m": d.deviation_m, "z": d.z, "known": d.sensor in sensors}
            for d in alert.deviating_sensors
        ],
        "strongest": (
            {"sensor": strongest.sensor, "deviation_m": strongest.deviation_m}
            if strongest
            else None
        ),
        "warnings": warnings,
    }


def map_payload(
    net: NetworkMap, alert: Alert | None = None, alerts: Sequence[Alert] = ()
) -> dict[str, Any]:
    """JSON-ready data for the interactive map (``dashboard.map_embed.map_component``).

    Pure: no drawing decisions beyond the region inks. ``alerts`` (any order) become the replay,
    oldest first and de-duplicated; ``alert`` is the selected one and joins the list if absent.
    Every string stays data: the page inserts it as text, never as HTML.
    """
    region_ids = sorted({s.region for s in net.sensors if s.region})
    by_id = {a.alert_id: a for a in alerts}
    if alert is not None:
        by_id.setdefault(alert.alert_id, alert)
    ordered = sorted(by_id.values(), key=lambda a: (a.window_end, a.alert_id))
    return {
        "version": PAYLOAD_VERSION,
        "network": "L-Town (BattLeDIM 2020), simulated",
        "attribution": net.attribution,
        "bbox": list(net.bbox),
        "counts": dict(net.counts),
        "links": [
            [lk.id, lk.type, lk.x1, lk.y1, lk.x2, lk.y2, lk.diameter, lk.region, lk.sensor]
            for lk in net.links
        ],
        "sensors": [[s.id, s.x, s.y, s.region] for s in net.sensors],
        "landmarks": [[m.id, m.kind, m.x, m.y] for m in net.landmarks],
        "regions": [
            {
                "id": rid,
                "color": REGION_PALETTE[i % len(REGION_PALETTE)],
                "sensors": sorted(s.id for s in net.sensors if s.region == rid),
                "links": sum(1 for lk in net.links if lk.region == rid),
            }
            for i, rid in enumerate(region_ids)
        ],
        "alerts": [_alert_view(net, a) for a in ordered],
        "selected": alert.alert_id if alert is not None else None,
    }
