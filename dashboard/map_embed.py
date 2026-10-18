"""Accessible SVG image wrapper for Streamlit's sanitized HTML renderer."""

import base64
from html import escape

from dashboard.network_map import NetworkMap, map_svg
from sentinel.schemas.alerts import Alert


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
