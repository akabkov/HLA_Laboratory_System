"""Немодальное окно интерактивной динамики антител пациента.

Окно получает header пациента только из основной БД приложения, а затем
строит синхронные графики `Class I` и `Class II` по объединённой хронологии
исследований. Дополнительная БД используется только как опциональный источник
дополнительных тестов и не подменяет основную карточку пациента.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import (
    QPoint,
    QSignalBlocker,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontInfo, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from hla_app.services.antibody_dynamics_models import DynamicsViewOptions
from hla_app.services.antibody_dynamics_service import (
    build_patient_dynamics_view,
    list_available_antibody_labels,
)
from hla_app.services.app_prefs import load_effective_dynamics_secondary_db_preferences
from hla_app.ui.widgets.antibody_dynamics_plot import AntibodyDynamicsPlot
from hla_app.ui.widgets.min_titer_input import MinTiterLineEdit
from hla_app.ui.workers.antibody_dynamics_worker import AntibodyDynamicsLoadTask
from hla_app.utils.validators import format_ddmmyyyy


def _popup_row_height(view: QAbstractItemView | None, fallback_height: int) -> int:
    if view is None:
        return fallback_height

    row_height = view.sizeHintForRow(0)
    if row_height <= 0:
        row_height = fallback_height
    return row_height


def _popup_content_height(
    view: QAbstractItemView | None,
    item_count: int,
    fallback_height: int,
) -> int:
    if view is None or item_count <= 0:
        return 0

    frame_height = view.frameWidth() * 2
    margins = 8
    return (
        frame_height + margins + _popup_row_height(view, fallback_height) * item_count
    )


def _popup_width(anchor_width: int, view: QAbstractItemView | None) -> int:
    if view is None:
        return anchor_width

    list_width = view.sizeHintForColumn(0)
    if list_width < 0:
        list_width = 0

    scrollbar_width = view.verticalScrollBar().sizeHint().width()
    return max(anchor_width, list_width + scrollbar_width + 32)


def _screen_fitted_popup_geometry(
    anchor: QWidget,
    *,
    popup_width: int,
    content_height: int,
) -> tuple[int, int, int, int] | None:
    button_bottom_left = anchor.mapToGlobal(QPoint(0, anchor.height()))
    button_top_left = anchor.mapToGlobal(QPoint(0, 0))

    screen = QGuiApplication.screenAt(button_bottom_left)
    if screen is None:
        screen = anchor.window().screen()
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None

    available = screen.availableGeometry()
    margin = 8
    popup_width = min(popup_width, max(120, available.width() - margin * 2))

    below_space = max(0, available.bottom() - button_bottom_left.y() - margin + 1)
    above_space = max(0, button_top_left.y() - available.top() - margin)

    show_above = False
    available_height = below_space
    if content_height > below_space and above_space > below_space:
        show_above = True
        available_height = above_space

    popup_height = min(content_height, available_height) if content_height else 0
    if popup_height <= 0:
        popup_height = min(max(below_space, above_space), anchor.height() * 8)
    if popup_height <= 0:
        popup_height = anchor.height() * 4

    x = button_top_left.x()
    max_x = available.right() - margin - popup_width + 1
    x = max(available.left() + margin, min(x, max_x))

    if show_above:
        y = button_top_left.y() - popup_height
    else:
        y = button_bottom_left.y()

    max_y = available.bottom() - margin - popup_height + 1
    y = max(available.top() + margin, min(y, max_y))

    return x, y, popup_width, popup_height


class _DropdownTriggerComboBox(QComboBox):
    popupRequested = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.popupRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if self.isEnabled() and event.key() in (
            Qt.Key_Space,
            Qt.Key_Return,
            Qt.Key_Enter,
            Qt.Key_Down,
        ):
            self.popupRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _ScreenAwareComboBox(QComboBox):
    def showPopup(self) -> None:
        view = self.view()
        content_height = _popup_content_height(
            view, self.count(), self.sizeHint().height()
        )
        popup_geometry = _screen_fitted_popup_geometry(
            self,
            popup_width=_popup_width(self.width(), view),
            content_height=content_height,
        )
        if popup_geometry is not None:
            row_height = _popup_row_height(view, self.sizeHint().height())
            visible_items = max(1, popup_geometry[3] // max(1, row_height))
            self.setMaxVisibleItems(min(self.count(), visible_items))

        super().showPopup()
        QTimer.singleShot(0, self._sync_popup_geometry)

    def _sync_popup_geometry(self) -> None:
        view = self.view()
        if view is None:
            return

        popup = view.window()
        if popup is None or not popup.isVisible():
            return

        popup_geometry = _screen_fitted_popup_geometry(
            self,
            popup_width=_popup_width(self.width(), view),
            content_height=_popup_content_height(
                view,
                self.count(),
                self.sizeHint().height(),
            ),
        )
        if popup_geometry is None:
            return

        popup.setGeometry(*popup_geometry)


class AntibodySelectionDropdown(QWidget):
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels: list[str] = []
        self._popup: QFrame | None = None
        self._listw: QListWidget | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._button = _DropdownTriggerComboBox(self)
        self._button.addItem("")
        self._button.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._button.popupRequested.connect(self._toggle_popup)
        layout.addWidget(self._button)

        self._update_button_text()

    def _ensure_popup(self) -> None:
        if self._popup is not None:
            return

        popup_parent = self.window()
        if popup_parent is self:
            popup_parent = None

        self._popup = QFrame(popup_parent, Qt.Popup)
        self._popup.setFrameShape(QFrame.StyledPanel)

        popup_layout = QVBoxLayout(self._popup)
        popup_layout.setContentsMargins(4, 4, 4, 4)
        popup_layout.setSpacing(0)

        self._listw = QListWidget(self._popup)
        self._listw.setSelectionMode(QAbstractItemView.NoSelection)
        self._listw.setUniformItemSizes(True)
        self._listw.itemChanged.connect(self._on_item_changed)
        popup_layout.addWidget(self._listw)

    def _toggle_popup(self) -> None:
        if self.is_popup_visible():
            self.hide_popup()
            return

        self.show_popup()

    def is_popup_visible(self) -> bool:
        return self._popup is not None and self._popup.isVisible()

    def hide_popup(self) -> None:
        if self._popup is not None:
            self._popup.hide()

    def show_popup(self) -> None:
        if not self._labels:
            return

        self._ensure_popup()
        popup_geometry = _screen_fitted_popup_geometry(
            self._button,
            popup_width=_popup_width(self._button.width(), self._listw),
            content_height=_popup_content_height(
                self._listw,
                len(self._labels),
                self._button.sizeHint().height(),
            ),
        )
        if popup_geometry is not None:
            self._popup.setGeometry(*popup_geometry)
        self._popup.show()
        self._popup.raise_()
        self._listw.setFocus()

    def _update_button_text(self) -> None:
        checked = self.checked_labels()

        if not self._labels:
            text = "Нет антител"
        elif not checked:
            text = "Все антитела"
        elif len(checked) == 1:
            text = checked[0]
        else:
            text = f"Выбрано: {len(checked)}"

        self._button.setItemText(0, text)
        self._button.updateGeometry()
        self._button.setToolTip("\n".join(checked) if checked else text)
        self._button.setEnabled(self.isEnabled() and bool(self._labels))

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_button_text()
        self.selectionChanged.emit()

    def set_items(
        self,
        labels: list[str],
        checked_labels: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self._labels = list(labels)
        checked = (
            set(self.checked_labels())
            if checked_labels is None
            else {str(label) for label in checked_labels}
        )

        self._ensure_popup()
        blocker = QSignalBlocker(self._listw)
        self._listw.clear()

        for label in self._labels:
            item = QListWidgetItem(label, self._listw)
            item.setFlags(
                (item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                & ~Qt.ItemIsSelectable
            )
            item.setCheckState(Qt.Checked if label in checked else Qt.Unchecked)

        del blocker

        if not self._labels:
            self.hide_popup()

        self._update_button_text()

    def checked_labels(self) -> list[str]:
        if self._listw is None:
            return []

        result = []
        for index in range(self._listw.count()):
            item = self._listw.item(index)
            if item.checkState() == Qt.Checked:
                result.append(item.text())
        return result

    def has_checked_items(self) -> bool:
        return bool(self.checked_labels())

    def clear_checked(self, *, emit_signal: bool = True) -> None:
        if self._listw is None:
            return

        changed = False
        blocker = QSignalBlocker(self._listw)
        for index in range(self._listw.count()):
            item = self._listw.item(index)
            if item.checkState() == Qt.Checked:
                item.setCheckState(Qt.Unchecked)
                changed = True
        del blocker

        self._update_button_text()
        if changed and emit_signal:
            self.selectionChanged.emit()

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        if not enabled:
            self.hide_popup()
        self._update_button_text()


# --- Главное окно динамики: header пациента, controls и два синхронных графика ---
class AntibodyDynamicsWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Динамика антител")
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(800, 800)

        self._thread_pool = QThreadPool(self)
        self._request_seq = 0
        self._active_request_id = 0
        self._current_patient_code: str | None = None
        self._raw_payload = None
        self._controls_enabled = False
        self._antibody_filter_locked = False
        self._ab_drb1_restore_checked_state = True
        self._available_dates: list[date] = []
        self._date_from_restore_text: str | None = None
        self._date_to_restore_text: str | None = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)

        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.lbl_patient_name = QLabel("")
        patient_name_font = QFont(self.lbl_patient_name.font())
        base_point_size = QFontInfo(patient_name_font).pointSizeF()
        if base_point_size <= 0:
            base_point_size = 10.0
        patient_name_font.setPointSizeF(base_point_size + 10.0)
        patient_name_font.setBold(True)
        self.lbl_patient_name.setFont(patient_name_font)

        self.lbl_birth_date = QLabel("")
        self.lbl_sex = QLabel("")
        self.lbl_source_status = QLabel("")
        self.lbl_source_status.setWordWrap(True)
        self.lbl_organ = QLabel("")
        self.lbl_organ.setAlignment(Qt.AlignCenter)

        organ_font = QFont(self.lbl_organ.font())
        organ_font.setBold(True)
        self.lbl_organ.setFont(organ_font)

        title_row = QWidget()
        title_layout = QGridLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setHorizontalSpacing(12)
        title_layout.setVerticalSpacing(0)
        title_layout.setColumnStretch(0, 1)
        title_layout.setColumnStretch(1, 1)
        title_layout.setColumnStretch(2, 1)
        title_layout.addWidget(
            self.lbl_patient_name, 0, 0, Qt.AlignLeft | Qt.AlignVCenter
        )
        title_layout.addWidget(self.lbl_organ, 0, 1, Qt.AlignCenter)

        meta_row = QWidget()
        meta_layout = QHBoxLayout(meta_row)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(14)
        meta_layout.addWidget(self.lbl_birth_date)
        meta_layout.addWidget(self.lbl_sex)
        meta_layout.addStretch(1)

        header_layout.addWidget(title_row)
        header_layout.addWidget(meta_row)

        root_layout.addWidget(header_widget)

        controls_widget = QWidget()
        controls_layout = QHBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(24)

        lbl_resolution = QLabel("Разрешение:")
        self.cb_resolution = QComboBox()
        self.cb_resolution.addItem("Низкое", "low")
        self.cb_resolution.addItem("Высокое", "high")

        self.chk_ab_drb1 = QCheckBox("Только A, B и DRB1")
        self.chk_ab_drb1.setChecked(True)

        lbl_threshold = QLabel("Нижний порог чувствительности:")
        self.ed_min_titer = MinTiterLineEdit(self)

        lbl_selected_antibodies = QLabel("Выбрать антитела:")
        self.dropdown_antibodies = AntibodySelectionDropdown(self)
        self.btn_reset_antibodies = QPushButton("Сбросить")
        # Enter в поле порога не должен случайно активировать служебные reset-кнопки
        # окна, иначе это сбрасывает текущие UI-фильтры вместо перестроения графика.
        self.btn_reset_antibodies.setAutoDefault(False)
        self.btn_reset_antibodies.setDefault(False)

        self.btn_reset_zoom = QPushButton("Сбросить масштаб")
        self.btn_reset_zoom.setAutoDefault(False)
        self.btn_reset_zoom.setDefault(False)

        lbl_date_from = QLabel("С:")
        self.cb_date_from = _ScreenAwareComboBox()
        self.cb_date_from.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.cb_date_from.setMinimumContentsLength(5)
        self.cb_date_from.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lbl_date_to = QLabel("По:")
        self.cb_date_to = _ScreenAwareComboBox()
        self.cb_date_to.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        self.cb_date_to.setMinimumContentsLength(5)
        self.cb_date_to.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_reset_date_range = QPushButton("Сбросить")
        self.btn_reset_date_range.setAutoDefault(False)
        self.btn_reset_date_range.setDefault(False)
        reset_buttons_width = max(
            self.btn_reset_antibodies.sizeHint().width(),
            self.btn_reset_date_range.sizeHint().width(),
        )
        self.btn_reset_antibodies.setFixedWidth(reset_buttons_width)
        self.btn_reset_date_range.setFixedWidth(reset_buttons_width)

        self._left_zone = QWidget()
        left_layout = QVBoxLayout(self._left_zone)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self._lbl_threshold = lbl_threshold
        self._left_top_row = QWidget()
        left_top_layout = QHBoxLayout(self._left_top_row)
        left_top_layout.setContentsMargins(0, 0, 0, 0)
        left_top_layout.setSpacing(10)
        left_top_layout.addWidget(lbl_resolution)
        left_top_layout.addWidget(self.cb_resolution)
        left_top_layout.addWidget(self.chk_ab_drb1)
        left_top_layout.addStretch(1)

        left_bottom_row = QWidget()
        self._left_bottom_layout = QHBoxLayout(left_bottom_row)
        self._left_bottom_layout.setContentsMargins(0, 0, 0, 0)
        self._left_bottom_layout.setSpacing(10)
        self._left_bottom_layout.addWidget(lbl_threshold)
        self._left_bottom_layout.addWidget(self.ed_min_titer)
        self._left_bottom_layout.addStretch(1)

        left_layout.addWidget(self._left_top_row)
        left_layout.addWidget(left_bottom_row)

        self._center_zone = QWidget()
        center_layout = QHBoxLayout(self._center_zone)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)

        center_content_zone = QWidget()
        center_content_layout = QVBoxLayout(center_content_zone)
        center_content_layout.setContentsMargins(0, 0, 0, 0)
        center_content_layout.setSpacing(6)

        center_top_row = QWidget()
        right_top_layout = QHBoxLayout(center_top_row)
        right_top_layout.setContentsMargins(0, 0, 0, 0)
        right_top_layout.setSpacing(10)
        right_top_layout.addWidget(lbl_selected_antibodies)
        right_top_layout.addWidget(self.dropdown_antibodies, 1)

        center_bottom_row = QWidget()
        right_bottom_layout = QHBoxLayout(center_bottom_row)
        right_bottom_layout.setContentsMargins(0, 0, 0, 0)
        right_bottom_layout.setSpacing(10)

        date_range_widget = QWidget()
        date_range_layout = QHBoxLayout(date_range_widget)
        date_range_layout.setContentsMargins(0, 0, 0, 0)
        date_range_layout.setSpacing(10)
        date_range_layout.addWidget(lbl_date_from)
        date_range_layout.addWidget(self.cb_date_from)
        date_range_layout.addWidget(lbl_date_to)
        date_range_layout.addWidget(self.cb_date_to)

        right_bottom_layout.addWidget(date_range_widget)

        center_content_layout.addWidget(center_top_row)
        center_content_layout.addWidget(center_bottom_row)

        reset_buttons_zone = QWidget()
        reset_buttons_layout = QVBoxLayout(reset_buttons_zone)
        reset_buttons_layout.setContentsMargins(0, 0, 0, 0)
        reset_buttons_layout.setSpacing(6)
        reset_buttons_layout.addWidget(self.btn_reset_antibodies)
        reset_buttons_layout.addWidget(self.btn_reset_date_range)
        reset_buttons_layout.addStretch(1)

        center_layout.addWidget(center_content_zone)
        center_layout.addWidget(reset_buttons_zone, 0, Qt.AlignTop)

        self._right_zone = QWidget()
        right_layout = QVBoxLayout(self._right_zone)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self.btn_reset_zoom, 0, Qt.AlignRight | Qt.AlignTop)

        # Средний блок должен центрироваться относительно всего окна, а не
        # только между левой и правой зонами. Для этого правая зона получает
        # ту же опорную ширину, что и левая, а сама кнопка остаётся прижатой вправо.
        self._right_zone.setMinimumWidth(self._left_zone.sizeHint().width())

        controls_layout.addWidget(self._left_zone, 0)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self._center_zone, 0)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self._right_zone, 0, Qt.AlignRight | Qt.AlignTop)

        root_layout.addWidget(controls_widget)

        self.status_widget = QWidget()
        status_layout = QVBoxLayout(self.status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        status_layout.addWidget(self.lbl_source_status)
        root_layout.addWidget(self.status_widget)
        self.status_widget.hide()

        self.plot_class1 = AntibodyDynamicsPlot("Class I", self)
        self.plot_class2 = AntibodyDynamicsPlot("Class II", self)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.plot_class1)
        splitter.addWidget(self.plot_class2)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root_layout.addWidget(splitter, 1)

        self.cb_resolution.currentIndexChanged.connect(self._on_resolution_changed)
        self.chk_ab_drb1.toggled.connect(self._rebuild_view_from_cached_raw)
        self.dropdown_antibodies.selectionChanged.connect(
            self._on_antibody_selection_changed
        )
        self.btn_reset_antibodies.clicked.connect(self._reset_antibody_selection)
        self.ed_min_titer.editingFinished.connect(self._rebuild_view_from_cached_raw)
        self.ed_min_titer.returnPressed.connect(self.ed_min_titer.clearFocus)
        self.ed_min_titer.emptyReturnPressed.connect(self._rebuild_view_from_cached_raw)
        self.cb_date_from.currentIndexChanged.connect(self._on_date_from_changed)
        self.cb_date_to.currentIndexChanged.connect(self._on_date_to_changed)
        self.btn_reset_date_range.clicked.connect(self._reset_date_range_selection)
        self.btn_reset_zoom.clicked.connect(self._reset_zoom)

        self.plot_class1.cursorIndexChanged.connect(self._sync_cursor_index)
        self.plot_class2.cursorIndexChanged.connect(self._sync_cursor_index)
        self.plot_class2.link_x_axis_to(self.plot_class1)

        QTimer.singleShot(0, self._sync_min_titer_width)
        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._controls_enabled = enabled
        self.cb_resolution.setEnabled(enabled)
        self.chk_ab_drb1.setEnabled(enabled and not self._antibody_filter_locked)
        self.ed_min_titer.setEnabled(enabled)
        self.dropdown_antibodies.setEnabled(enabled)
        self.cb_date_from.setEnabled(enabled and self.cb_date_from.count() > 0)
        self.cb_date_to.setEnabled(enabled and self.cb_date_to.count() > 0)
        self.btn_reset_antibodies.setEnabled(
            enabled
            and (
                self._antibody_filter_locked
                or self.dropdown_antibodies.has_checked_items()
            )
        )
        self.btn_reset_date_range.setEnabled(enabled and self._has_custom_date_range())
        self.btn_reset_zoom.setEnabled(enabled)

    def _clear_plots(self, message: str) -> None:
        self.plot_class1.clear_view(message)
        self.plot_class2.clear_view(message)

    def _set_status_message(self, text: str | None) -> None:
        status_text = (text or "").strip()
        self.lbl_source_status.setText(status_text)
        self.status_widget.setVisible(bool(status_text))

    def _sync_min_titer_width(self) -> None:
        target_width = (
            self._left_top_row.sizeHint().width()
            - self._lbl_threshold.sizeHint().width()
            - self._left_bottom_layout.spacing()
        )
        min_width = self.ed_min_titer.minimumSizeHint().width()
        self.ed_min_titer.setFixedWidth(max(min_width, target_width))
        self._center_zone.setMinimumWidth(self._center_zone.sizeHint().width())
        self._right_zone.setMinimumWidth(self._left_zone.sizeHint().width())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_min_titer_width()

    def _remember_date_range_selection(self) -> None:
        if self.cb_date_from.count() > 0:
            self._date_from_restore_text = self.cb_date_from.currentText() or None
        if self.cb_date_to.count() > 0:
            self._date_to_restore_text = self.cb_date_to.currentText() or None

    def _selected_date_range_dates(self) -> tuple[date | None, date | None]:
        start_index, end_index = self._selected_date_range_indices()
        if (
            start_index is None
            or end_index is None
            or not self._available_dates
            or start_index >= len(self._available_dates)
            or end_index >= len(self._available_dates)
        ):
            return None, None

        return self._available_dates[start_index], self._available_dates[end_index]

    def _selected_date_range_indices(self) -> tuple[int | None, int | None]:
        if self.cb_date_from.count() == 0 or self.cb_date_to.count() == 0:
            return None, None

        start_index = self.cb_date_from.currentData()
        end_index = self.cb_date_to.currentData()
        if start_index is None or end_index is None:
            return None, None

        return int(start_index), int(end_index)

    def _has_custom_date_range(self) -> bool:
        if not self._available_dates:
            return False

        start_index, end_index = self._selected_date_range_indices()
        if start_index is None or end_index is None:
            return False

        return start_index != 0 or end_index != len(self._available_dates) - 1

    def _refresh_date_from_options(self, selected_from_text: str | None = None) -> None:
        labels_count = len(self._available_dates)
        end_index = self.cb_date_to.currentData()

        blocker = QSignalBlocker(self.cb_date_from)
        self.cb_date_from.clear()

        # При новой загрузке пациента combo ещё могут хранить stale userData от
        # предыдущего набора дат. Сначала обнуляем пустой диапазон, а затем
        # clamping'ом поджимаем старый индекс под новую chronology.
        if labels_count <= 0:
            del blocker
            return

        if end_index is None:
            end_index = labels_count - 1
        end_index = max(0, min(int(end_index), labels_count - 1))

        for index in range(0, end_index + 1):
            self.cb_date_from.addItem(
                format_ddmmyyyy(self._available_dates[index]), index
            )

        if self.cb_date_from.count() > 0:
            from_index = (
                self.cb_date_from.findText(selected_from_text)
                if selected_from_text
                else -1
            )
            self.cb_date_from.setCurrentIndex(from_index if from_index >= 0 else 0)

        del blocker

    def _refresh_date_to_options(self, selected_to_text: str | None = None) -> None:
        labels_count = len(self._available_dates)
        start_index = self.cb_date_from.currentData()

        blocker = QSignalBlocker(self.cb_date_to)
        self.cb_date_to.clear()

        # Симметричная защита для верхней границы диапазона: после смены
        # пациента или ошибки загрузки старый индекс не должен ломать rebuild.
        if labels_count <= 0:
            del blocker
            return

        start_index = int(start_index) if start_index is not None else 0
        start_index = max(0, min(start_index, labels_count - 1))

        for index in range(labels_count - 1, start_index - 1, -1):
            self.cb_date_to.addItem(
                format_ddmmyyyy(self._available_dates[index]), index
            )

        if self.cb_date_to.count() > 0:
            to_index = (
                self.cb_date_to.findText(selected_to_text) if selected_to_text else -1
            )
            self.cb_date_to.setCurrentIndex(to_index if to_index >= 0 else 0)

        del blocker

    def _apply_selected_date_range(self, *, changed_source: str | None = None) -> None:
        start_index, end_index = self._selected_date_range_indices()
        if start_index is None or end_index is None:
            return

        # Combo `С:` идёт по возрастанию, а `По:` по убыванию, но в userData
        # обеих combo хранится исходный X-index общей оси графиков.
        if start_index > end_index:
            if changed_source == "to":
                blocker = QSignalBlocker(self.cb_date_from)
                self.cb_date_from.setCurrentIndex(self.cb_date_from.findData(end_index))
                del blocker
                start_index = end_index
            else:
                blocker = QSignalBlocker(self.cb_date_to)
                self.cb_date_to.setCurrentIndex(self.cb_date_to.findData(start_index))
                del blocker
                end_index = start_index

        self._remember_date_range_selection()
        self._rebuild_view_from_cached_raw()

    def _refresh_date_range_controls(
        self,
        available_dates: list[date] | None,
        *,
        preserve_selected: bool = True,
    ) -> None:
        self._available_dates = list(available_dates or [])
        selected_from = self._date_from_restore_text if preserve_selected else None
        selected_to = self._date_to_restore_text if preserve_selected else None

        self._refresh_date_from_options(selected_from)
        self._refresh_date_to_options(selected_to)
        self._refresh_date_from_options(self.cb_date_from.currentText() or None)

        self.cb_date_from.setEnabled(
            self._controls_enabled and bool(self._available_dates)
        )
        self.cb_date_to.setEnabled(
            self._controls_enabled and self.cb_date_to.count() > 0
        )
        self._set_controls_enabled(self._controls_enabled)

    def _collect_view_options(self) -> DynamicsViewOptions:
        selected_from_date, selected_to_date = self._selected_date_range_dates()

        return DynamicsViewOptions(
            resolution=self.cb_resolution.currentData(),
            only_ab_drb1=(
                self.chk_ab_drb1.isChecked() and not self._antibody_filter_locked
            ),
            min_titer=self.ed_min_titer.effective_value(),
            selected_antibodies=tuple(self.dropdown_antibodies.checked_labels()),
            selected_from_date=selected_from_date,
            selected_to_date=selected_to_date,
        )

    def _build_shared_series_colors(self, view) -> dict[str, QColor]:
        all_series = [
            series
            for payload in (view.class1, view.class2)
            for series in payload.series
        ]
        if not all_series:
            return {}

        # Сначала используем достаточно контрастный фиксированный набор цветов,
        # общий для обеих панелей. Это исключает повторы между Class I и II.
        base_palette = [
            "#1f77b4",
            "#d62728",
            "#2ca02c",
            "#9467bd",
            "#ff7f0e",
            "#17becf",
            "#8c564b",
            "#e377c2",
            "#7f7f7f",
            "#bcbd22",
            "#006ba4",
            "#ff800e",
            "#ababab",
            "#595959",
            "#5f9ed1",
            "#c85200",
            "#898989",
            "#a2c8ec",
            "#ffbc79",
            "#cfcfcf",
            "#005b96",
            "#b30000",
            "#007d34",
            "#803e75",
        ]

        group_labels = sorted({series.color_group for series in all_series})
        group_colors: dict[str, QColor] = {}

        for index, group_label in enumerate(group_labels):
            if index < len(base_palette):
                group_colors[group_label] = QColor(base_palette[index])
                continue

            extra_index = index - len(base_palette)
            hue = (29 + 53 * extra_index) % 360
            sat = (205, 175, 235)[extra_index % 3]
            val = (220, 235, 200)[(extra_index // 3) % 3]
            group_colors[group_label] = QColor.fromHsv(hue, sat, val)

        return {series.label: group_colors[series.color_group] for series in all_series}

    def _update_header(self, raw_payload) -> None:
        header = raw_payload.header

        self.lbl_patient_name.setText(header.full_name or "Пациент")
        self.lbl_organ.setText((header.organ_title or "").strip())

        if header.birth_date is not None:
            self.lbl_birth_date.setText(
                f"Дата рождения: {format_ddmmyyyy(header.birth_date)} г."
            )
        else:
            self.lbl_birth_date.setText("Дата рождения: не указана")

        if header.sex == "m":
            sex_text = "мужской"
        elif header.sex == "f":
            sex_text = "женский"
        else:
            sex_text = "не указан"
        self.lbl_sex.setText(f"Пол: {sex_text}")
        if header.full_name:
            self.setWindowTitle(f"Динамика антител — {header.full_name}")
        else:
            self.setWindowTitle("Динамика антител")

    def _clear_header(self, *, loading: bool = False) -> None:
        self.lbl_patient_name.setText("Загрузка пациента..." if loading else "Пациент")
        self.lbl_organ.setText("")
        self.lbl_birth_date.setText("")
        self.lbl_sex.setText("")
        self.setWindowTitle("Динамика антител")

    def _sync_antibody_filter_after_items_refresh(self) -> None:
        if self.dropdown_antibodies.has_checked_items():
            if not self._antibody_filter_locked:
                self._lock_ab_drb1_for_antibody_filter()
            else:
                self._set_controls_enabled(self._controls_enabled)
            return

        if self._antibody_filter_locked:
            self._restore_ab_drb1_after_antibody_reset()
        else:
            self._set_controls_enabled(self._controls_enabled)

    def _refresh_antibody_dropdown_items(
        self, *, preserve_checked: bool = True
    ) -> None:
        checked_labels = (
            self.dropdown_antibodies.checked_labels() if preserve_checked else []
        )

        if self._raw_payload is None:
            self.dropdown_antibodies.set_items([], checked_labels=[])
            self._sync_antibody_filter_after_items_refresh()
            return

        labels = list_available_antibody_labels(
            self._raw_payload,
            resolution=self.cb_resolution.currentData(),
            selected_from_date=self._selected_date_range_dates()[0],
            selected_to_date=self._selected_date_range_dates()[1],
        )
        self.dropdown_antibodies.set_items(labels, checked_labels=checked_labels)
        self._sync_antibody_filter_after_items_refresh()

    def _lock_ab_drb1_for_antibody_filter(self) -> None:
        if self._antibody_filter_locked:
            return

        self._ab_drb1_restore_checked_state = self.chk_ab_drb1.isChecked()
        self._antibody_filter_locked = True
        self._set_controls_enabled(self._controls_enabled)

    def _restore_ab_drb1_after_antibody_reset(self) -> None:
        if not self._antibody_filter_locked:
            self._set_controls_enabled(self._controls_enabled)
            return

        self._antibody_filter_locked = False
        blocker = QSignalBlocker(self.chk_ab_drb1)
        self.chk_ab_drb1.setChecked(self._ab_drb1_restore_checked_state)
        del blocker
        self._set_controls_enabled(self._controls_enabled)

    def _on_antibody_selection_changed(self) -> None:
        if self.dropdown_antibodies.has_checked_items():
            self._lock_ab_drb1_for_antibody_filter()

        self._set_controls_enabled(self._controls_enabled)
        self._rebuild_view_from_cached_raw()

    def _reset_antibody_selection(self) -> None:
        self.dropdown_antibodies.clear_checked(emit_signal=False)
        self._restore_ab_drb1_after_antibody_reset()
        self._rebuild_view_from_cached_raw()

    def _on_resolution_changed(self) -> None:
        if self._raw_payload is None:
            return

        if self._antibody_filter_locked or self.dropdown_antibodies.has_checked_items():
            self.dropdown_antibodies.clear_checked(emit_signal=False)
            self._restore_ab_drb1_after_antibody_reset()

        self._refresh_antibody_dropdown_items(preserve_checked=False)
        self._rebuild_view_from_cached_raw()

    def _on_date_from_changed(self) -> None:
        self._refresh_date_to_options(self.cb_date_to.currentText() or None)
        self._apply_selected_date_range(changed_source="from")
        self._set_controls_enabled(self._controls_enabled)

    def _on_date_to_changed(self) -> None:
        self._refresh_date_from_options(self.cb_date_from.currentText() or None)
        self._apply_selected_date_range(changed_source="to")
        self._set_controls_enabled(self._controls_enabled)

    def _reset_date_range_selection(self) -> None:
        if not self._available_dates:
            return

        self._refresh_date_from_options(format_ddmmyyyy(self._available_dates[0]))
        self._refresh_date_to_options(format_ddmmyyyy(self._available_dates[-1]))
        self._refresh_date_from_options(self.cb_date_from.currentText() or None)
        self._apply_selected_date_range()
        self._set_controls_enabled(self._controls_enabled)

    def load_patient(self, patient_code: str) -> None:
        self._current_patient_code = patient_code
        self._raw_payload = None
        self._request_seq += 1
        self._active_request_id = self._request_seq

        self._remember_date_range_selection()
        self.dropdown_antibodies.hide_popup()
        self._clear_header(loading=True)
        self._set_status_message("Загрузка данных...")
        self._set_controls_enabled(False)
        self._refresh_date_range_controls([], preserve_selected=True)
        self._clear_plots("Загрузка данных...")

        # Настройки дополнительной БД читаются заново на каждую загрузку
        # пациента, чтобы окно не держало stale effective config.
        secondary_config = load_effective_dynamics_secondary_db_preferences()

        task = AntibodyDynamicsLoadTask(
            request_id=self._active_request_id,
            patient_code=patient_code,
            secondary_config=secondary_config,
        )
        task.signals.finished.connect(self._on_load_finished)
        task.signals.failed.connect(self._on_load_failed)
        self._thread_pool.start(task)

    def reload_current_patient(self) -> None:
        if self._current_patient_code:
            self.load_patient(self._current_patient_code)

    def _on_load_finished(self, request_id: int, raw_payload) -> None:
        if request_id != self._active_request_id:
            return

        self._raw_payload = raw_payload
        self._update_header(raw_payload)
        self._set_status_message(None)
        self._refresh_date_range_controls(
            sorted({row.test_date for row in raw_payload.rows}),
            preserve_selected=True,
        )
        self._set_controls_enabled(True)
        self._rebuild_view_from_cached_raw()

    def _on_load_failed(self, request_id: int, error_text: str) -> None:
        if request_id != self._active_request_id:
            return

        self._clear_header()
        self.dropdown_antibodies.hide_popup()
        self._set_controls_enabled(False)
        self._refresh_date_range_controls([], preserve_selected=True)
        self._clear_plots("Не удалось загрузить данные.")
        self._set_status_message("Не удалось загрузить данные.")

        QMessageBox.critical(
            self,
            "Ошибка",
            f"Не удалось загрузить динамику антител:\n{error_text}",
        )

    def _rebuild_view_from_cached_raw(self) -> None:
        if self._raw_payload is None:
            return

        self._refresh_antibody_dropdown_items()
        options = self._collect_view_options()
        # Перестроение графиков по UI-настройкам идёт только из cached raw payload
        # и не должно повторно ходить в PostgreSQL.
        view = build_patient_dynamics_view(self._raw_payload, options)
        series_colors = self._build_shared_series_colors(view)

        self.plot_class1.apply_view(
            view.class1,
            view.x_labels,
            view.threshold_value,
            view.boundary_x,
            series_colors=series_colors,
        )
        self.plot_class2.apply_view(
            view.class2,
            view.x_labels,
            view.threshold_value,
            view.boundary_x,
            series_colors=series_colors,
        )
        self._refresh_date_range_controls(view.available_dates)

    def _sync_cursor_index(self, index) -> None:
        self.plot_class1.set_cursor_index(index)
        self.plot_class2.set_cursor_index(index)

    def _reset_zoom(self) -> None:
        self.plot_class1.reset_view()
        self.plot_class2.reset_view()
