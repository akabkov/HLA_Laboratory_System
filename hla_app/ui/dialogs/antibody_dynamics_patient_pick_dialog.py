"""Read-only диалог выбора пациента для окна динамики антител.

Используется только в ситуации, когда по введённым данным найдено несколько
кандидатов в основной PostgreSQL. Диалог ничего не меняет в данных пациента,
а лишь помогает пользователю явно выбрать нужный `patient_code`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from hla_app.services.antibody_dynamics_models import PatientCandidate
from hla_app.utils.validators import format_ddmmyyyy


# --- Простое read-only подтверждение выбранного patient_code ---
class AntibodyDynamicsPatientPickDialog(QDialog):
    def __init__(self, *, candidates: list[PatientCandidate], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор пациента")
        self.resize(860, 380)

        self.selected_candidate: PatientCandidate | None = None
        self._candidates = list(candidates)

        layout = QVBoxLayout(self)

        lbl_info = QLabel(
            "Найдено несколько пациентов, подходящих под введённые данные.\n"
            "Выберите нужного пациента для построения динамики антител:"
        )
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        self.listw = QListWidget()
        layout.addWidget(self.listw, 1)

        for candidate in self._candidates:
            item = QListWidgetItem(self._format_candidate(candidate))
            item.setData(Qt.UserRole, candidate)
            self.listw.addItem(item)

        buttons = QHBoxLayout()
        self.btn_open = QPushButton("Открыть")
        self.btn_cancel = QPushButton("Отмена")

        buttons.addStretch(1)
        buttons.addWidget(self.btn_open)
        buttons.addWidget(self.btn_cancel)
        layout.addLayout(buttons)

        self.btn_open.clicked.connect(self._accept_selected)
        self.btn_cancel.clicked.connect(self.reject)
        self.listw.itemDoubleClicked.connect(lambda _item: self._accept_selected())

        if self.listw.count() > 0:
            self.listw.setCurrentRow(0)

    def _format_candidate(self, candidate: PatientCandidate) -> str:
        birth_text = (
            format_ddmmyyyy(candidate.birth_date)
            if candidate.birth_date is not None
            else "не указана"
        )
        recipient_text = (
            str(candidate.recipient_code)
            if candidate.recipient_code is not None
            else "не указан"
        )
        organ_text = candidate.organ_title or "не указан"

        return (
            f"{candidate.full_name} | "
            f"ДР: {birth_text} | "
            f"Орган: {organ_text} | "
            f"Код реципиента: {recipient_text} | "
            f"patient_code: {candidate.patient_code}"
        )

    def _accept_selected(self) -> None:
        item = self.listw.currentItem()
        if item is None:
            QMessageBox.warning(
                self,
                "Внимание",
                "Выберите пациента из списка.",
            )
            return

        candidate = item.data(Qt.UserRole)
        if candidate is None:
            QMessageBox.warning(
                self,
                "Внимание",
                "Не удалось определить выбранного пациента.",
            )
            return

        self.selected_candidate = candidate
        self.accept()
