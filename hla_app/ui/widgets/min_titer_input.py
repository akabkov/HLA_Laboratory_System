"""Общее поле ввода для нижнего порога чувствительности.

В разных окнах приложения это одно и то же доменное значение: если пользователь
оставил поле пустым, логика должна трактовать это как "использовать порог по
умолчанию", а не как отдельный введённый ноль или текст.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import QLineEdit

from hla_app.services.avg_service import DEFAULT_AVG_MIN_TITER


class MinTiterLineEdit(QLineEdit):
    """Поле ввода порога, где пустое значение означает доменный default."""

    emptyReturnPressed = Signal()

    def __init__(self, parent=None, *, default_value: int = DEFAULT_AVG_MIN_TITER):
        super().__init__(parent)
        self._default_value = max(0, int(default_value))
        self.setValidator(QIntValidator(0, 10**5, self))
        self.setPlaceholderText(str(self._default_value))
        self.setAlignment(Qt.AlignCenter)
        content_min_width = self.fontMetrics().horizontalAdvance("100000") + 24
        self.setMinimumWidth(max(self.minimumSizeHint().width(), content_min_width))

    def effective_value(self) -> int:
        text = (self.text() or "").strip()
        return int(text) if text else self._default_value

    def keyPressEvent(self, event) -> None:
        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and not (self.text() or "").strip()
        ):
            self.clearFocus()
            self.emptyReturnPressed.emit()
            event.accept()
            return

        super().keyPressEvent(event)
