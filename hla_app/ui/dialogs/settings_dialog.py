from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from hla_app.config.settings import (
    APP_PASSWORD,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    DEFAULT_CONCLUSION_PLACE,
    DEFAULT_CONCLUSION_SAVE_DIR,
    DEFAULT_DIALOG_DIR,
    DEFAULT_SUM_SAVE_DIR,
    LIMIT_ROOT_EXPLORER_TO_ORGANS,
    ROOT_DIR,
)
from hla_app.db.engine import probe_db_settings
from hla_app.services.app_prefs import (
    load_effective_app_preferences,
    load_user_app_preferences,
    save_user_app_preferences,
)
from hla_app.services.root_dir_service import probe_root_dir_settings


class AppSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")

        user_prefs = load_user_app_preferences()
        effective_prefs = load_effective_app_preferences()
        self._initial_effective_root_dir = effective_prefs.root_dir
        self._initial_effective_db_config = (
            effective_prefs.db_user,
            effective_prefs.db_password,
            effective_prefs.db_host,
            effective_prefs.db_port,
            effective_prefs.db_name,
        )

        layout = QVBoxLayout(self)

        # ---------- Блок 1. Файловая база ----------
        gb_root = QGroupBox("Настройка подключения к файловой базе")
        root_layout = QVBoxLayout(gb_root)

        root_info = QLabel(
            "При изменении пути к файловой базе потребуется ввод пароля.\n"
        )
        root_info.setWordWrap(True)
        root_info.setStyleSheet("color: #444444;")
        root_layout.addWidget(root_info)

        root_form = QFormLayout()
        root_layout.addLayout(root_form)

        self.ed_root_dir = QLineEdit(
            str(user_prefs.root_dir) if user_prefs.root_dir else ""
        )
        self.ed_root_dir.setPlaceholderText(self._placeholder_root_dir())

        root_form.addRow(
            "Путь к файловой базе:",
            self._build_path_row(
                self.ed_root_dir,
                "Выберите папку файловой базы",
            ),
        )

        layout.addWidget(gb_root)

        # ---------- Блок 2. PostgreSQL ----------
        gb_db = QGroupBox("Настройка подключения к базе PostgreSQL")
        db_layout = QVBoxLayout(gb_db)

        db_info = QLabel(
            "Если оставить поле пустым, будет использовано значение по умолчанию. "
            "При изменении настроек подключения потребуется ввод пароля приложения. "
            "Новые параметры подключения будут использоваться сразу после сохранения.\n"
        )
        db_info.setWordWrap(True)
        db_info.setStyleSheet("color: #444444;")
        db_layout.addWidget(db_info)

        db_form = QFormLayout()
        db_layout.addLayout(db_form)

        self.ed_db_user = QLineEdit(
            str(user_prefs.db_user) if user_prefs.db_user else ""
        )
        self.ed_db_password = QLineEdit(
            str(user_prefs.db_password) if user_prefs.db_password else ""
        )
        self.ed_db_host = QLineEdit(
            str(user_prefs.db_host) if user_prefs.db_host else ""
        )
        self.ed_db_port = QLineEdit(
            str(user_prefs.db_port) if user_prefs.db_port is not None else ""
        )
        self.ed_db_name = QLineEdit(
            str(user_prefs.db_name) if user_prefs.db_name else ""
        )

        self.ed_db_password.setEchoMode(QLineEdit.Password)
        self.ed_db_port.setValidator(QIntValidator(1, 65535, self.ed_db_port))

        self.ed_db_user.setPlaceholderText(self._placeholder_db_user())
        self.ed_db_password.setPlaceholderText(self._placeholder_db_password())
        self.ed_db_host.setPlaceholderText(self._placeholder_db_host())
        self.ed_db_port.setPlaceholderText(self._placeholder_db_port())
        self.ed_db_name.setPlaceholderText(self._placeholder_db_name())

        db_form.addRow(
            "Пользователь:",
            self._build_resettable_text_row(self.ed_db_user),
        )
        db_form.addRow(
            "Пароль:",
            self._build_resettable_text_row(self.ed_db_password),
        )
        db_form.addRow(
            "Хост:",
            self._build_resettable_text_row(self.ed_db_host),
        )
        db_form.addRow(
            "Порт:",
            self._build_resettable_text_row(self.ed_db_port),
        )
        db_form.addRow(
            "База данных:",
            self._build_resettable_text_row(self.ed_db_name),
        )

        layout.addWidget(gb_db)

        # ---------- Блок 3. Пользовательские папки ----------
        gb_paths = QGroupBox("Настройка пользовательских папок")
        paths_layout = QVBoxLayout(gb_paths)

        dir_info = QLabel(
            "Если оставить поле пустым, будет использовано значение по умолчанию.\n"
        )
        dir_info.setWordWrap(True)
        dir_info.setStyleSheet("color: #444444;")
        paths_layout.addWidget(dir_info)

        form = QFormLayout()
        paths_layout.addLayout(form)

        self.ed_dialog_dir = QLineEdit(
            str(user_prefs.dialog_dir) if user_prefs.dialog_dir else ""
        )
        self.ed_jpg_dir = QLineEdit(
            str(user_prefs.jpg_dialog_dir) if user_prefs.jpg_dialog_dir else ""
        )
        self.ed_conclusion_dir = QLineEdit(
            str(user_prefs.conclusion_save_dir)
            if user_prefs.conclusion_save_dir
            else ""
        )
        self.ed_sum_dir = QLineEdit(
            str(user_prefs.sum_save_dir) if user_prefs.sum_save_dir else ""
        )

        self.ed_dialog_dir.setPlaceholderText(self._placeholder_dialog_dir())
        self.ed_jpg_dir.setPlaceholderText(self._placeholder_jpg_dir())
        self.ed_conclusion_dir.setPlaceholderText(self._placeholder_conclusion_dir())
        self.ed_sum_dir.setPlaceholderText(self._placeholder_sum_dir())

        form.addRow(
            "Папка выбора файлов:",
            self._build_path_row(
                self.ed_dialog_dir,
                "Выберите папку для открытия файлов",
            ),
        )
        form.addRow(
            "Папка выбора JPG заключения:",
            self._build_path_row(
                self.ed_jpg_dir,
                "Выберите папку для открытия JPG заключения",
            ),
        )
        form.addRow(
            "Папка сохранения заключений:",
            self._build_path_row(
                self.ed_conclusion_dir,
                "Выберите папку для сохранения заключений",
            ),
        )
        form.addRow(
            "Папка сохранения титра антител:",
            self._build_path_row(
                self.ed_sum_dir,
                "Выберите папку для сохранения вычисленного титра антител",
            ),
        )

        layout.addWidget(gb_paths)

        # ---------- Блок 4. Учреждение по умолчанию ----------
        gb_place = QGroupBox("Учреждение по умолчанию для заключения")
        place_layout = QVBoxLayout(gb_place)

        place_info = QLabel(
            "Этот выбор будет использоваться при открытии приложения "
            "как значение по умолчанию в блоке «Сформировать заключение».\n"
        )
        place_info.setWordWrap(True)
        place_info.setStyleSheet("color: #444444;")
        place_layout.addWidget(place_info)

        place_layout.addStretch(1)

        place_form = QFormLayout()

        self.cb_conclusion_place = QComboBox()
        self.cb_conclusion_place.addItem("ГУ «МНПЦ ХТиГ»", "mnpc")
        self.cb_conclusion_place.addItem("ГУ «РНПЦ ТиМБ»", "rnpc")

        idx = self.cb_conclusion_place.findData(effective_prefs.conclusion_place)
        if idx < 0:
            idx = 0
        self.cb_conclusion_place.setCurrentIndex(idx)

        place_form.addRow("Учреждение:", self.cb_conclusion_place)
        place_layout.addLayout(place_form)

        # ---------- Блок 5. Проводник ----------
        gb_explorer = QGroupBox("Настройка проводника")
        explorer_layout = QVBoxLayout(gb_explorer)

        explorer_info = QLabel(
            "По умолчанию в корне боковой панели проводника показываются только папки из списка ОРГАНОВ. "
            "Если включить опцию, проводник будет показывать всё содержимое корня ФАЙЛОВОЙ БАЗЫ.\n"
        )
        explorer_info.setWordWrap(True)
        explorer_info.setStyleSheet("color: #444444;")
        explorer_layout.addWidget(explorer_info)

        explorer_layout.addStretch(1)

        self.chk_show_all_root_explorer_content = QCheckBox(
            "Показывать всё содержимое корня файловой базы"
        )
        self.chk_show_all_root_explorer_content.setChecked(
            not bool(effective_prefs.limit_root_explorer_to_organs)
        )

        checkbox_row = QHBoxLayout()
        checkbox_row.setContentsMargins(0, 0, 0, 0)
        checkbox_row.addStretch(1)
        checkbox_row.addWidget(self.chk_show_all_root_explorer_content)
        checkbox_row.addStretch(1)

        explorer_layout.addLayout(checkbox_row)

        place_and_explorer_row = QHBoxLayout()
        place_and_explorer_row.setContentsMargins(0, 0, 0, 0)
        place_and_explorer_row.setSpacing(6)
        place_and_explorer_row.addWidget(gb_place)
        place_and_explorer_row.addWidget(gb_explorer)
        place_and_explorer_row.setStretch(0, 1)
        place_and_explorer_row.setStretch(1, 1)

        layout.addLayout(place_and_explorer_row)

        # ---------- Кнопки ----------
        buttons = QHBoxLayout()

        btn_reset = self._tune_dialog_button(QPushButton("Настроить по умолчанию"))
        btn_save = self._tune_dialog_button(QPushButton("Сохранить"))
        btn_cancel = self._tune_dialog_button(QPushButton("Отмена"))

        buttons.addWidget(btn_reset)
        buttons.addStretch(1)
        buttons.addWidget(btn_save)
        buttons.addWidget(btn_cancel)

        layout.addLayout(buttons)

        for edit in (
            self.ed_root_dir,
            self.ed_db_user,
            self.ed_db_password,
            self.ed_db_host,
            self.ed_db_port,
            self.ed_db_name,
            self.ed_dialog_dir,
            self.ed_jpg_dir,
            self.ed_conclusion_dir,
            self.ed_sum_dir,
        ):
            self._bind_line_edit_confirm(edit)

        btn_reset.clicked.connect(self._reset_all_settings)
        btn_save.clicked.connect(self._save)
        btn_cancel.clicked.connect(self.reject)

        self.adjustSize()
        size = self.sizeHint().expandedTo(QSize(640, 0))
        self.setFixedSize(size)

        QTimer.singleShot(0, self._clear_initial_focus)

    def _placeholder_root_dir(self) -> str:
        return f"По умолчанию: {ROOT_DIR}"

    def _placeholder_db_user(self) -> str:
        return f"По умолчанию: {DB_USER}"

    def _placeholder_db_password(self) -> str:
        password = "" if DB_PASSWORD is None else str(DB_PASSWORD)
        return f"По умолчанию: {'●' * len(password)}"

    def _placeholder_db_host(self) -> str:
        return f"По умолчанию: {DB_HOST}"

    def _placeholder_db_port(self) -> str:
        return f"По умолчанию: {DB_PORT}"

    def _placeholder_db_name(self) -> str:
        return f"По умолчанию: {DB_NAME}"

    def _placeholder_dialog_dir(self) -> str:
        if DEFAULT_DIALOG_DIR is None:
            return "По умолчанию: Рабочий стол"
        return f"По умолчанию: {DEFAULT_DIALOG_DIR}"

    def _placeholder_jpg_dir(self) -> str:
        if DEFAULT_DIALOG_DIR is None:
            return "По умолчанию: Рабочий стол"
        return f"По умолчанию: {DEFAULT_DIALOG_DIR}"

    def _placeholder_conclusion_dir(self) -> str:
        if DEFAULT_CONCLUSION_SAVE_DIR is None:
            return "По умолчанию: Рабочий стол"
        return f"По умолчанию: {DEFAULT_CONCLUSION_SAVE_DIR}"

    def _placeholder_sum_dir(self) -> str:
        if DEFAULT_SUM_SAVE_DIR is None:
            return "По умолчанию: рядом с исходным CSV"
        return f"По умолчанию: {DEFAULT_SUM_SAVE_DIR}"

    def _tune_dialog_button(self, button: QPushButton) -> QPushButton:
        button.setAutoDefault(False)
        button.setDefault(False)
        return button

    def _bind_line_edit_confirm(self, edit: QLineEdit) -> None:
        edit.returnPressed.connect(edit.clearFocus)

    def _clear_initial_focus(self) -> None:
        widget = self.focusWidget()
        if widget is not None:
            widget.clearFocus()
        self.setFocus(Qt.OtherFocusReason)

    def _build_path_row(self, edit: QLineEdit, title: str) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        btn_browse = self._tune_dialog_button(QPushButton("Обзор..."))
        btn_clear = self._tune_dialog_button(QPushButton("Сброс"))

        btn_browse.clicked.connect(lambda: self._browse_dir(edit, title))
        btn_clear.clicked.connect(edit.clear)

        lay.addWidget(edit, 1)
        lay.addWidget(btn_browse)
        lay.addWidget(btn_clear)

        return row

    def _build_resettable_text_row(self, edit: QLineEdit) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        btn_clear = self._tune_dialog_button(QPushButton("Сброс"))
        btn_clear.clicked.connect(edit.clear)

        lay.addWidget(edit, 1)
        lay.addWidget(btn_clear)

        return row

    def _browse_dir(self, edit: QLineEdit, title: str) -> None:
        start_dir = edit.text().strip()
        if not start_dir:
            start_dir = str(Path.home())

        selected = QFileDialog.getExistingDirectory(self, title, start_dir)
        if selected:
            edit.setText(selected)

    def _normalize_optional_dir(self, value: str) -> Path | None:
        text = (value or "").strip()
        if not text:
            return None
        return Path(text).expanduser()

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

    def _validate_dir(
        self,
        path: Path | None,
        label: str,
        *,
        must_exist: bool,
    ) -> bool:
        if path is None:
            return True

        if path.exists():
            if path.is_dir():
                return True

            QMessageBox.warning(
                self,
                "Некорректная папка",
                f"Поле «{label}» должно содержать путь к папке, а не к файлу.",
            )
            return False

        if not must_exist:
            return True

        QMessageBox.warning(
            self,
            "Некорректная папка",
            f"Поле «{label}» должно содержать существующую папку\nили быть пустым.",
        )
        return False

    def _ask_password_for_root_dir_change(self) -> bool:
        parent = self.parent()
        ask_password = getattr(parent, "_ask_password", None)

        if callable(ask_password):
            return ask_password(
                title="Подтверждение изменения настроек",
                prompt="Введите пароль:",
            )

        while True:
            password, ok = QInputDialog.getText(
                self,
                "Подтверждение",
                "Введите пароль:",
                QLineEdit.Password,
            )

            if not ok:
                return False

            if password == APP_PASSWORD:
                return True

            QMessageBox.warning(self, "Внимание", "Неверный пароль.")

    def _reset_all_settings(self) -> None:
        answer = QMessageBox.question(
            self,
            "Сброс настроек",
            "Сбросить все параметры к значениям по умолчанию?\n\n"
            "Изменения будут применены только после нажатия кнопки «Сохранить».",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return

        self.ed_root_dir.clear()

        self.ed_db_user.clear()
        self.ed_db_password.clear()
        self.ed_db_host.clear()
        self.ed_db_port.clear()
        self.ed_db_name.clear()

        self.ed_dialog_dir.clear()
        self.ed_jpg_dir.clear()
        self.ed_conclusion_dir.clear()
        self.ed_sum_dir.clear()

        idx = self.cb_conclusion_place.findData(DEFAULT_CONCLUSION_PLACE)
        if idx < 0:
            idx = 0
        self.cb_conclusion_place.setCurrentIndex(idx)

        self.chk_show_all_root_explorer_content.setChecked(
            not bool(LIMIT_ROOT_EXPLORER_TO_ORGANS)
        )

    def _save(self) -> None:
        root_dir = self._normalize_optional_dir(self.ed_root_dir.text())
        dialog_dir = self._normalize_optional_dir(self.ed_dialog_dir.text())
        jpg_dir = self._normalize_optional_dir(self.ed_jpg_dir.text())
        conclusion_dir = self._normalize_optional_dir(self.ed_conclusion_dir.text())
        sum_dir = self._normalize_optional_dir(self.ed_sum_dir.text())
        conclusion_place = self.cb_conclusion_place.currentData()
        limit_root_explorer_to_organs = (
            not self.chk_show_all_root_explorer_content.isChecked()
        )

        db_user = self._normalize_optional_text(self.ed_db_user.text())
        db_password = self._normalize_optional_text(self.ed_db_password.text())
        db_host = self._normalize_optional_text(self.ed_db_host.text())
        db_port_raw = self.ed_db_port.text().strip()
        db_port = self._normalize_optional_port(db_port_raw)
        db_name = self._normalize_optional_text(self.ed_db_name.text())

        if not self._validate_dir(
            root_dir,
            "Путь к файловой базе",
            must_exist=True,
        ):
            return

        if not self._validate_dir(
            dialog_dir,
            "Папка выбора файлов",
            must_exist=True,
        ):
            return

        if not self._validate_dir(
            jpg_dir,
            "Папка выбора JPG заключения",
            must_exist=True,
        ):
            return

        if not self._validate_dir(
            conclusion_dir,
            "Папка сохранения заключений",
            must_exist=False,
        ):
            return

        if not self._validate_dir(
            sum_dir,
            "Папка для суммарного титра",
            must_exist=False,
        ):
            return

        if db_port_raw and db_port is None:
            QMessageBox.warning(
                self,
                "Некорректный порт",
                "Поле «Порт» должно содержать целое число.",
            )
            return

        if db_port is not None and not (1 <= db_port <= 65535):
            QMessageBox.warning(
                self,
                "Некорректный порт",
                "Поле «Порт» должно быть в диапазоне 1–65535.",
            )
            return

        new_effective_root_dir = root_dir if root_dir is not None else ROOT_DIR

        new_effective_db_config = (
            db_user if db_user is not None else DB_USER,
            db_password if db_password is not None else DB_PASSWORD,
            db_host if db_host is not None else DB_HOST,
            db_port if db_port is not None else DB_PORT,
            db_name if db_name is not None else DB_NAME,
        )

        new_effective_db_user = db_user if db_user is not None else DB_USER
        new_effective_db_password = (
            db_password if db_password is not None else DB_PASSWORD
        )
        new_effective_db_host = db_host if db_host is not None else DB_HOST
        new_effective_db_port = db_port if db_port is not None else DB_PORT
        new_effective_db_name = db_name if db_name is not None else DB_NAME

        db_changed = new_effective_db_config != self._initial_effective_db_config
        root_changed = new_effective_root_dir != self._initial_effective_root_dir

        if root_changed or db_changed:
            if not self._ask_password_for_root_dir_change():
                return

        if root_changed:
            try:
                probe_root_dir_settings(root_dir=new_effective_root_dir)
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Ошибка подключения к файловой базе",
                    "Не удалось подключиться к файловой базе по указанному пути.\n\n"
                    "Настройки пути к файловой базе НЕ будут сохранены.\n\n"
                    f"{e}",
                )
                return

        if db_changed:
            try:
                probe_db_settings(
                    db_user=new_effective_db_user,
                    db_password=new_effective_db_password,
                    db_host=new_effective_db_host,
                    db_port=new_effective_db_port,
                    db_name=new_effective_db_name,
                )
            except Exception as e:
                QMessageBox.critical(
                    self,
                    "Ошибка подключения к PostgreSQL",
                    "Не удалось подключиться к базе данных с указанными параметрами.\n\n"
                    "Настройки подключения НЕ будут сохранены.\n\n"
                    f"{e}",
                )
                return

        save_user_app_preferences(
            root_dir=root_dir,
            dialog_dir=dialog_dir,
            jpg_dialog_dir=jpg_dir,
            conclusion_save_dir=conclusion_dir,
            sum_save_dir=sum_dir,
            conclusion_place=conclusion_place,
            limit_root_explorer_to_organs=limit_root_explorer_to_organs,
            db_user=db_user,
            db_password=db_password,
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
        )

        self.accept()
