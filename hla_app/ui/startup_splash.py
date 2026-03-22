"""Splash-экран запуска приложения.

Файл содержит отрисовку стартового окна, текста статуса и визуального оформления
при запуске системы. Если на старте неверно показываются сообщения, логотип или
оформление заставки, соответствующая логика находится здесь.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from hla_app.__about__ import __copyright__, __title__, __version__

# --- Виджет заставки и журнала запуска приложения ---


class StartupSplash(QWidget):
    def __init__(self, background_path: Path | None = None, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SplashScreen,
        )

        self.setFixedSize(640, 360)
        self._background = (
            QPixmap(str(background_path))
            if background_path and background_path.exists()
            else QPixmap()
        )
        self._log_lines: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.setSpacing(0)

        outer.addStretch(1)

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet("""
            QFrame#card {
                background-color: rgba(9, 20, 33, 185);
                border: 1px solid rgba(255, 255, 255, 28);
                border-radius: 18px;
            }
            QLabel#title {
                color: white;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#subtitle {
                color: rgba(255, 255, 255, 180);
                font-size: 12px;
            }
            QLabel#status {
                color: white;
                font-size: 16px;
                font-weight: 600;
            }
            QLabel#details {
                color: rgba(255, 255, 255, 210);
                font-size: 12px;
            }
            QProgressBar {
                height: 14px;
                border: none;
                border-radius: 7px;
                text-align: center;
                color: white;
                background-color: rgba(255, 255, 255, 25);
            }
            QProgressBar::chunk {
                border-radius: 7px;
                background-color: #53c6d9;
            }
            """)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(10)

        self.lbl_title = QLabel(f"{__title__} {__version__}")
        self.lbl_title.setObjectName("title")

        self.lbl_subtitle = QLabel(
            "Импорт, хранение и формирование результатов HLA-исследований\n"
            f"{__copyright__}"
        )
        self.lbl_subtitle.setObjectName("subtitle")
        self.lbl_subtitle.setWordWrap(True)

        self.lbl_status = QLabel("Подготовка запуска...")
        self.lbl_status.setObjectName("status")
        self.lbl_status.setWordWrap(True)

        self.lbl_details = QLabel("")
        self.lbl_details.setObjectName("details")
        self.lbl_details.setTextFormat(Qt.RichText)
        self.lbl_details.setWordWrap(True)
        self.lbl_details.setMinimumHeight(110)
        self.lbl_details.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.progress.setTextVisible(True)

        lay.addWidget(self.lbl_title)
        lay.addWidget(self.lbl_subtitle)
        lay.addSpacing(8)
        lay.addWidget(self.lbl_status)
        lay.addWidget(self.lbl_details, 1)
        lay.addWidget(self.progress)

        outer.addWidget(card)

    def showEvent(self, event):
        super().showEvent(event)
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())
        self.raise_()
        self.activateWindow()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = self.rect()

        if not self._background.isNull():
            pix = self._background.scaled(
                rect.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            x = (rect.width() - pix.width()) // 2
            y = (rect.height() - pix.height()) // 2
            painter.drawPixmap(x, y, pix)
        else:
            gradient = QLinearGradient(0, 0, rect.width(), rect.height())
            gradient.setColorAt(0.0, QColor(10, 34, 56))
            gradient.setColorAt(0.5, QColor(14, 55, 78))
            gradient.setColorAt(1.0, QColor(8, 22, 38))
            painter.fillRect(rect, gradient)

        painter.fillRect(rect, QColor(5, 14, 24, 90))

    def append_log(self, text: str, *, level: str = "info") -> None:
        colors = {
            "ok": "#9FE870",
            "warn": "#FFD166",
            "error": "#FF8A8A",
            "info": "#D7E3EA",
        }
        bullets = {
            "ok": "●",
            "warn": "●",
            "error": "●",
            "info": "●",
        }

        color = colors.get(level, colors["info"])
        bullet = bullets.get(level, bullets["info"])

        safe = html.escape(text)
        self._log_lines.append(f'<span style="color:{color}">{bullet}</span> {safe}')
        self._log_lines = self._log_lines[-7:]
        self.lbl_details.setText("<br>".join(self._log_lines))
        QApplication.processEvents()

    def set_step(
        self,
        percent: int,
        status: str,
        detail: str | None = None,
        *,
        level: str = "info",
    ) -> None:
        self.progress.setValue(max(0, min(100, int(percent))))
        self.lbl_status.setText(status)

        if detail:
            self.append_log(detail, level=level)

        QApplication.processEvents()
