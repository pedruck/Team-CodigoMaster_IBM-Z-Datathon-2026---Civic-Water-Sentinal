"""The network map embedded in a page: the static SVG image (``map_image``) and the
interactive document (``map_document``) that ``streamlit.components.v1.html`` and the
Jupyter view load in an iframe.

The interactive document is self-contained: local CSS, script and fonts are inlined, so it
works offline and inside a ``srcdoc`` iframe. Map and alert data travel as JSON in a
non-executing ``<script type="application/json">``; the script inserts every string as text.
"""

import base64
import json
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any

from dashboard.network_map import NetworkMap, map_payload, map_svg
from sentinel.schemas.alerts import Alert

ASSETS = Path(__file__).with_name("assets")
FONTS = (
    ("IBM Plex Sans", "IBMPlexSans-Regular.woff2", "400"),
    ("IBM Plex Sans", "IBMPlexSans-Medium.woff2", "500"),
    ("IBM Plex Sans", "IBMPlexSans-SemiBold.woff2", "600"),
    ("IBM Plex Mono", "IBMPlexMono-Regular.woff2", "400"),
)
VARIANTS = ("workspace", "page", "notebook")


def map_image(net: NetworkMap, alert: Alert | None, *, width: int = 1200) -> str:
    """Keep the SVG vector sharp while allowing horizontal inspection on narrow screens."""
    svg = map_svg(net, alert, width=width)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    context = (
        f"Alert {alert.alert_id}, region {alert.region or 'not available'}"
        if alert is not None
        else "network only"
    )
    alt = escape(f"L-Town simulated water network map; {context}", quote=True)
    return (
        '<div class="network-map-scroll" role="region" tabindex="0" '
        'aria-label="L-Town network map, horizontally scrollable on narrow screens">'
        f'<img src="data:image/svg+xml;base64,{encoded}" alt="{alt}">'
        "</div>"
    )


def payload_json(payload: dict[str, Any]) -> str:
    """JSON that cannot close its ``<script>`` element or start markup, whatever the IDs hold."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for raw, safe in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        text = text.replace(raw, safe)
    return text


@lru_cache
def _static_parts() -> tuple[str, str, str]:
    faces = "".join(
        f"@font-face{{font-family:'{family}';font-style:normal;font-weight:{weight};"
        "font-display:swap;src:url(data:font/woff2;base64,"
        f"{base64.b64encode((ASSETS / 'fonts' / name).read_bytes()).decode('ascii')})"
        " format('woff2');}"
        for family, name, weight in FONTS
    )
    styles = faces + (ASSETS / "network_map.css").read_text(encoding="utf-8")
    script = (ASSETS / "network_map.js").read_text(encoding="utf-8")
    template = (ASSETS / "network_map.html").read_text(encoding="utf-8")
    return template, styles, script


def map_document(
    net: NetworkMap,
    alert: Alert | None = None,
    alerts: tuple[Alert, ...] | list[Alert] = (),
    *,
    variant: str = "workspace",
) -> str:
    """The interactive map as one HTML document (zoom, pan, details, modes, replay)."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    template, styles, script = _static_parts()
    return (
        template.replace("/*__STYLES__*/", styles)
        .replace("__VARIANT__", variant)
        .replace("__PAYLOAD__", payload_json(map_payload(net, alert, alerts)))
        .replace("/*__SCRIPT__*/", script)
    )


def map_iframe(document: str, *, height: int = 640, title: str = "L-Town network map") -> str:
    """``<iframe srcdoc>`` for hosts that render HTML but not components (JupyterLab)."""
    return (
        f'<iframe title="{escape(title, quote=True)}" srcdoc="{escape(document, quote=True)}" '
        f'style="width:100%;height:{int(height)}px;border:1px solid #dce8e3;border-radius:8px" '
        'loading="eager"></iframe>'
    )
