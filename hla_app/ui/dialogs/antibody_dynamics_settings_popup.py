"""Popup-настройки дополнительной PostgreSQL для динамики антител.

Это лёгкое немодальное окно, открывающееся от кнопки `⋯` рядом с
`📈 Динамика антител`. Popup позволяет переопределить параметры подключения
к дополнительной БД, но не влияет на обязательную основную БД приложения.
Если дополнительная БД недоступна, окно динамики всё равно должно работать
по основной БД.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hla_app.config.settings import (
    FIRST_CLINIC,
    SECOND_CLINIC,
    get_builtin_opposite_db_name_for_clinic,
    get_opposite_clinic_code,
)
from hla_app.services.app_prefs import (
    DynamicsSecondaryDbPreferences,
    load_effective_app_preferences,
    load_effective_dynamics_secondary_db_preferences,
    load_user_dynamics_secondary_db_preferences,
    save_user_dynamics_secondary_db_preferences,
)
from hla_app.ui.workers.antibody_dynamics_worker import SecondaryDbProbeTask


# --- Лёгкий popup для optional second DB без расширения общих настроек ---
class AntibodyDynamicsSettingsPopup(QFrame):
    settings_saved = Signal()

    def __init__(self, parent_window):
        super().__init__(parent_window.window(), Qt.Popup)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(520)

        self._parent_window = parent_window
        self._thread_pool = QThreadPool(self)
        self._request_seq = 0
        self._active_request_id = 0

        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.setInterval(300)
        self._probe_timer.timeout.connect(self._start_probe)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self.lbl_info_title = QLabel(
            "Настройка дополнительной базы данных для расширения динамики антител"
        )
        self.lbl_info_title.setWordWrap(True)
        self.lbl_info_title.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.addWidget(self.lbl_info_title)

        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lbl_info.setStyleSheet("color: #555555;")
        layout.addWidget(self.lbl_info)

        form = QFormLayout()
        layout.addLayout(form)

        self.ed_db_user = QLineEdit()
        self.ed_db_password = QLineEdit()
        self.ed_db_host = QLineEdit()
        self.ed_db_port = QLineEdit()
        self.ed_db_name = QLineEdit()

        self.ed_db_password.setEchoMode(QLineEdit.Password)
        self.ed_db_port.setValidator(QIntValidator(1, 65535, self.ed_db_port))

        form.addRow("Пользователь:", self._build_resettable_text_row(self.ed_db_user))
        form.addRow("Пароль:", self._build_resettable_text_row(self.ed_db_password))
        form.addRow("Хост:", self._build_resettable_text_row(self.ed_db_host))
        form.addRow("Порт:", self._build_resettable_text_row(self.ed_db_port))
        form.addRow("База данных:", self._build_resettable_text_row(self.ed_db_name))

        buttons = QHBoxLayout()
        self.btn_save = QPushButton("Сохранить")
        self.btn_reset = QPushButton("Сброс")

        buttons.addStretch(1)
        buttons.addWidget(self.btn_reset)
        buttons.addWidget(self.btn_save)

        self.lbl_status = QLabel()
        self.lbl_status.setTextFormat(Qt.RichText)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lbl_status.setStyleSheet("color: #555555;")
        layout.addWidget(self.lbl_status)
        layout.addLayout(buttons)

        self.btn_save.clicked.connect(self._save)
        self.btn_reset.clicked.connect(self._reset)

        for edit in (
            self.ed_db_user,
            self.ed_db_password,
            self.ed_db_host,
            self.ed_db_port,
            self.ed_db_name,
        ):
            edit.textChanged.connect(self._schedule_probe)
            edit.returnPressed.connect(edit.clearFocus)

        self._load_initial_values()
        self._refresh_info_label()
        self._refresh_placeholders()

        QTimer.singleShot(0, self._start_probe)

    def _clinic_display_name(self, clinic_code: str | None) -> str:
        if clinic_code == "f_clinic":
            return FIRST_CLINIC
        if clinic_code == "s_clinic":
            return SECOND_CLINIC
        return clinic_code or "не указано"

    def _password_placeholder(self, password: str | None) -> str:
        pwd = "" if password is None else str(password)
        return f"По умолчанию: {'●' * len(pwd)}"

    def _set_probe_status(self, ok: bool) -> None:
        if ok:
            self.lbl_status.setText(
                "<span style='color: #2e7d32;'>●</span> Подключение установлено"
            )
        else:
            self.lbl_status.setText(
                "<span style='color: #c62828;'>●</span> Подключение отсутствует"
            )

    def _refresh_info_label(self) -> None:
        prefs = load_effective_app_preferences()
        opposite_clinic_name = self._clinic_display_name(
            get_opposite_clinic_code(prefs.clinic)
        )

        self.lbl_info.setText(
            f"По умолчанию используется база данных {opposite_clinic_name}. "
            "Эта база опциональна. Если подключение к ней отсутствует, "
            "графики будут строиться по основной базе приложения."
        )

    def _refresh_placeholders(self) -> None:
        primary = load_effective_app_preferences()
        default_secondary_db_name = get_builtin_opposite_db_name_for_clinic(
            primary.clinic
        )

        self.ed_db_user.setPlaceholderText(f"По умолчанию: {primary.db_user}")
        self.ed_db_password.setPlaceholderText(
            self._password_placeholder(primary.db_password)
        )
        self.ed_db_host.setPlaceholderText(f"По умолчанию: {primary.db_host}")
        self.ed_db_port.setPlaceholderText(f"По умолчанию: {primary.db_port}")
        self.ed_db_name.setPlaceholderText(f"По умолчанию: {default_secondary_db_name}")

    def _load_initial_values(self) -> None:
        user = load_user_dynamics_secondary_db_preferences()

        self.ed_db_user.setText(user.db_user or "")
        self.ed_db_password.setText(user.db_password or "")
        self.ed_db_host.setText(user.db_host or "")
        self.ed_db_port.setText(str(user.db_port) if user.db_port is not None else "")
        self.ed_db_name.setText(user.db_name or "")

    def _build_resettable_text_row(self, edit: QLineEdit) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        btn_clear = QPushButton("Сброс")
        btn_clear.setAutoDefault(False)
        btn_clear.setDefault(False)
        btn_clear.clicked.connect(edit.clear)

        layout.addWidget(edit, 1)
        layout.addWidget(btn_clear)

        return row

    def _normalize_optional_text(self, value: str) -> str | None:
        text = (value or "").strip()
        return text if text else None

    def _normalize_optional_port(self, value: str) -> int | None:
        text = (value or "").strip()
        if not text:
            return None

        try:
            return int(text)
        except Exception:
            return None

    def _collect_user_values(self) -> dict:
        return {
            "db_user": self._normalize_optional_text(self.ed_db_user.text()),
            "db_password": self._normalize_optional_text(self.ed_db_password.text()),
            "db_host": self._normalize_optional_text(self.ed_db_host.text()),
            "db_port": self._normalize_optional_port(self.ed_db_port.text()),
            "db_name": self._normalize_optional_text(self.ed_db_name.text()),
        }

    def _build_effective_config_from_inputs(self) -> DynamicsSecondaryDbPreferences:
        primary = load_effective_app_preferences()
        values = self._collect_user_values()

        # Пустые поля в popup означают "используй effective default", а не
        # "очисти параметр". Для secondary это обычно credentials primary DB и
        # стандартное имя БД противоположной клиники.
        return DynamicsSecondaryDbPreferences(
            db_user=values["db_user"]
            if values["db_user"] is not None
            else primary.db_user,
            db_password=(
                values["db_password"]
                if values["db_password"] is not None
                else primary.db_password
            ),
            db_host=values["db_host"]
            if values["db_host"] is not None
            else primary.db_host,
            db_port=values["db_port"]
            if values["db_port"] is not None
            else primary.db_port,
            db_name=(
                values["db_name"]
                if values["db_name"] is not None
                else get_builtin_opposite_db_name_for_clinic(primary.clinic)
            ),
        )

    def _schedule_probe(self) -> None:
        self._probe_timer.start()

    def _start_probe(self) -> None:
        port_raw = self.ed_db_port.text().strip()
        port_value = self._normalize_optional_port(port_raw)

        if port_raw and port_value is None:
            self._set_probe_status(False)
            return

        config = self._build_effective_config_from_inputs()

        if (
            config.db_user is None
            or config.db_password is None
            or config.db_host is None
            or config.db_port is None
            or config.db_name is None
        ):
            self._set_probe_status(False)
            return

        self._request_seq += 1
        self._active_request_id = self._request_seq

        task = SecondaryDbProbeTask(
            request_id=self._active_request_id,
            config=config,
        )
        task.signals.finished.connect(self._on_probe_finished)
        self._thread_pool.start(task)

    def _on_probe_finished(self, request_id: int, ok: bool) -> None:
        if request_id != self._active_request_id:
            return

        self._set_probe_status(ok)

    def _reset(self) -> None:
        self.ed_db_user.clear()
        self.ed_db_password.clear()
        self.ed_db_host.clear()
        self.ed_db_port.clear()
        self.ed_db_name.clear()
        self._schedule_probe()

    def _save(self) -> None:
        port_raw = self.ed_db_port.text().strip()
        port_value = self._normalize_optional_port(port_raw)

        if port_raw and port_value is None:
            QMessageBox.warning(
                self,
                "Некорректный порт",
                "Поле «Порт» должно содержать целое число.",
            )
            return

        if port_value is not None and not (1 <= port_value <= 65535):
            QMessageBox.warning(
                self,
                "Некорректный порт",
                "Поле «Порт» должно быть в диапазоне 1–65535.",
            )
            return

        old_effective = load_effective_dynamics_secondary_db_preferences()
        new_effective = self._build_effective_config_from_inputs()

        if old_effective != new_effective:
            if not self._parent_window._ask_password(
                title="Подтверждение изменения настроек",
                prompt="🔒 Введите пароль:",
            ):
                return

        values = self._collect_user_values()
        save_user_dynamics_secondary_db_preferences(
            db_user=values["db_user"],
            db_password=values["db_password"],
            db_host=values["db_host"],
            db_port=values["db_port"],
            db_name=values["db_name"],
        )

        self._refresh_info_label()
        self._refresh_placeholders()
        self.settings_saved.emit()
        self._start_probe()
