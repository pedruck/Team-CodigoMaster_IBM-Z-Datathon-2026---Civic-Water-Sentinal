"""Network map for JupyterLab on the VM (ipywidgets), next to ``dashboard.jupyter`` (ADR-009).

By default the interactive map (zoom, pan, details, regions, replay) is shown in an
``<iframe srcdoc>``, the same document Streamlit embeds. A slider picks the alert it opens on.
If the notebook front end does not run scripts inside that iframe, switch ``Map`` to
``Static SVG``: the plain drawing works everywhere. Rendering only.

    from dashboard.network_map_view import NetworkMapView
    view = NetworkMapView(HttpAlertSource("http://127.0.0.1:8000")); view.refresh(); view.widget
"""

from html import escape
from pathlib import Path

import ipywidgets as widgets

from dashboard.map_embed import map_document, map_iframe
from dashboard.network_map import NetworkMap, load_map, map_svg
from dashboard.source import AlertQuery, AlertSource, SourceError
from sentinel.schemas.alerts import Alert

NETWORK_ONLY = "network only"
INTERACTIVE = "Interactive"
STATIC = "Static SVG"


class NetworkMapView:
    def __init__(
        self,
        source: AlertSource,
        net: NetworkMap | None = None,
        *,
        limit: int = 500,
        interactive: bool = True,
        height: int = 620,
    ) -> None:
        self.source = source
        self.net = net if net is not None else load_map()
        self.limit = limit
        self.height = height
        self.alerts: list[Alert] = []
        self.status = widgets.HTML()
        self.slider = widgets.SelectionSlider(
            description="Alert",
            options=[NETWORK_ONLY],
            continuous_update=False,
            layout=widgets.Layout(width="900px"),
        )
        self.renderer = widgets.ToggleButtons(
            options=[INTERACTIVE, STATIC],
            value=INTERACTIVE if interactive else STATIC,
            description="Map",
        )
        self.map = widgets.HTML()
        self.slider.observe(lambda _: self._render(), names="value")
        self.renderer.observe(lambda _: self._render(), names="value")
        self.widget = widgets.VBox([self.status, self.renderer, self.slider, self.map])
        self._render()

    def refresh(self) -> None:
        """Reload the alerts (oldest first) and keep the slider on the latest one."""
        try:
            page = self.source.list_alerts(AlertQuery(limit=self.limit))
        except SourceError as exc:
            self.status.value = f"<b>Could not load alerts:</b> {escape(str(exc))}"
            return
        self.alerts = sorted(page.items, key=lambda a: (a.window_end, a.alert_id))
        self.status.value = f"{len(self.alerts)} alert(s) of {page.total}"
        options = [NETWORK_ONLY, *(a.alert_id for a in self.alerts)]
        self.slider.options = options
        self.slider.value = options[-1]
        self._render()

    def selected(self) -> Alert | None:
        return next((a for a in self.alerts if a.alert_id == self.slider.value), None)

    def save_svg(self, path: Path | str) -> Path:
        """Write the map currently shown (for slides and the video)."""
        out = Path(path)
        out.write_text(map_svg(self.net, self.selected()), encoding="utf-8")
        return out

    def _render(self) -> None:
        if self.renderer.value == STATIC:
            self.map.value = map_svg(self.net, self.selected())
            return
        document = map_document(self.net, self.selected(), self.alerts, variant="notebook")
        self.map.value = map_iframe(document, height=self.height)
