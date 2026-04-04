"""Qt/pyqtgraph-виджет одного графика динамики антител.

Один экземпляр виджета отвечает за один HLA-класс (`Class I` или `Class II`).
Основные кривые строятся по `raw_value`, а `PRA` отображается на отдельном
вторичном `ViewBox`, чтобы не смешивать две разные шкалы на одной оси Y.
"""

from __future__ import annotations

import math

import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPalette
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from hla_app.services.antibody_dynamics_models import ClassPlotPayload, PlotSeries


# --- Ось X хранит индексы, а пользователю показывает форматированные даты ---
class DateIndexAxis(pg.AxisItem):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._labels: list[str] = []

    def set_labels(self, labels: list[str]) -> None:
        self._labels = list(labels)

    def tickStrings(self, values, scale, spacing):
        result = []
        for value in values:
            rounded = round(value)
            if not math.isclose(value, rounded, abs_tol=1e-3):
                result.append("")
                continue

            index = int(rounded)
            if 0 <= index < len(self._labels):
                result.append(self._labels[index])
            else:
                result.append("")
        return result


# --- Один виджет = один график класса с линиями raw_value и overlay PRA ---
class AntibodyDynamicsPlot(QWidget):
    cursorIndexChanged = Signal(object)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        self._x_labels: list[str] = []
        self._pra_by_index = {}
        self._series_name_label_items_by_label: dict[str, pg.TextItem] = {}
        self._value_label_items_by_label: dict[str, list[pg.TextItem]] = {}
        self._tooltip_rows_by_index: dict[int, list[tuple[str, float]]] = {}
        self._right_items = []
        self._legend = None
        self._current_cursor_index = None
        self._pending_tooltip_index: int | None = None
        self._tooltip_visible_index: int | None = None

        self._axis = DateIndexAxis(orientation="bottom")
        self._plot_widget = pg.PlotWidget(axisItems={"bottom": self._axis})
        self._plot_item = self._plot_widget.getPlotItem()
        self._plot_item.setTitle(self._format_title(title))
        self._plot_item.showGrid(x=True, y=True, alpha=0.10)
        self._plot_item.setClipToView(True)

        # PRA рисуется на отдельном ViewBox, чтобы не смешивать шкалу 0..100
        # со значениями raw_value на основной оси Y.
        self._right_view = pg.ViewBox()
        self._plot_item.showAxis("right")
        self._plot_item.scene().addItem(self._right_view)
        self._plot_item.getAxis("right").linkToView(self._right_view)
        self._plot_item.getAxis("right").setLabel("PRA")
        self._plot_item.getAxis("left").setLabel("MFI")
        self._right_view.setXLink(self._plot_item)
        self._plot_item.vb.sigResized.connect(self._update_right_view_geometry)

        self._threshold_line = pg.InfiniteLine(angle=0, movable=False)
        self._boundary_line = pg.InfiniteLine(angle=90, movable=False)
        self._cursor_line = pg.InfiniteLine(angle=90, movable=False)

        self._empty_label = QLabel("Нет данных")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._plot_widget)
        layout.addWidget(self._empty_label)

        self._legend = self._plot_item.addLegend(offset=(10, 10))
        self._plot_item.addItem(self._threshold_line)
        self._plot_item.addItem(self._boundary_line)
        self._plot_item.addItem(self._cursor_line)

        self._threshold_line.hide()
        self._boundary_line.hide()
        self._cursor_line.hide()

        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.setInterval(250)
        self._tooltip_timer.timeout.connect(self._show_pending_tooltip)

        self._tooltip_popup = QLabel("", self, Qt.ToolTip)
        self._tooltip_popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._tooltip_popup.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._tooltip_popup.setTextFormat(Qt.PlainText)
        self._tooltip_popup.setMargin(6)
        self._tooltip_popup.hide()

        self._plot_widget.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._apply_palette_theme()
        self._update_right_view_geometry()

    def _format_title(self, title: str) -> str:
        text_color = self.palette().color(QPalette.WindowText).name()
        return (
            f"<span style='color:{text_color}; font-size:14pt; font-weight:600;'>"
            f"{title}"
            "</span>"
        )

    def _with_alpha(self, color: QColor, alpha: int) -> QColor:
        result = QColor(color)
        result.setAlpha(alpha)
        return result

    def _build_pra_theme_colors(self) -> tuple[QColor, QColor, QColor]:
        palette = self.palette()
        base_color = palette.color(QPalette.Base)
        highlight_color = QColor(palette.color(QPalette.Highlight))
        is_dark_theme = base_color.lightness() < 128

        if is_dark_theme:
            axis_color = highlight_color.lighter(135)
            brush_color = self._with_alpha(highlight_color.lighter(125), 100)
            pen_color = QColor(brush_color)
        else:
            axis_color = highlight_color.darker(110)
            brush_color = self._with_alpha(highlight_color, 50)
            pen_color = QColor(brush_color)

        return axis_color, brush_color, pen_color

    def _apply_palette_theme(self) -> None:
        palette = self.palette()
        base_color = palette.color(QPalette.Base)
        text_color = palette.color(QPalette.WindowText)
        mid_color = palette.color(QPalette.Mid)
        highlight_color = palette.color(QPalette.Highlight)
        window_color = palette.color(QPalette.Window)
        pra_axis_color, _pra_brush_color, _pra_pen_color = (
            self._build_pra_theme_colors()
        )

        self._plot_widget.setBackground(base_color)
        self._plot_item.setTitle(self._format_title(self._title))
        self._plot_item.getAxis("left").setPen(pg.mkPen(mid_color))
        self._plot_item.getAxis("bottom").setPen(pg.mkPen(mid_color))
        self._plot_item.getAxis("left").setTextPen(pg.mkPen(text_color))
        self._plot_item.getAxis("bottom").setTextPen(pg.mkPen(text_color))
        self._plot_item.getAxis("right").setTextPen(pg.mkPen(pra_axis_color))
        self._plot_item.getAxis("right").setPen(pg.mkPen(pra_axis_color))
        self._plot_item.getViewBox().setBorder(pg.mkPen(mid_color))

        self._threshold_line.setPen(
            pg.mkPen(self._with_alpha(highlight_color, 180), width=1, style=Qt.DashLine)
        )
        self._boundary_line.setPen(pg.mkPen(QColor(214, 76, 76), width=2))
        self._cursor_line.setPen(
            pg.mkPen(self._with_alpha(mid_color, 220), width=1, style=Qt.DashLine)
        )

        if self._legend is not None:
            self._legend.setBrush(pg.mkBrush(self._with_alpha(window_color, 235)))
            self._legend.setPen(pg.mkPen(mid_color))

        tooltip_base = palette.color(QPalette.ToolTipBase)
        tooltip_text = palette.color(QPalette.ToolTipText)
        self._tooltip_popup.setStyleSheet(
            "QLabel {"
            f"background: {tooltip_base.name()};"
            f"color: {tooltip_text.name()};"
            f"border: 1px solid {mid_color.name()};"
            "padding: 2px;"
            "}"
        )

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.PaletteChange:
            self._apply_palette_theme()

    def _update_right_view_geometry(self) -> None:
        self._right_view.setGeometry(self._plot_item.vb.sceneBoundingRect())
        self._right_view.linkedViewChanged(
            self._plot_item.vb,
            self._right_view.XAxis,
        )

    def _clear_series_annotation_state(self) -> None:
        self._series_name_label_items_by_label = {}
        self._value_label_items_by_label = {}

    def _format_numeric_value(self, value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.1f}"

    def _build_text_html(
        self,
        *,
        text: str,
        color: QColor,
        font_size_pt: int,
        bold: bool = False,
    ) -> str:
        font_weight = 600 if bold else 500
        return (
            "<span style="
            f"'color:{color.name()}; font-size:{font_size_pt}pt; "
            f"font-weight:{font_weight};'>"
            f"{text}"
            "</span>"
        )

    def _value_label_offset(self) -> tuple[float, float, tuple[float, float]]:
        return 0.0, 50.0, (0.5, 1.0)

    def _add_series_annotations(
        self,
        *,
        series: PlotSeries,
        color: QColor,
    ) -> None:
        if not series.points:
            return

        first_point = min(series.points, key=lambda point: point.x_index)
        name_item = pg.TextItem(
            html=self._build_text_html(
                text=series.label,
                color=color,
                font_size_pt=10,
                bold=True,
            ),
            anchor=(1, 0.5),
        )
        name_item.setPos(first_point.x_index - 0.08, first_point.y_value)
        self._plot_item.addItem(name_item)
        self._series_name_label_items_by_label[series.label] = name_item

        x_offset, y_offset, anchor = self._value_label_offset()
        value_items: list[pg.TextItem] = []
        for point in series.points:
            value_item = pg.TextItem(
                html=self._build_text_html(
                    text=self._format_numeric_value(point.y_value),
                    color=color,
                    font_size_pt=8,
                    bold=True,
                ),
                anchor=anchor,
            )
            value_item.setPos(point.x_index + x_offset, point.y_value + y_offset)
            self._plot_item.addItem(value_item)
            value_items.append(value_item)

        self._value_label_items_by_label[series.label] = value_items

    def _build_series_palette(self, size: int) -> list[QColor]:
        palette: list[QColor] = []
        sat_levels = (205, 175, 235)
        val_levels = (220, 235, 200)

        for index in range(size):
            cycle = index // 360
            hue = (37 + 137 * index) % 360
            sat = sat_levels[cycle % len(sat_levels)]
            val = val_levels[(cycle // len(sat_levels)) % len(val_levels)]
            palette.append(QColor.fromHsv(hue, sat, val))

        return palette

    def _build_series_colors(self, payload: ClassPlotPayload) -> dict[str, QColor]:
        if not payload.series:
            return {}

        palette_size = max(360, len(payload.series))
        palette = self._build_series_palette(palette_size)
        used_slots: set[int] = set()
        colors: dict[str, QColor] = {}

        for series in sorted(
            payload.series, key=lambda item: (item.color_key, item.label)
        ):
            slot = series.color_key % palette_size

            while slot in used_slots:
                slot = (slot + 1) % palette_size

            used_slots.add(slot)
            colors[series.label] = palette[slot]

        return colors

    def _resolved_x_range(self) -> tuple[float, float, float]:
        if not self._x_labels:
            return -0.5, 0.5, 0.0

        return -0.5, len(self._x_labels) - 0.5, 0.02

    def _apply_x_limits(self) -> None:
        # Диапазон дат теперь фильтрует сами данные построения, а не только
        # видимую область. Поэтому жёсткие X-лимиты больше не нужны: они
        # ломают штатное wheel-zoom поведение pyqtgraph, когда X уже упирается
        # в границы, а Y продолжает отдельно расширяться.
        self._plot_item.vb.setLimits(xMin=None, xMax=None, maxXRange=None)
        self._right_view.setLimits(xMin=None, xMax=None, maxXRange=None)

    def _nearest_index(self, x_value: float) -> int | None:
        if not self._x_labels:
            return None

        index = int(round(x_value))
        if index < 0:
            return 0
        if index >= len(self._x_labels):
            return len(self._x_labels) - 1
        return index

    def _has_hover_data(self, index: int | None) -> bool:
        if index is None or not (0 <= index < len(self._x_labels)):
            return False

        if index in self._pra_by_index:
            return True

        return bool(self._tooltip_rows_by_index.get(index))

    def _reset_tooltip(self) -> None:
        self._tooltip_timer.stop()
        self._pending_tooltip_index = None
        self._tooltip_visible_index = None
        self._tooltip_popup.hide()

    def _schedule_tooltip(self, index: int) -> None:
        if not self._has_hover_data(index):
            self._reset_tooltip()
            return

        if self._tooltip_visible_index == index:
            return

        if (
            self._tooltip_visible_index is not None
            and self._tooltip_visible_index != index
        ):
            self._tooltip_popup.hide()
            self._tooltip_visible_index = None

        self._pending_tooltip_index = index
        self._tooltip_timer.start()

    def _show_pending_tooltip(self) -> None:
        if self._pending_tooltip_index is None:
            return

        self._show_tooltip_for_index(self._pending_tooltip_index)
        self._tooltip_visible_index = self._pending_tooltip_index

    def _show_tooltip_for_index(self, index: int | None) -> None:
        if index is None or not (0 <= index < len(self._x_labels)):
            self._tooltip_popup.hide()
            return

        lines = [f"Дата: {self._x_labels[index]}"]

        pra_point = self._pra_by_index.get(index)
        if pra_point is not None:
            lines.append(f"PRA: {pra_point.value}")

        series_lines = []
        for label, y_value in self._tooltip_rows_by_index.get(index, []):
            series_lines.append(f"{label}: {self._format_numeric_value(y_value)}")

        if series_lines:
            lines.extend(series_lines)
        elif pra_point is None:
            lines.append("Нет значений на выбранную дату.")

        self._tooltip_popup.setText("\n".join(lines))
        self._tooltip_popup.adjustSize()
        cursor_pos = QCursor.pos()
        popup_size = self._tooltip_popup.sizeHint()

        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is None:
            screen = self.window().screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()

        x = cursor_pos.x() + 16
        y = cursor_pos.y() + 20

        if screen is not None:
            available = screen.availableGeometry()
            margin = 8

            if x + popup_size.width() > available.right() - margin:
                x = cursor_pos.x() - popup_size.width() - 16

            if y + popup_size.height() > available.bottom() - margin:
                y = cursor_pos.y() - popup_size.height() - 20

            max_x = available.right() - margin - popup_size.width()
            x = min(x, max_x)
            x = max(available.left() + margin, x)
            y = max(available.top() + margin, y)

        self._tooltip_popup.move(x, y)
        self._tooltip_popup.show()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._reset_tooltip()

    def _on_mouse_moved(self, pos) -> None:
        if not self._plot_item.sceneBoundingRect().contains(pos):
            self._reset_tooltip()
            return

        mouse_point = self._plot_item.vb.mapSceneToView(pos)
        index = self._nearest_index(mouse_point.x())
        if index is None:
            self._reset_tooltip()
            return

        if self._current_cursor_index == index:
            self._schedule_tooltip(index)
            return

        self._current_cursor_index = index
        self._cursor_line.setPos(index)
        self._cursor_line.show()
        self.cursorIndexChanged.emit(index)
        self._schedule_tooltip(index)

    def apply_view(
        self,
        payload: ClassPlotPayload,
        x_labels: list[str],
        threshold: int,
        boundary_x: float | None,
        series_colors: dict[str, QColor] | None = None,
    ) -> None:
        self._x_labels = list(x_labels)
        self._pra_by_index = {point.x_index: point for point in payload.pra_points}
        self._clear_series_annotation_state()
        self._tooltip_rows_by_index = dict(payload.tooltip_rows_by_index)
        self._current_cursor_index = None
        self._reset_tooltip()

        self._axis.set_labels(self._x_labels)

        # clear() сбрасывает и пользовательские элементы графика, поэтому
        # persistent guide lines и legend ниже добавляются заново.
        self._plot_item.clear()
        for item in self._right_items:
            try:
                self._right_view.removeItem(item)
            except Exception:
                pass
        self._right_items.clear()

        if self._legend is not None:
            try:
                self._legend.scene().removeItem(self._legend)
            except Exception:
                pass
            self._legend = None

        self._plot_item.showGrid(x=True, y=True, alpha=0.10)
        self._plot_item.showAxis("right")
        self._plot_item.getAxis("right").linkToView(self._right_view)
        self._plot_item.getAxis("right").setLabel("PRA")
        self._plot_item.getAxis("left").setLabel("MFI")
        self._plot_item.setTitle(self._format_title(payload.title))

        self._legend = self._plot_item.addLegend(offset=(12, 12))
        self._apply_palette_theme()

        self._plot_item.addItem(self._threshold_line)
        self._plot_item.addItem(self._boundary_line)
        self._plot_item.addItem(self._cursor_line)

        self._threshold_line.setPos(float(threshold))
        self._threshold_line.show()

        if boundary_x is not None:
            self._boundary_line.setPos(boundary_x)
            self._boundary_line.show()
        else:
            self._boundary_line.hide()

        self._cursor_line.hide()

        if payload.pra_points:
            _pra_axis_color, pra_brush_color, pra_pen_color = (
                self._build_pra_theme_colors()
            )
            bar_item = pg.BarGraphItem(
                x=[point.x_index for point in payload.pra_points],
                height=[point.value for point in payload.pra_points],
                width=0.56,
                brush=pg.mkBrush(pra_brush_color),
                pen=pg.mkPen(pra_pen_color, width=1),
            )
            self._right_view.addItem(bar_item)
            self._right_items.append(bar_item)

        self._right_view.setYRange(0, 100, padding=0.02)

        color_map = series_colors or self._build_series_colors(payload)

        for series in payload.series:
            color = color_map.get(series.label, QColor.fromHsv(0, 180, 220))
            line_x_values = [point.x_index for point in series.points]
            line_y_values = [point.y_value for point in series.points]

            self._plot_item.plot(
                x=line_x_values,
                y=line_y_values,
                pen=pg.mkPen(color=color, width=2),
                name=series.label,
            )

            if series.points:
                scatter = pg.ScatterPlotItem(
                    x=[point.x_index for point in series.points],
                    y=[point.y_value for point in series.points],
                    size=6,
                    pen=pg.mkPen(color=color, width=1),
                    brush=pg.mkBrush(color),
                )
                self._plot_item.addItem(scatter)
                self._add_series_annotations(
                    series=series,
                    color=color,
                )

        if payload.series:
            self._empty_label.hide()
        else:
            self._empty_label.setText(payload.empty_message)
            self._empty_label.show()

        self.reset_view()
        self._update_right_view_geometry()

    def clear_view(self, message: str = "Нет данных") -> None:
        self._x_labels = []
        self._pra_by_index = {}
        self._clear_series_annotation_state()
        self._tooltip_rows_by_index = {}
        self._current_cursor_index = None
        self._reset_tooltip()

        self._axis.set_labels([])

        # Этот метод вызывается перед новой асинхронной загрузкой и при ошибке,
        # чтобы в окне не оставались stale-данные предыдущего пациента.
        self._plot_item.clear()

        for item in self._right_items:
            try:
                self._right_view.removeItem(item)
            except Exception:
                pass
        self._right_items.clear()

        if self._legend is not None:
            try:
                self._legend.scene().removeItem(self._legend)
            except Exception:
                pass
            self._legend = None

        self._plot_item.showGrid(x=True, y=True, alpha=0.10)
        self._plot_item.showAxis("right")
        self._plot_item.getAxis("right").linkToView(self._right_view)
        self._plot_item.getAxis("right").setLabel("PRA")
        self._plot_item.getAxis("left").setLabel("MFI")
        self._plot_item.setTitle(self._format_title(self._title))
        self._apply_palette_theme()

        self._plot_item.addItem(self._threshold_line)
        self._plot_item.addItem(self._boundary_line)
        self._plot_item.addItem(self._cursor_line)

        self._threshold_line.hide()
        self._boundary_line.hide()
        self._cursor_line.hide()

        self._empty_label.setText(message)
        self._empty_label.show()

        self._apply_x_limits()
        self.reset_view()
        self._update_right_view_geometry()

    def set_cursor_index(self, index: int | None) -> None:
        self._current_cursor_index = index
        if index is None:
            self._cursor_line.hide()
            return

        self._cursor_line.setPos(index)
        self._cursor_line.show()

    def link_x_axis_to(self, other_plot) -> None:
        self._plot_item.setXLink(other_plot._plot_item)
        self._right_view.setXLink(other_plot._plot_item)

    def reset_view(self) -> None:
        self._apply_x_limits()
        left, right, padding = self._resolved_x_range()
        self._plot_item.setXRange(left, right, padding=padding)

        self._plot_item.vb.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        self._plot_item.vb.autoRange()
        self._plot_item.setXRange(left, right, padding=padding)

        self._right_view.setYRange(0, 100, padding=0.02)
