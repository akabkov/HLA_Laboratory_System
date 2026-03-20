from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Callable

from PySide6.QtCore import QDate, QPoint, Qt, QTimer
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import (
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hla_app.utils.validators import (
    check_date_in_range,
    format_ddmmyyyy,
    parse_ddmmyyyy,
)


class PrefixBoundedIntValidator(QValidator):
    """
    Валидатор, который:
    - разрешает только цифры;
    - проверяет ввод по префиксу прямо в момент набора;
    - запрещает ввод, если текущее значение уже не может стать допустимым;
    - может требовать обязательный начальный префикс (например '2' или '19').
    """

    def __init__(
        self,
        *,
        bounds_provider: Callable[[], tuple[int, int]],
        max_digits: int,
        min_complete_len: int,
        required_prefix: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.bounds_provider = bounds_provider
        self.max_digits = max_digits
        self.min_complete_len = min_complete_len
        self.required_prefix = required_prefix

    def _matches_required_prefix(self, text: str) -> bool:
        if not self.required_prefix:
            return True

        need = min(len(text), len(self.required_prefix))
        return text[:need] == self.required_prefix[:need]

    def _can_become_valid(self, text: str, min_value: int, max_value: int) -> bool:
        start_len = max(len(text) + 1, self.min_complete_len)

        for total_len in range(start_len, self.max_digits + 1):
            rest = total_len - len(text)
            limit = 10**rest

            for n in range(limit):
                suffix = f"{n:0{rest}d}"
                candidate = text + suffix

                if not self._matches_required_prefix(candidate):
                    continue

                value = int(candidate)
                if min_value <= value <= max_value:
                    return True

        return False

    def validate(self, input_str: str, pos: int):
        text = input_str or ""

        if text == "":
            return QValidator.Intermediate, input_str, pos

        if not text.isdigit():
            return QValidator.Invalid, input_str, pos

        if len(text) > self.max_digits:
            return QValidator.Invalid, input_str, pos

        if not self._matches_required_prefix(text):
            return QValidator.Invalid, input_str, pos

        min_value, max_value = self.bounds_provider()
        current_value = int(text)

        is_complete_valid = (
            len(text) >= self.min_complete_len
            and min_value <= current_value <= max_value
        )

        if len(text) == self.max_digits:
            if is_complete_valid:
                return QValidator.Acceptable, input_str, pos
            return QValidator.Invalid, input_str, pos

        if is_complete_valid:
            return QValidator.Acceptable, input_str, pos

        if self._can_become_valid(text, min_value, max_value):
            return QValidator.Intermediate, input_str, pos

        return QValidator.Invalid, input_str, pos


class DateInput(QWidget):
    """
    Три поля: DD MM YYYY + кнопка календаря.

    Ввод "жёсткий":
    - нельзя ввести заведомо неверный день/месяц/год;
    - нельзя ввести '00' в день и месяц;
    - год проверяется по префиксу прямо в момент ввода;
    - учитываются дни месяца и високосность;
    - полный диапазон min/max проверяется в validate().
    """

    def __init__(
        self,
        *,
        min_date: date,
        max_date: date,
        default: date | None,
        placeholder: bool,
        required_year_prefix: str = "",
    ):
        super().__init__()
        self.min_date = min_date
        self.max_date = max_date
        self.default_date = default
        self.required_year_prefix = required_year_prefix
        self._calendar_popup = None

        self.dd = QLineEdit()
        self.mm = QLineEdit()
        self.yyyy = QLineEdit()
        self.btn_cal = QPushButton("📅")
        self.btn_cal.setFixedWidth(40)

        self.dd.setMaxLength(2)
        self.mm.setMaxLength(2)
        self.yyyy.setMaxLength(4)

        self.dd.setFixedWidth(40)
        self.mm.setFixedWidth(40)
        self.yyyy.setFixedWidth(60)

        self.dd.setAlignment(Qt.AlignCenter)
        self.mm.setAlignment(Qt.AlignCenter)
        self.yyyy.setAlignment(Qt.AlignCenter)

        self.dd.setValidator(
            PrefixBoundedIntValidator(
                bounds_provider=self._day_bounds,
                max_digits=2,
                min_complete_len=1,
                parent=self.dd,
            )
        )
        self.mm.setValidator(
            PrefixBoundedIntValidator(
                bounds_provider=self._month_bounds,
                max_digits=2,
                min_complete_len=1,
                parent=self.mm,
            )
        )
        self.yyyy.setValidator(
            PrefixBoundedIntValidator(
                bounds_provider=self._year_bounds,
                max_digits=4,
                min_complete_len=4,
                required_prefix=required_year_prefix,
                parent=self.yyyy,
            )
        )

        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.dd)
        lay.addWidget(QLabel("."))
        lay.addWidget(self.mm)
        lay.addWidget(QLabel("."))
        lay.addWidget(self.yyyy)
        lay.addWidget(self.btn_cal)
        lay.addStretch(1)
        self.setLayout(lay)

        if placeholder:
            self.dd.setPlaceholderText("дд")
            self.mm.setPlaceholderText("мм")
            self.yyyy.setPlaceholderText("гггг")

        self.dd.textChanged.connect(lambda: self._jump(self.dd, 2, self.mm))
        self.mm.textChanged.connect(lambda: self._jump(self.mm, 2, self.yyyy))

        self.dd.editingFinished.connect(lambda: self._normalize_part_width(self.dd, 2))
        self.mm.editingFinished.connect(lambda: self._normalize_part_width(self.mm, 2))

        for w in (self.dd, self.mm, self.yyyy):
            w.installEventFilter(self)

        self.yyyy.textChanged.connect(self._on_year_changed)
        self.mm.textChanged.connect(self._on_month_changed)

        self.btn_cal.clicked.connect(self._open_calendar)

        if default:
            self.set_date(default)

        self._on_year_changed()

    def _year_bounds(self) -> tuple[int, int]:
        return self.min_date.year, self.max_date.year

    def _month_bounds(self) -> tuple[int, int]:
        # Во время ввода ограничиваем только естественный диапазон месяца.
        # Полный диапазон min/max проверяется позже в validate().
        return 1, 12

    def _day_bounds(self) -> tuple[int, int]:
        # Во время ввода ограничиваем только естественный диапазон дня
        # для выбранного месяца/года.
        # Полный диапазон min/max проверяется позже в validate().
        y = self._int_or_none(self.yyyy.text()) if len(self.yyyy.text()) == 4 else None
        m = self._int_or_none(self.mm.text())

        return 1, self._days_in_month_safe(y, m)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.FocusIn and isinstance(obj, QLineEdit):
            QTimer.singleShot(0, obj.selectAll)
        return super().eventFilter(obj, event)

    def clear_date(self):
        self.dd.setText("")
        self.mm.setText("")
        self.yyyy.setText("")
        self._on_year_changed()

    def reset_to_default(self):
        if self.default_date is not None:
            self.set_date(self.default_date)
        else:
            self.clear_date()

    def _jump(self, src: QLineEdit, need: int, dst: QLineEdit):
        t = src.text()
        if len(t) >= need:
            dst.setFocus()
            dst.selectAll()

    def _int_or_none(self, s: str) -> int | None:
        s = (s or "").strip()
        return int(s) if s.isdigit() else None

    def _normalize_part_width(self, edit: QLineEdit, width: int) -> None:
        text = (edit.text() or "").strip()
        if not text or not text.isdigit():
            return
        if len(text) < width:
            edit.setText(text.zfill(width))

    def _on_year_changed(self):
        self._normalize_month_if_needed()
        self._normalize_day_if_needed()

    def _on_month_changed(self):
        self._normalize_day_if_needed()

    def _normalize_month_if_needed(self):
        m = self._int_or_none(self.mm.text())
        if m is None:
            return

        min_m, max_m = self._month_bounds()
        if m < min_m or m > max_m:
            m2 = min(max(m, min_m), max_m)
            self.mm.setText(f"{m2:02d}")

    def _normalize_day_if_needed(self):
        d = self._int_or_none(self.dd.text())
        if d is None:
            return

        min_d, max_d = self._day_bounds()
        if d < min_d or d > max_d:
            d2 = min(max(d, min_d), max_d)
            self.dd.setText(f"{d2:02d}")

    def _days_in_month_safe(self, y: int | None, m: int | None) -> int:
        if m is None or not (1 <= m <= 12):
            return 31
        if y is None:
            if m == 2:
                return 29
            if m in (4, 6, 9, 11):
                return 30
            return 31
        return monthrange(y, m)[1]

    def _open_calendar(self):
        from PySide6.QtWidgets import QFrame

        if self._calendar_popup is not None and self._calendar_popup.isVisible():
            self._calendar_popup.close()
            self._calendar_popup = None
            return

        popup = QFrame(self.window(), Qt.Popup)
        popup.setObjectName("DateCalendarPopup")
        popup.setFrameShape(QFrame.StyledPanel)

        v = QVBoxLayout(popup)
        v.setContentsMargins(4, 4, 4, 4)

        cal = QCalendarWidget(popup)
        v.addWidget(cal)

        cal.setMinimumDate(
            QDate(self.min_date.year, self.min_date.month, self.min_date.day)
        )
        cal.setMaximumDate(
            QDate(self.max_date.year, self.max_date.month, self.max_date.day)
        )

        d = self.get_date()
        if d:
            cal.setSelectedDate(QDate(d.year, d.month, d.day))
        else:
            cal.setSelectedDate(QDate.currentDate())

        def on_pick(qd: QDate):
            dsel = date(qd.year(), qd.month(), qd.day())
            self.set_date(dsel)
            popup.close()
            self._calendar_popup = None

        cal.clicked.connect(on_pick)
        cal.activated.connect(on_pick)

        popup.adjustSize()

        global_pos = self.btn_cal.mapToGlobal(QPoint(0, self.btn_cal.height()))
        popup.move(global_pos)

        self._calendar_popup = popup
        popup.show()

    def set_date(self, d: date):
        self.dd.setText(f"{d.day:02d}")
        self.mm.setText(f"{d.month:02d}")
        self.yyyy.setText(f"{d.year:04d}")
        self._on_year_changed()

    def get_date(self) -> date | None:
        if not (self.dd.text() and self.mm.text() and self.yyyy.text()):
            return None
        s = (
            f"{self.dd.text().zfill(2)}."
            f"{self.mm.text().zfill(2)}."
            f"{self.yyyy.text().zfill(4)}"
        )
        return parse_ddmmyyyy(s)

    def validate(self) -> tuple[bool, str]:
        d = self.get_date()
        if not d:
            self.reset_to_default()
            return False, "не заполнена или неверный формат"

        if not check_date_in_range(d, self.min_date, self.max_date):
            self.reset_to_default()
            return (
                False,
                f"должна быть в диапазоне {format_ddmmyyyy(self.min_date)} – {format_ddmmyyyy(self.max_date)}",
            )

        return True, ""
