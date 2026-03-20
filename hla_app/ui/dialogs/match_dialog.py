from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class MatchDialogAny(QDialog):
    """
    Единый диалог совпадений:
    - новый формат: совпадение по ФИО-части (без учёта ДР)
    - старый формат: совпадение по фамилии(ям) + инициалам

    Можно:
    - использовать выбранную папку
    - (опционально) переименовать выбранную папку в desired_name
    - создать нового пациента
    """

    def __init__(
        self, *, items: list[tuple[str, str, bool]], desired_name: str, parent=None
    ):
        super().__init__(parent)
        self.setWindowTitle("Найдены совпадения")

        # ("use", folder_name, do_rename) или ("new", None, False)
        self.choice: tuple[str, str | None, bool] | None = None

        # Для старых папок предупреждение при первой попытке снять галочку
        # показываем только один раз на каждую выбранную папку в рамках диалога.
        self._legacy_uncheck_acknowledged: set[str] = set()
        self._rename_checkbox_guard = False

        v = QVBoxLayout(self)
        lbl_info = QLabel(
            "Найдены совпадения:\n"
            "• НОВЫЙ формат — совпадение по полному ФИО-блоку; "
            "при пустом отчестве также возможны совпадения по фамилии(ям) + имени + дате рождения; "
            "дополнительно могут показываться совпадения по фамилии(ям) + инициалам\n"
            "• СТАРЫЙ формат — совпадение по фамилии(ям) + первому инициалу\n\n"
            "Выберите папку или создайте нового пациента:"
        )
        lbl_info.setWordWrap(True)
        v.addWidget(lbl_info)

        self.listw = QListWidget()
        for label, folder_name, is_new_format in items:
            it = QListWidgetItem(label, self.listw)
            it.setData(Qt.UserRole, (folder_name, is_new_format))
        v.addWidget(self.listw)

        self.chk_rename = QCheckBox(
            "Переименовать выбранную папку (включая дату рождения)"
        )
        v.addWidget(self.chk_rename)

        lbl_target = QLabel(f"Целевое имя:\n{desired_name}")
        lbl_target.setWordWrap(True)
        v.addWidget(lbl_target)

        h = QHBoxLayout()
        btn_use = QPushButton("Использовать выбранную")
        btn_new = QPushButton("Создать нового пациента")
        btn_cancel = QPushButton("Отмена")

        h.addWidget(btn_use)
        h.addWidget(btn_new)
        h.addWidget(btn_cancel)
        v.addLayout(h)

        self.listw.currentItemChanged.connect(self._sync_rename_default)
        self.chk_rename.toggled.connect(self._on_chk_rename_toggled)

        if self.listw.count() > 0:
            self.listw.setCurrentRow(0)
            self._sync_rename_default()

        btn_use.clicked.connect(self._use)
        btn_new.clicked.connect(self._new)
        btn_cancel.clicked.connect(self.reject)

    def _set_rename_checked_silently(self, checked: bool) -> None:
        self._rename_checkbox_guard = True
        try:
            self.chk_rename.setChecked(checked)
        finally:
            self._rename_checkbox_guard = False

    def _current_folder_info(self) -> tuple[str | None, bool | None]:
        item = self.listw.currentItem()
        if not item:
            return None, None

        folder_name, is_new_format = item.data(Qt.UserRole)
        return folder_name, is_new_format

    def _sync_rename_default(self, current=None, _previous=None):
        item = current if current is not None else self.listw.currentItem()
        if not item:
            self._set_rename_checked_silently(True)
            return

        _folder_name, is_new_format = item.data(Qt.UserRole)

        # Старый формат -> по умолчанию True
        # Новый формат -> по умолчанию False
        self._set_rename_checked_silently(not is_new_format)

    def _on_chk_rename_toggled(self, checked: bool) -> None:
        if self._rename_checkbox_guard:
            return

        # Интересует только попытка СНЯТЬ галочку
        if checked:
            return

        folder_name, is_new_format = self._current_folder_info()
        if not folder_name:
            return

        # Для нового формата предупреждение не нужно
        if is_new_format:
            return

        # Для конкретной старой папки предупреждаем только один раз.
        # После OK повторная попытка снять галочку уже разрешается.
        if folder_name in self._legacy_uncheck_acknowledged:
            return

        QMessageBox.warning(
            self,
            "Рекомендуется переименование папки",
            "Вы выбрали папку СТАРОГО формата.\n\n"
            "Для поддержания базы данных в валидном состоянии "
            "желательно переименовать такую папку в НОВЫЙ формат "
            "(с датой рождения в имени), если нет явных причин этого не делать.\n\n"
            "После закрытия этого сообщения вы сможете повторно снять галочку, "
            "если всё же хотите использовать папку без переименования.",
            QMessageBox.Ok,
        )

        self._legacy_uncheck_acknowledged.add(folder_name)
        self._set_rename_checked_silently(True)

    def _use(self):
        item = self.listw.currentItem()
        if not item:
            QMessageBox.warning(self, "Внимание", "Выберите папку из списка")
            return
        folder_name, _is_new_format = item.data(Qt.UserRole)
        self.choice = ("use", folder_name, self.chk_rename.isChecked())
        self.accept()

    def _new(self):
        self.choice = ("new", None, False)
        self.accept()
