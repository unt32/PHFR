"""
ui_main.py
----------
PyQt6 dashboard UI: left sidebar (map loading, point selection, route
details) + right-hand interactive matplotlib map, embedded via
FigureCanvasQTAgg. All slow work (parsing OSM data) runs on a background
QThread so the UI never freezes.

The map canvas has no navigation toolbar and fills the entire right-hand
panel. Zooming is done with the mouse scroll wheel (centered on the
cursor), and the current view is preserved across redraws (point picks,
route calculation) instead of resetting to the full-graph extent every
time. Route calculation is only triggered by the explicit "Find Route"
button.
"""

import math
import os

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import osmnx as ox

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QProgressBar,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QFrame,
    QSizePolicy,
)

from map_engine import MapEngine, RouteNotFoundError


# ----------------------------------------------------------------------
# Dark theme stylesheet
# ----------------------------------------------------------------------
DARK_STYLESHEET = """
QWidget {
    background-color: #1a1d23;
    color: #e0e0e0;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
}
QMainWindow { background-color: #14161a; }
QGroupBox {
    border: 1px solid #2c3038;
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
    font-weight: 600;
    color: #9fe6ff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
}
QPushButton {
    background-color: #2b303a;
    border: 1px solid #3a3f4b;
    border-radius: 6px;
    padding: 8px 10px;
    color: #f0f0f0;
}
QPushButton:hover { background-color: #343a46; border-color: #00b4d8; }
QPushButton:pressed { background-color: #1f232b; }
QPushButton:disabled { color: #666a72; background-color: #22262e; border-color: #262a31; }
QPushButton:checkable:checked {
    background-color: #00b4d8;
    color: #0b0d10;
    font-weight: 600;
    border-color: #00b4d8;
}
QPushButton#startPickButton:checked { background-color: #00ff7f; color: #0b0d10; }
QPushButton#endPickButton:checked { background-color: #ff3b30; color: #ffffff; }
QPushButton#findRouteButton {
    background-color: #00b4d8;
    color: #0b0d10;
    font-weight: 600;
}
QPushButton#findRouteButton:hover { background-color: #17c6ea; }
QPushButton#findRouteButton:disabled { color: #666a72; background-color: #22262e; border-color: #262a31; }
QLabel#statusLabel { color: #8a8f98; font-style: italic; }
QLabel#metricValue { color: #00e5ff; font-weight: 700; font-size: 14px; }
QLabel#routePathValue { color: #00e5ff; font-weight: 500; font-size: 12px; }
QLabel#sourceLabel { color: #6f7580; font-size: 11px; }
QProgressBar {
    border: 1px solid #343943;
    border-radius: 5px;
    background-color: #22262e;
    text-align: center;
    color: #e0e0e0;
    min-height: 16px;
}
QProgressBar::chunk { background-color: #00b4d8; border-radius: 5px; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #1a1d23; width: 10px; margin: 0px; }
QScrollBar::handle:vertical { background: #343943; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #454b58; }
QFrame#separator { background-color: #2c3038; max-height: 1px; min-height: 1px; }
"""


class GraphLoaderThread(QThread):
    """Runs a slow MapEngine loading call off the GUI thread."""

    success = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, task_func, *args, **kwargs):
        super().__init__()
        self.task_func = task_func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            self.task_func(*self.args, **self.kwargs)
            self.success.emit("Map loaded successfully.")
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self.failed.emit(str(exc))


def _separator() -> QFrame:
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.Shape.HLine)
    return line


class MainWindow(QMainWindow):
    # Scroll-zoom: > 1 zooms out per notch, its reciprocal zooms in.
    ZOOM_SCALE = 1.2

    def __init__(self):
        super().__init__()
        self.setWindowTitle("OSM Route Finder \u2014 Shortest Path Explorer")
        self.resize(1440, 900)

        self.engine = MapEngine()
        self.start_node = None
        self.end_node = None
        self.current_route = None
        self.pick_mode = None  # None | "start" | "end"
        self.loader_thread = None
        self._map_has_view = False  # whether the axes currently hold a meaningful view

        # Left-click-drag panning state.
        self._pan_active = False
        self._pan_start_pixel = None  # (event.x, event.y) in display coords
        self._pan_start_xlim = None
        self._pan_start_ylim = None
        self._pan_scale_x = 1.0  # data units per pixel, computed at drag start
        self._pan_scale_y = 1.0
        # When a button_press_event is consumed by on_map_click (a point
        # pick), on_map_button_press still receives that same event right
        # afterwards. This flag tells it "this press was already handled,
        # don't also start a pan drag from it".
        self._suppress_pan_click = False

        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_sidebar(), 0)
        root_layout.addWidget(self._build_map_panel(), 1)

        self._update_find_route_enabled()
        self._draw_placeholder()

    # ------------------------------------------------------------------
    # Sidebar construction
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(360)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)

        layout.addWidget(self._build_load_group())
        layout.addWidget(self._build_points_group())
        layout.addWidget(self._build_details_group())
        layout.addStretch(1)

        scroll.setWidget(container)
        return scroll

    def _build_load_group(self) -> QGroupBox:
        group = QGroupBox("Load Map")
        layout = QVBoxLayout(group)

        self.load_file_btn = QPushButton("Load Local .osm / .osm.pbf File")
        self.load_file_btn.clicked.connect(self.on_load_file)
        layout.addWidget(self.load_file_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # indeterminate "spinner" style
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        return group

    def _build_points_group(self) -> QGroupBox:
        group = QGroupBox("Start / End Points")
        layout = QVBoxLayout(group)

        layout.addWidget(QLabel("Click directly on the map to set a point"))

        pick_row = QHBoxLayout()
        self.pick_start_btn = QPushButton("\u25cf Pick Start")
        self.pick_start_btn.setObjectName("startPickButton")
        self.pick_start_btn.setCheckable(True)
        self.pick_start_btn.toggled.connect(self.on_pick_start_toggled)
        self.pick_end_btn = QPushButton("\u25cf Pick End")
        self.pick_end_btn.setObjectName("endPickButton")
        self.pick_end_btn.setCheckable(True)
        self.pick_end_btn.toggled.connect(self.on_pick_end_toggled)
        pick_row.addWidget(self.pick_start_btn)
        pick_row.addWidget(self.pick_end_btn)
        layout.addLayout(pick_row)

        layout.addWidget(_separator())

        self.find_route_btn = QPushButton("\u25b6 Find Route")
        self.find_route_btn.setObjectName("findRouteButton")
        self.find_route_btn.clicked.connect(self.on_find_route_clicked)
        layout.addWidget(self.find_route_btn)

        self.clear_points_btn = QPushButton("Clear Points")
        self.clear_points_btn.clicked.connect(self.on_clear_points)
        layout.addWidget(self.clear_points_btn)

        return group

    def _build_details_group(self) -> QGroupBox:
        group = QGroupBox("Route Details")
        layout = QFormLayout(group)

        self.route_path_value = QLabel("\u2014")
        self.route_path_value.setObjectName("routePathValue")
        self.route_path_value.setWordWrap(True)

        self.distance_value = QLabel("\u2014")
        self.distance_value.setObjectName("metricValue")

        layout.addRow("Route path:", self.route_path_value)
        layout.addRow("Distance:", self.distance_value)

        return group

    # ------------------------------------------------------------------
    # Map panel construction
    # ------------------------------------------------------------------

    def _build_map_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        # No margins/spacing so the canvas fills 100% of this panel.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.figure = Figure(figsize=(9, 9))
        self.figure.patch.set_facecolor("#121212")
        # Axes span the entire figure - no matplotlib padding/margins.
        self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.ax = self.figure.add_subplot(111)
        # Belt-and-suspenders: pin the axes to the full figure rectangle so
        # nothing (aspect handling, tight_layout, etc.) can reintroduce a
        # border later. This position gets reasserted after every redraw.
        self.ax.set_position([0, 0, 1, 1])
        self.ax.margins(0)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.canvas.mpl_connect("button_press_event", self.on_map_click)
        self.canvas.mpl_connect("scroll_event", self.on_map_scroll)
        # Left-click-drag panning (active only outside point-pick mode -
        # see on_map_button_press).
        self.canvas.mpl_connect("button_press_event", self.on_map_button_press)
        self.canvas.mpl_connect("motion_notify_event", self.on_map_motion)
        self.canvas.mpl_connect("button_release_event", self.on_map_button_release)

        # No NavigationToolbar2QT - the map area stays clean and seamless.
        layout.addWidget(self.canvas, 1)

        return panel

    # ------------------------------------------------------------------
    # Loading handlers
    # ------------------------------------------------------------------

    # Recognized OSM file suffixes, checked case-insensitively against the
    # end of each filename in the auto-detect data/ directory.
    AUTO_LOAD_SUFFIXES = (".osm", ".osm.pbf")

    def on_load_file(self):
        auto_path = self._find_auto_load_file()
        if auto_path:
            self.status_label.setText(
                f"Auto-detected {os.path.basename(auto_path)} in data/\u2026"
            )
            self._start_loading(self.engine.load_from_file, auto_path)
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select OSM File", "", "OSM Files (*.osm *.osm.pbf);;All Files (*)"
        )
        if not path:
            return
        self._start_loading(self.engine.load_from_file, path)

    def _find_auto_load_file(self):
        """Look for a .osm / .osm.pbf file in ./data relative to the CWD.

        Returns the path to load automatically, or None if the data/
        directory doesn't exist or contains no matching file - in which
        case the caller falls back to the manual QFileDialog picker.
        """
        data_dir = os.path.join(os.getcwd(), "data")
        if not os.path.isdir(data_dir):
            return None

        try:
            entries = sorted(os.listdir(data_dir))
        except OSError:
            return None

        matches = [
            name
            for name in entries
            if os.path.isfile(os.path.join(data_dir, name))
            and name.lower().endswith(self.AUTO_LOAD_SUFFIXES)
        ]
        if not matches:
            return None

        # Exactly one match: load it. Multiple matches: load the first
        # (alphabetically), per the auto-detect fallback rules.
        return os.path.join(data_dir, matches[0])

    def _start_loading(self, func, *args, **kwargs):
        self._set_loading_state(True)
        self.loader_thread = GraphLoaderThread(func, *args, **kwargs)
        self.loader_thread.success.connect(self.on_load_success)
        self.loader_thread.failed.connect(self.on_load_failed)
        self.loader_thread.start()

    def _set_loading_state(self, loading: bool):
        self.progress_bar.setVisible(loading)
        self.load_file_btn.setEnabled(not loading)
        self.status_label.setText(
            "Loading map data\u2026 this may take a moment." if loading else ""
        )

    def on_load_success(self, msg: str):
        self._set_loading_state(False)
        n_nodes = self.engine.graph.number_of_nodes()
        n_edges = self.engine.graph.number_of_edges()
        self.status_label.setText(
            f"{msg} ({self.engine.graph_source} \u2014 {n_nodes} nodes, {n_edges} edges)"
        )
        self.start_node = None
        self.end_node = None
        self.current_route = None
        self._reset_route_details()
        self._update_find_route_enabled()
        # A brand-new graph should be shown at its full extent, not whatever
        # view happened to be on screen before.
        self._map_has_view = False
        self.redraw_map(preserve_view=False)

    def on_load_failed(self, err_msg: str):
        self._set_loading_state(False)
        self.status_label.setText("Failed to load map.")
        QMessageBox.critical(
            self, "Map Loading Error", f"Could not load the map:\n\n{err_msg}"
        )

    # ------------------------------------------------------------------
    # Point selection handlers
    # ------------------------------------------------------------------

    def on_pick_start_toggled(self, checked: bool):
        if checked:
            self.pick_end_btn.setChecked(False)
            self.pick_mode = "start"
        elif self.pick_mode == "start":
            self.pick_mode = None
        self._update_pick_cursor()

    def on_pick_end_toggled(self, checked: bool):
        if checked:
            self.pick_start_btn.setChecked(False)
            self.pick_mode = "end"
        elif self.pick_mode == "end":
            self.pick_mode = None
        self._update_pick_cursor()

    def _update_pick_cursor(self):
        """Reflect the current pick mode as a cursor shape over the canvas."""
        if self.pick_mode == "start":
            self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        elif self.pick_mode == "end":
            self.canvas.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.canvas.setCursor(Qt.CursorShape.ArrowCursor)

    def on_map_click(self, event):
        if event.button != 1:
            return

        if not self.engine.has_graph() or self.pick_mode is None:
            return
        if event.xdata is None or event.ydata is None:
            return
        lon, lat = event.xdata, event.ydata
        try:
            node = self.engine.nearest_node(lon, lat)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Selection Error", str(exc))
            return

        if self.pick_mode == "start":
            self.start_node = node
            self.pick_start_btn.setChecked(False)
        else:
            self.end_node = node
            self.pick_end_btn.setChecked(False)

        self.pick_mode = None
        self._update_pick_cursor()
        self.current_route = None
        self._reset_route_details()
        self._update_find_route_enabled()

        # This button_press_event is about to also reach on_map_button_press
        # (both are connected to the same Qt/Matplotlib signal). Since we've
        # just fully consumed it as a point pick, make sure that handler
        # doesn't also interpret it as the start of a pan drag, and make
        # sure any pan state/cursor is clean regardless of what it was
        # doing before this click.
        self._suppress_pan_click = True
        self._pan_active = False
        self._pan_start_pixel = None
        self._pan_start_xlim = None
        self._pan_start_ylim = None
        self._update_pick_cursor()

        # Rebuilding the whole plot (ax.clear() + OSMnx replot) synchronously
        # here, in the middle of Matplotlib/Qt's button_press_event dispatch,
        # was leaving that dispatch cycle without a matching
        # button_release_event ever being delivered - Matplotlib/Qt would
        # then behave as if the left mouse button was still held down until
        # the user clicked again. Scheduling the redraw with
        # QTimer.singleShot(0, ...) defers it to the next iteration of Qt's
        # event loop, after this press event (and its release) have finished
        # dispatching normally.
        QTimer.singleShot(0, lambda: self.redraw_map(preserve_view=True))

    def on_clear_points(self):
        self.start_node = None
        self.end_node = None
        self.current_route = None
        self._reset_route_details()
        self._update_find_route_enabled()
        self.redraw_map(preserve_view=True)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def on_find_route_clicked(self):
        self._find_route()

    def _update_find_route_enabled(self):
        self.find_route_btn.setEnabled(
            self.engine.has_graph()
            and self.start_node is not None
            and self.end_node is not None
        )

    def _find_route(self):
        try:
            route = self.engine.compute_route(
                self.start_node, self.end_node, weight="length"
            )
        except RouteNotFoundError as exc:
            QMessageBox.critical(self, "No Route Found", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Routing Error", str(exc))
            return

        self.current_route = route
        stats = self.engine.route_stats(route)
        self.route_path_value.setText(" \u2192 ".join(str(n) for n in route))
        self.distance_value.setText(f"{stats['distance_km']:.2f} km")
        # Preserve the current view - finding a route shouldn't snap the
        # camera back to the full-graph extent.
        self.redraw_map(preserve_view=True)

    def _reset_route_details(self):
        self.route_path_value.setText("\u2014")
        self.distance_value.setText("\u2014")

    # ------------------------------------------------------------------
    # Scroll-to-zoom
    # ------------------------------------------------------------------

    def on_map_scroll(self, event):
        """Zoom in/out on the matplotlib canvas, centered on the cursor."""
        if not self.engine.has_graph():
            return
        if event.xdata is None or event.ydata is None:
            return

        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()

        if event.button == "up":
            scale_factor = 1 / self.ZOOM_SCALE
        elif event.button == "down":
            scale_factor = self.ZOOM_SCALE
        else:
            return

        xdata, ydata = event.xdata, event.ydata

        new_width = (cur_xlim[1] - cur_xlim[0]) * scale_factor
        new_height = (cur_ylim[1] - cur_ylim[0]) * scale_factor

        relx = (cur_xlim[1] - xdata) / (cur_xlim[1] - cur_xlim[0])
        rely = (cur_ylim[1] - ydata) / (cur_ylim[1] - cur_ylim[0])

        self.ax.set_xlim([xdata - new_width * (1 - relx), xdata + new_width * relx])
        self.ax.set_ylim([ydata - new_height * (1 - rely), ydata + new_height * rely])
        self._map_has_view = True
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    # Left-click-drag panning
    # ------------------------------------------------------------------

    def on_map_button_press(self, event):
        """Start a pan drag on left-click, but only outside point-pick mode.

        When a pick mode ("start"/"end") is active, the same click is also
        handled by on_map_click for node selection - that takes priority
        and panning is skipped entirely so the two don't fight over the
        click.
        """
        if self._suppress_pan_click:
            # Already handled by on_map_click as a point pick - don't also
            # start a pan drag from the same press.
            self._suppress_pan_click = False
            return
        if not self.engine.has_graph():
            return
        if event.button != 3:  # left mouse button only
            return
        if event.x is None or event.y is None:
            return

        bbox = self.ax.get_window_extent()
        if bbox.width <= 0 or bbox.height <= 0:
            return

        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        self._pan_active = True
        self._pan_start_pixel = (event.x, event.y)
        self._pan_start_xlim = xlim
        self._pan_start_ylim = ylim
        # Data units per pixel, fixed for the duration of this drag so the
        # motion handler doesn't need to re-derive it (and doesn't drift as
        # xlim/ylim change mid-drag).
        self._pan_scale_x = (xlim[1] - xlim[0]) / bbox.width
        self._pan_scale_y = (ylim[1] - ylim[0]) / bbox.height
        self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)

    def on_map_motion(self, event):
        """Translate the view to follow the cursor while a pan is active."""
        if not self._pan_active:
            return
        if event.x is None or event.y is None:
            return

        dx_px = event.x - self._pan_start_pixel[0]
        dy_px = event.y - self._pan_start_pixel[1]
        dx_data = dx_px * self._pan_scale_x
        dy_data = dy_px * self._pan_scale_y

        x0, x1 = self._pan_start_xlim
        y0, y1 = self._pan_start_ylim
        self.ax.set_xlim(x0 - dx_data, x1 - dx_data)
        self.ax.set_ylim(y0 - dy_data, y1 - dy_data)
        self._map_has_view = True
        self.canvas.draw_idle()

    def on_map_button_release(self, event):
        """End an active pan drag and restore the normal cursor."""
        if event.button != 3:
            return
        if not self._pan_active:
            return
        self._pan_active = False
        self._pan_start_pixel = None
        self._pan_start_xlim = None
        self._pan_start_ylim = None
        # Back to the arrow (or the pick cursor, if a pick mode is somehow
        # active again by the time the button is released).
        self._update_pick_cursor()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw_placeholder(self):
        self.ax.clear()
        self.ax.set_facecolor("#121212")
        self.ax.set_position([0, 0, 1, 1])
        self.ax.margins(0)
        self.ax.text(
            0.5,
            0.5,
            "Load a map to begin",
            color="#888888",
            ha="center",
            va="center",
            fontsize=14,
            transform=self.ax.transAxes,
        )
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas.draw_idle()

    def redraw_map(self, preserve_view: bool = True):
        """Redraw the graph/route/markers.

        When `preserve_view` is True and the axes already hold a meaningful
        view (i.e. this isn't the very first draw after a fresh load), the
        current xlim/ylim are captured before clearing and reapplied after
        redrawing - so picking points or recalculating a route never snaps
        the viewport back to the full-graph extent.
        """
        saved_xlim = saved_ylim = None
        if preserve_view and self._map_has_view:
            saved_xlim = self.ax.get_xlim()
            saved_ylim = self.ax.get_ylim()

        self.ax.clear()

        if not self.engine.has_graph():
            self._draw_placeholder()
            return

        ox.plot_graph(
            self.engine.graph,
            ax=self.ax,
            show=False,
            close=False,
            node_size=0,
            edge_color="#3a3f4b",
            edge_linewidth=0.8,
            bgcolor="#121212",
        )

        if self.current_route:
            ox.plot_graph_route(
                self.engine.graph,
                self.current_route,
                ax=self.ax,
                show=False,
                close=False,
                route_color="#00e5ff",
                route_linewidth=4,
                route_alpha=0.9,
                orig_dest_size=0,
            )

        if self.start_node is not None:
            x, y = self.engine.node_xy(self.start_node)
            self.ax.scatter(
                [x],
                [y],
                c="#00ff7f",
                s=110,
                zorder=6,
                edgecolors="white",
                linewidths=1.4,
            )

        if self.end_node is not None:
            x, y = self.engine.node_xy(self.end_node)
            self.ax.scatter(
                [x],
                [y],
                c="#ff3b30",
                s=110,
                zorder=6,
                edgecolors="white",
                linewidths=1.4,
            )

        self.ax.set_facecolor("#121212")
        self.figure.patch.set_facecolor("#121212")

        # ox.plot_graph / ox.plot_graph_route set their own aspect handling
        # and can quietly reset the figure's subplot params back to something
        # with padding. Reassert full-bleed subplot params every time, after
        # they've had their say, so no black border creeps back in around
        # the top, bottom, or sides of the map. (Note: we do NOT also pin
        # self.ax's position here - see the aspect handling below, which
        # needs to be able to adjust that position itself.)
        self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.ax.set_xmargin(0)
        self.ax.set_ymargin(0)

        # ox.plot_graph / ox.plot_graph_route both autoscale the axes to the
        # data extent by default. If we have a view worth keeping, restore
        # it now - before computing the aspect ratio below, so that
        # calculation is based on the latitude actually being displayed.
        if saved_xlim is not None:
            self.ax.set_xlim(saved_xlim)
            self.ax.set_ylim(saved_ylim)

        # Graph coordinates here are geographic (longitude/latitude degrees,
        # EPSG:4326), not a flat projection - one degree of longitude covers
        # less real-world ground than one degree of latitude, except right
        # at the equator. Forcing a literal "equal" 1:1 aspect (as before)
        # ignores that, and combined with manually pinning the axes to fill
        # the whole figure rectangle via set_position([0, 0, 1, 1])
        # regardless of the widget's own aspect ratio, that mismatch was
        # resolved by stretching the *data limits* (adjustable="datalim") -
        # squishing/stretching roads depending on the window/widget size.
        #
        # Instead, compute the latitude-corrected aspect ratio and let
        # Matplotlib adjust the *axes box* (adjustable="box") to satisfy it.
        # This keeps data limits - and therefore real-world proportions -
        # untouched; Matplotlib only ever shrinks the axes rectangle within
        # the available figure space (adding letterboxing on one axis if the
        # widget's proportions don't match the geography), and never distorts
        # the geometry itself.
        ylim = self.ax.get_ylim()
        mean_lat = (ylim[0] + ylim[1]) / 2.0
        if abs(mean_lat) < 89:  # guard against cos(90deg) blowing up near the poles
            lat_correction = 1 / math.cos(math.radians(mean_lat))
        else:
            lat_correction = 1.0
        self.ax.set_aspect(lat_correction, adjustable="datalim")

        self._map_has_view = True
        self.canvas.draw_idle()
