from __future__ import annotations

import gc
import os
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QEventLoop,
    QModelIndex,
    QRegularExpression,
    QSize,
    QStandardPaths,
    QStringListModel,
    Qt,
    QThreadPool,
    QTimer,
)
from PySide6.QtGui import QIcon, QIntValidator, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from hla_app.__about__ import (
    __author__,
    __copyright__,
    __email__,
    __license__,
    __title__,
    __version__,
)
from hla_app.config.conclusion_staff import (
    M_ACTING_HEAD,
    M_BIOLOGISTS,
    M_DOCTOR_DEFAULT,
    M_DOCTORS,
    M_HEAD_DEFAULT,
    M_HEAD_OF_ITEMS,
    R_ACTING_HEAD,
    R_BIOLOGISTS,
    R_DOCTORS,
    R_HEAD_DEFAULT,
    R_HEAD_OF_ITEMS,
)
from hla_app.config.managed_files import (
    CONCLUSION_FILE_NAMES,
    NEGATIVE_CONCLUSION_FILE_NAME,
    POSITIVE_CONCLUSION_FILE_NAME,
    class_result_file_name,
)
from hla_app.config.settings import (
    APP_PASSWORD,
    ORGANS,
    get_date_min_birth,
    get_date_min_test,
)
from hla_app.data.luminex_parser import parse_luminex_csv
from hla_app.db.engine import probe_db_settings
from hla_app.services.app_prefs import (
    load_effective_app_preferences,
    save_explorer_visibility_preference,
)
from hla_app.services.avg_service import build_avg_excels
from hla_app.services.conclusion_service import normalize_staff_name
from hla_app.services.conclusion_workflow import (
    build_conclusion_payload,
    save_conclusion_docx,
)
from hla_app.services.file_tree_index import FileTreeIndexService
from hla_app.services.import_conflicts import find_import_conflicts
from hla_app.services.import_service import (
    ImportInput,
    PatientData,
    delete_entire_study,
    delete_existing_results,
    do_import,
    rename_existing_patient_record,
)
from hla_app.services.import_ui_models import ImportFormState, ResolvedPatientTarget
from hla_app.services.patient_folder_service import (
    build_db_patient_code,
    build_patient_folder_search,
    create_new_patient_folder,
)
from hla_app.services.root_dir_service import probe_root_dir_settings
from hla_app.services.study_state_service import (
    capture_study_state_snapshot,
    cleanup_study_state_snapshot,
    restore_study_state_snapshot,
)
from hla_app.storage.fs_ops import (
    delete_dir_tree as fs_delete_dir_tree,
    delete_conclusion_jpg,
    get_base_tree_paths,
    rename_patient_folder,
    rename_source_files_for_patient,
    save_conclusion_jpg,
    split_patient_folder_name,
)
from hla_app.ui.dialogs.match_dialog import MatchDialogAny
from hla_app.ui.dialogs.settings_dialog import AppSettingsDialog
from hla_app.ui.widgets.date_input import DateInput
from hla_app.ui.widgets.root_explorer import (
    EXPLORER_AVG_WIDTH,
    EXPLORER_MAX_WIDTH,
    EXPLORER_MIN_WIDTH,
    RootExplorerWidget,
)
from hla_app.ui.workers.file_index_worker import FileTreeIndexBuildTask
from hla_app.ui.workers.patient_autocomplete_worker import PatientAutocompleteTask
from hla_app.utils.validators import (
    cap_hyphenated_lastname,
    cap_ru,
    format_ddmmyyyy,
    is_valid_ru_lastname,
    is_valid_ru_name,
    normalize_for_compare,
)

# Параметры размера окна приложения
_INITIAL_WINDOW_WIDTH_FACTOR = 1.30
_INITIAL_WINDOW_EXTRA_WIDTH = 30
_INITIAL_WINDOW_EXTRA_HEIGHT = 30
_STATUS_LABEL_MIN_HEIGHT = 30
_STATUS_LABEL_MAX_HEIGHT = 60
_FILE_INDEX_BUILD_WAIT_TIMEOUT_SECONDS = 45.0
_PATIENT_AUTOCOMPLETE_WAIT_TIMEOUT_MS = 15000


class PatientFolderCompleter(QCompleter):
    """
    Не подставляет выбранное полное имя папки прямо в QLineEdit.
    Мы используем popup только как список выбора.
    """

    def pathFromIndex(self, index):
        widget = self.widget()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return super().pathFromIndex(index)


class MainSplitterHandle(QSplitterHandle):
    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            splitter = self.splitter()
            if isinstance(splitter, MainSplitter):
                splitter.reset_left_panel_width()
            event.accept()
            return

        super().mouseDoubleClickEvent(event)


class MainSplitter(QSplitter):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._default_left_width = EXPLORER_AVG_WIDTH

    def set_default_left_width(self, width: int) -> None:
        self._default_left_width = max(0, int(width))

    def reset_left_panel_width(self) -> None:
        sizes = self.sizes()
        if len(sizes) < 2:
            return

        total_width = sum(sizes)
        left_width = min(self._default_left_width, max(total_width - 1, 0))
        right_width = max(total_width - left_width, 1)

        self.setSizes([left_width, right_width])

    def createHandle(self) -> QSplitterHandle:
        return MainSplitterHandle(self.orientation(), self)


class MainWindow(QMainWindow):
    def __init__(self, *, root_dir_available: bool = True):
        super().__init__()
        self._root_dir_available = bool(root_dir_available)

        self._file_index_service = self._create_file_index_service()
        self._file_index_thread_pool = QThreadPool(self)
        self._file_index_build_in_progress = False
        self._file_index_build_queued = False
        self._file_index_rebuild_deferred_by_import = False

        self._center_on_screen_pending = True
        icon_path = Path(__file__).resolve().parents[2] / "assets" / "app.ico"
        self.setWindowTitle(f"{__title__} {__version__} — by {__author__}")
        self.setWindowIcon(QIcon(str(icon_path)))
        self.setAutoFillBackground(True)

        self.class1_path: Path | None = None
        self.class2_path: Path | None = None
        self.conclusion_jpg_path: Path | None = None
        self.avg_csv_paths: list[Path] = []
        self._import_in_progress = False

        root = QWidget()
        self.setCentralWidget(root)

        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.outer_scroll = QScrollArea()
        self.outer_scroll.setWidgetResizable(True)
        outer.addWidget(self.outer_scroll)

        self.main_content = QWidget()
        self.main_content_layout = QHBoxLayout(self.main_content)
        self.main_content_layout.setContentsMargins(0, 0, 0, 0)
        self.main_content_layout.setSpacing(0)
        self.outer_scroll.setWidget(self.main_content)

        self.main_splitter = MainSplitter(Qt.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_content_layout.addWidget(self.main_splitter)

        initial_explorer_root = (
            self._effective_root_dir() if self._root_dir_available else Path.home()
        )

        self.root_explorer = RootExplorerWidget(
            root_dir=initial_explorer_root,
            password_callback=lambda: self._ask_password(
                title="Подтверждение изменения файлов",
                prompt="Введите пароль:",
            ),
            index_service=self._file_index_service,
            request_index_rebuild_callback=lambda: (
                self._schedule_file_tree_index_rebuild(force=True)
            ),
            parent=self,
        )
        self.root_explorer.setMinimumWidth(EXPLORER_MIN_WIDTH)
        self.root_explorer.setMaximumWidth(EXPLORER_MAX_WIDTH)

        self.main_splitter.addWidget(self.root_explorer)

        self.right_panel = QWidget()
        self.right_panel.setMinimumWidth(0)
        self.right_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        v = QVBoxLayout(self.right_panel)
        self.main_splitter.addWidget(self.right_panel)

        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)

        self.main_splitter.splitterMoved.connect(self._remember_explorer_width)

        self._explorer_last_width = EXPLORER_AVG_WIDTH
        self.main_splitter.set_default_left_width(self._explorer_last_width)

        about_row = QHBoxLayout()
        about_row.setContentsMargins(0, 0, 0, 0)
        about_row.setSpacing(8)

        self.btn_toggle_explorer = QToolButton()
        self.btn_toggle_explorer.setText("≡")
        self.btn_toggle_explorer.setToolTip("Проводник")
        self.btn_toggle_explorer.setCheckable(True)
        self.btn_toggle_explorer.setAutoRaise(True)
        self.btn_toggle_explorer.setCursor(Qt.PointingHandCursor)
        self.btn_toggle_explorer.setStyleSheet(
            """
            QToolButton {
                border: none;
                font-size: 22px;
                padding: 2px 4px;
            }
            QToolButton:hover {
                background: #e9e9e9;
                border-radius: 4px;
            }
            QToolButton:checked, QToolButton:pressed {
                background: #dcdcdc;
                border-radius: 4px;
            }
            """
        )
        self.btn_toggle_explorer.toggled.connect(self._on_explorer_panel_toggled)

        self.lbl_about = QLabel(
            "Система предназначена для импорта и хранения результатов HLA-исследований в базе данных, формирования отсортированных Excel-отчётов, расчёта усредненного по низкому разрешению титра антител к A, B и DRB1, а также автоматического формирования заключений скрининга и идентификации."
        )
        self.lbl_about.setWordWrap(True)
        self.lbl_about.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.lbl_about.setStyleSheet("color: #555555;")

        self.btn_settings = QToolButton()
        self.btn_settings.setText("⛭")
        self.btn_settings.setToolTip("Настройки")
        self.btn_settings.setAutoRaise(True)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setStyleSheet(
            """
            QToolButton {
                border: none;
                font-size: 20px;
                padding: 4px 4px;
            }
            QToolButton:hover {
                background: #e9e9e9;
                border-radius: 4px;
            }
            QToolButton:pressed {
                background: #dcdcdc;
                border-radius: 4px;
            }
            """
        )
        self.btn_settings.clicked.connect(self.open_settings_dialog)

        self.btn_about = QToolButton()
        self.btn_about.setText("🛈")
        self.btn_about.setToolTip("О программе")
        self.btn_about.setAutoRaise(True)
        self.btn_about.setCursor(Qt.PointingHandCursor)
        self.btn_about.setStyleSheet(
            """
            QToolButton {
                border: none;
                font-size: 20px;
                padding: 4px 4px;
            }
            QToolButton:hover {
                background: #e9e9e9;
                border-radius: 4px;
            }
            QToolButton:pressed {
                background: #dcdcdc;
                border-radius: 4px;
            }
            """
        )
        self.btn_about.clicked.connect(self.show_about_dialog)

        about_row.addWidget(self.btn_toggle_explorer, 0, Qt.AlignTop)
        about_row.addWidget(self.lbl_about, 1)
        about_row.addWidget(self.btn_settings, 0, Qt.AlignTop)
        about_row.addWidget(self.btn_about, 0, Qt.AlignTop)

        v.addLayout(about_row)

        gb_patient = QGroupBox("Данные пациента")
        gb_patient.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        vb_patient = QVBoxLayout(gb_patient)

        lbl_patient_hint = QLabel(
            "Обязательны поля: дата поступления материала, орган, имя, пол и дата рождения. "
            "Поле «Новая фамилия» опционально. Необходимо заполнить хотя бы одно из полей: "
            "«Фамилия» или «Новая фамилия». Поиск пациента учитывает обе фамилии.\n"
        )
        lbl_patient_hint.setWordWrap(True)
        lbl_patient_hint.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lbl_patient_hint.setStyleSheet("color: #555555;")
        vb_patient.addWidget(lbl_patient_hint)

        form = QFormLayout()
        vb_patient.addLayout(form)

        today = date.today()

        self.test_date = DateInput(
            min_date=get_date_min_test(),
            max_date=today,
            default=today,
            placeholder=False,
            required_year_prefix="2",
        )
        form.addRow("Дата поступления материала:", self.test_date)

        self.organ = QComboBox()
        self.organ.addItems(ORGANS)
        self.organ.setCurrentIndex(-1)
        self.organ.setPlaceholderText("Выберите орган")
        form.addRow("Орган:", self.organ)

        self.last_name = QLineEdit()
        form.addRow("Фамилия:", self.last_name)

        self.chk_new_lastname = QCheckBox()
        self.new_last_name = QLineEdit()
        self.new_last_name.setEnabled(False)

        newln_container = QWidget()
        newln_layout = QHBoxLayout(newln_container)
        newln_layout.setContentsMargins(0, 0, 0, 0)
        newln_layout.setSpacing(6)
        newln_layout.addWidget(self.chk_new_lastname)
        newln_layout.addWidget(self.new_last_name)

        form.addRow("Новая фамилия:", newln_container)
        self.chk_new_lastname.toggled.connect(self.on_toggle_new_lastname)

        self._last_name_lookup: dict[str, object] = {}
        self._new_last_name_lookup: dict[str, object] = {}
        self._selected_patient_dir_from_autocomplete: Path | None = None
        self._selected_patient_snapshot: dict[str, object] | None = None
        self._autocomplete_thread_pool = QThreadPool(self)
        self._autocomplete_request_seq = 0
        self._active_autocomplete_request_id = {"last": 0, "new": 0}
        self._pending_autocomplete_text = {"last": "", "new": ""}

        self._autocomplete_timer_last = QTimer(self)
        self._autocomplete_timer_last.setSingleShot(True)
        self._autocomplete_timer_last.setInterval(250)
        self._autocomplete_timer_last.timeout.connect(
            lambda: self._start_patient_autocomplete_search("last")
        )

        self._autocomplete_timer_new = QTimer(self)
        self._autocomplete_timer_new.setSingleShot(True)
        self._autocomplete_timer_new.setInterval(250)
        self._autocomplete_timer_new.timeout.connect(
            lambda: self._start_patient_autocomplete_search("new")
        )
        self._init_patient_autocomplete()

        self.first_name = QLineEdit()
        form.addRow("Имя:", self.first_name)

        self.middle_name = QLineEdit()
        form.addRow("Отчество:", self.middle_name)

        rx_last = QRegularExpression(
            r"^(?:|[А-Яа-яЁё]|[А-Яа-яЁё]{2,}(?:-[А-Яа-яЁё]*)?)$"
        )
        rx_name = QRegularExpression(r"^[А-Яа-яЁё]*$")

        self.last_name.setValidator(QRegularExpressionValidator(rx_last))
        self.new_last_name.setValidator(QRegularExpressionValidator(rx_last))
        self.first_name.setValidator(QRegularExpressionValidator(rx_last))
        self.middle_name.setValidator(QRegularExpressionValidator(rx_name))

        self.last_name.editingFinished.connect(self._normalize_names)
        self.new_last_name.editingFinished.connect(self._normalize_names)
        self.first_name.editingFinished.connect(self._normalize_names)
        self.middle_name.editingFinished.connect(self._normalize_names)

        self.sex = QComboBox()
        self.sex.addItem("жен.", "f")
        self.sex.addItem("муж.", "m")
        self.sex.setCurrentIndex(-1)
        self.sex.setPlaceholderText("Укажите пол")
        form.addRow("Пол:", self.sex)

        self.birth_date = DateInput(
            min_date=get_date_min_birth(),
            max_date=today,
            default=None,
            placeholder=True,
        )
        form.addRow("Дата рождения:", self.birth_date)

        v.addWidget(gb_patient)

        g = QGroupBox("CSV-файлы анализатора и JPG заключения")
        g.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        gv = QVBoxLayout(g)

        lbl_files_hint_top = QLabel(
            "Для импорта исследования выберите хотя бы один CSV-файл Class I или Class II и JPG заключения. "
            "Для отдельного импорта JPG допускается только файл заключения."
        )
        lbl_files_hint_top.setWordWrap(True)
        lbl_files_hint_top.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lbl_files_hint_top.setStyleSheet("color: #555555;")
        gv.addWidget(lbl_files_hint_top)

        self.lbl_c1 = QLabel("не выбрано")
        self.lbl_c2 = QLabel("не выбрано")
        self.lbl_jpg = QLabel("не выбрано")

        self.chk_delete_c1 = QCheckBox("Заменить Excel Class I  ")
        self.chk_delete_c2 = QCheckBox("Заменить Excel Class II ")
        self.chk_delete_jpg = QCheckBox("Заменить заключение")

        self.chk_delete_study = QCheckBox("Удалить исследование")

        self.chk_delete_c1.setChecked(False)
        self.chk_delete_c2.setChecked(False)
        self.chk_delete_jpg.setChecked(False)

        self.chk_delete_c1.toggled.connect(self._on_delete_c1_toggled)
        self.chk_delete_c2.toggled.connect(self._on_delete_c2_toggled)
        self.chk_delete_jpg.toggled.connect(self._on_delete_jpg_toggled)

        # ---------- Class I ----------
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(6)

        self.btn_c1 = QPushButton("Выбрать CSV Class I          ")
        self.btn_c1_cancel = QPushButton("Отмена")
        self.btn_c1_cancel.setFixedWidth(80)

        left1 = QHBoxLayout()
        left1.setContentsMargins(0, 0, 0, 0)
        left1.setSpacing(6)
        left1.addWidget(self.btn_c1)
        left1.addWidget(self.lbl_c1, 1)

        middle1 = QHBoxLayout()
        middle1.setContentsMargins(0, 0, 0, 0)
        middle1.setSpacing(0)
        middle1.addStretch(1)
        middle1.addWidget(self.btn_c1_cancel)
        middle1.addStretch(1)

        right1 = QHBoxLayout()
        right1.setContentsMargins(0, 0, 0, 0)
        right1.setSpacing(0)
        right1.addStretch(1)
        right1.addWidget(self.chk_delete_c1)

        row1.addLayout(left1, 1)
        row1.addLayout(middle1)
        row1.addLayout(right1, 1)

        # ---------- Class II ----------
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(6)

        self.btn_c2 = QPushButton("Выбрать CSV Class II         ")
        self.btn_c2_cancel = QPushButton("Отмена")
        self.btn_c2_cancel.setFixedWidth(80)

        left2 = QHBoxLayout()
        left2.setContentsMargins(0, 0, 0, 0)
        left2.setSpacing(6)
        left2.addWidget(self.btn_c2)
        left2.addWidget(self.lbl_c2, 1)

        middle2 = QHBoxLayout()
        middle2.setContentsMargins(0, 0, 0, 0)
        middle2.setSpacing(0)
        middle2.addStretch(1)
        middle2.addWidget(self.btn_c2_cancel)
        middle2.addStretch(1)

        right2 = QHBoxLayout()
        right2.setContentsMargins(0, 0, 0, 0)
        right2.setSpacing(0)
        right2.addStretch(1)
        right2.addWidget(self.chk_delete_c2)

        row2.addLayout(left2, 1)
        row2.addLayout(middle2)
        row2.addLayout(right2, 1)

        # ---------- JPG заключение ----------
        row3 = QHBoxLayout()
        row3.setContentsMargins(0, 0, 0, 0)
        row3.setSpacing(6)

        self.btn_jpg = QPushButton("Выбрать JPG заключения")
        self.btn_jpg_cancel = QPushButton("Отмена")
        self.btn_jpg_cancel.setFixedWidth(80)

        left3 = QHBoxLayout()
        left3.setContentsMargins(0, 0, 0, 0)
        left3.setSpacing(6)
        left3.addWidget(self.btn_jpg)
        left3.addWidget(self.lbl_jpg, 1)

        middle3 = QHBoxLayout()
        middle3.setContentsMargins(0, 0, 0, 0)
        middle3.setSpacing(0)
        middle3.addStretch(1)
        middle3.addWidget(self.btn_jpg_cancel)
        middle3.addStretch(1)

        right3 = QHBoxLayout()
        right3.setContentsMargins(0, 0, 0, 0)
        right3.setSpacing(0)
        right3.addStretch(1)
        right3.addWidget(self.chk_delete_jpg)

        row3.addLayout(left3, 1)
        row3.addLayout(middle3)
        row3.addLayout(right3, 1)

        gv.addLayout(row1)
        gv.addLayout(row2)
        gv.addLayout(row3)

        lbl_files_hint_bottom = QLabel(
            "Чтобы заменить существующее исследование на новое, одновременно отметьте "
            "соответствующие чекбоксы «Заменить Class I/II» и/или «Заменить заключение» "
            "и выберите файлы для импорта."
        )
        lbl_files_hint_bottom.setWordWrap(True)
        lbl_files_hint_bottom.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        lbl_files_hint_bottom.setStyleSheet("color: #555555;")
        gv.addWidget(lbl_files_hint_bottom)

        gb_delete = QGroupBox("Полное удаление")
        gb_delete.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        vb_delete = QVBoxLayout(gb_delete)

        lbl_delete_hint = QLabel(
            "Удаление исследования без замены на новое. Будет удалена папка исследования, связанные исходные файлы и записи в базе данных."
        )
        lbl_delete_hint.setWordWrap(True)
        lbl_delete_hint.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lbl_delete_hint.setStyleSheet("color: #555555;")
        vb_delete.addWidget(lbl_delete_hint)

        vb_delete.addStretch(1)

        delete_study_row = QHBoxLayout()
        delete_study_row.addStretch(1)
        delete_study_row.addWidget(self.chk_delete_study)
        delete_study_row.addStretch(1)
        vb_delete.addLayout(delete_study_row)

        vb_delete.addStretch(1)

        files_and_delete_row = QHBoxLayout()
        files_and_delete_row.setContentsMargins(0, 0, 0, 0)
        files_and_delete_row.setSpacing(6)
        files_and_delete_row.addWidget(g, 3)
        files_and_delete_row.addWidget(gb_delete, 1)

        v.addLayout(files_and_delete_row)

        self.btn_c1.clicked.connect(lambda: self.pick_csv(1))
        self.btn_c2.clicked.connect(lambda: self.pick_csv(2))
        self.btn_jpg.clicked.connect(self.pick_conclusion_jpg)
        self.btn_c1_cancel.clicked.connect(lambda: self.clear_csv(1))
        self.btn_c2_cancel.clicked.connect(lambda: self.clear_csv(2))
        self.btn_jpg_cancel.clicked.connect(self.clear_conclusion_jpg)
        self.chk_delete_study.toggled.connect(self._on_delete_study_whole_toggled)

        row = QHBoxLayout()

        gb_concl = QGroupBox("Сформировать заключение")
        gb_concl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        vb_concl = QVBoxLayout(gb_concl)

        self.rb_concl_mnpc = QRadioButton("ГУ «МНПЦ ХТиГ»")
        self.rb_concl_rnpc = QRadioButton("ГУ «РНПЦ ТиМБ»")

        self.concl_place_group = QButtonGroup(self)
        self.concl_place_group.setExclusive(True)
        self.concl_place_group.addButton(self.rb_concl_mnpc)
        self.concl_place_group.addButton(self.rb_concl_rnpc)

        left_half_widget = QWidget()
        left_half_layout = QHBoxLayout(left_half_widget)
        left_half_layout.setContentsMargins(0, 0, 0, 0)
        left_half_layout.setSpacing(12)
        left_half_layout.addWidget(self.rb_concl_mnpc, 1)
        left_half_layout.addWidget(self.rb_concl_rnpc, 1)

        concl_place_row = QHBoxLayout()
        concl_place_row.setContentsMargins(0, 0, 0, 0)
        concl_place_row.setSpacing(0)
        concl_place_row.addWidget(left_half_widget, 1)
        concl_place_row.addStretch(1)

        vb_concl.addLayout(concl_place_row)

        concl_form = QFormLayout()
        vb_concl.addLayout(concl_form)

        self.concl_num_register = QLineEdit()
        self.concl_num_register.setValidator(
            QIntValidator(0, 10**9, self.concl_num_register)
        )
        concl_form.addRow("№ по журналу:", self.concl_num_register)

        self.concl_head_of = QComboBox()
        self.concl_acting = QCheckBox("И. о.")
        head_row = QWidget()
        head_lay = QHBoxLayout(head_row)
        head_lay.setContentsMargins(0, 0, 0, 0)
        head_lay.setSpacing(6)
        head_lay.addWidget(self.concl_acting, 0)
        head_lay.addWidget(self.concl_head_of, 1)
        concl_form.addRow("Заведующий:", head_row)

        self.concl_bio1 = QComboBox()
        self.concl_doctor = QCheckBox("Врач")
        bio1_row = QWidget()
        bio1_lay = QHBoxLayout(bio1_row)
        bio1_lay.setContentsMargins(0, 0, 0, 0)
        bio1_lay.setSpacing(6)
        bio1_lay.addWidget(self.concl_doctor, 0)
        bio1_lay.addWidget(self.concl_bio1, 1)
        concl_form.addRow("Биолог:", bio1_row)

        self.concl_bio2 = QComboBox()
        concl_form.addRow("Биолог:", self.concl_bio2)

        self.concl_screening = QCheckBox("Добавить СКРИНИНГ в методы исследования")
        vb_concl.addWidget(self.concl_screening)

        hb_concl_btns = QHBoxLayout()
        self.btn_concl_save = QPushButton("Сохранить")
        self.btn_concl_print = QPushButton("Распечатать")
        hb_concl_btns.addWidget(self.btn_concl_save)
        hb_concl_btns.addWidget(self.btn_concl_print)
        vb_concl.addLayout(hb_concl_btns)

        self._setup_staff_combo(
            self.concl_head_of,
            items=[],
            default="",
        )
        self._setup_staff_combo(
            self.concl_bio1,
            items=[],
            default="",
        )
        self._setup_staff_combo(
            self.concl_bio2,
            items=[],
            default="",
        )

        self.rb_concl_mnpc.toggled.connect(
            lambda checked: self._on_concl_place_changed("mnpc", checked)
        )
        self.rb_concl_rnpc.toggled.connect(
            lambda checked: self._on_concl_place_changed("rnpc", checked)
        )
        self.concl_acting.toggled.connect(self._on_concl_acting_toggled)
        self.concl_doctor.toggled.connect(self._on_concl_doctor_toggled)

        # Стартовое поведение как раньше — по умолчанию активен МНПЦ
        self._apply_default_conclusion_place_from_prefs()

        self.btn_concl_save.clicked.connect(self.on_conclusion_save)
        self.btn_concl_print.clicked.connect(self.on_conclusion_print)

        gb_avg = QGroupBox("Титр антител к A, B и DRB1")
        gb_avg.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        vb_avg = QVBoxLayout(gb_avg)
        vb_avg.setContentsMargins(9, 9, 9, 9)
        vb_avg.setSpacing(6)

        lbl_avg_hint_top = QLabel(
            "Результаты будут сохранены в виде Excel-таблицы рядом с исходными файлами или в папку, заданную в настройках.\n"
        )
        lbl_avg_hint_top.setWordWrap(True)
        lbl_avg_hint_top.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lbl_avg_hint_top.setStyleSheet("color: #555555;")
        vb_avg.addWidget(lbl_avg_hint_top)

        lbl_avg = QLabel("Вычислить среднее значение по низкому разрешению:")
        lbl_avg.setWordWrap(True)
        lbl_avg.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        vb_avg.addWidget(lbl_avg)

        middle_avg = QWidget()
        middle_avg_layout = QVBoxLayout(middle_avg)
        middle_avg_layout.setContentsMargins(0, 0, 0, 0)
        middle_avg_layout.setSpacing(0)
        middle_avg_layout.addStretch(1)

        threshold_row = QWidget()
        threshold_layout = QHBoxLayout(threshold_row)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_layout.setSpacing(6)

        lbl_avg_threshold = QLabel("Нижний порог чувствительности:")
        self.ed_avg_min_titer = QLineEdit()
        self.ed_avg_min_titer.setValidator(
            QIntValidator(0, 10**9, self.ed_avg_min_titer)
        )
        self.ed_avg_min_titer.setPlaceholderText("0")
        self.ed_avg_min_titer.setAlignment(Qt.AlignCenter)
        self.ed_avg_min_titer.setFixedWidth(120)

        threshold_layout.addStretch(1)
        threshold_layout.addWidget(lbl_avg_threshold)
        threshold_layout.addWidget(self.ed_avg_min_titer)
        threshold_layout.addStretch(1)

        hb_avg = QHBoxLayout()
        hb_avg.setContentsMargins(0, 0, 0, 0)
        hb_avg.setSpacing(6)

        self.btn_avg_pick = QPushButton("Выбрать CSV")
        self.lbl_avg_csv = QLabel("не выбрано")
        self.btn_avg_calc = QPushButton("Вычислить")
        self.btn_avg_cancel = QPushButton("Отмена")

        self.ed_avg_min_titer.returnPressed.connect(self._confirm_avg_threshold_input)

        hb_avg.addStretch(1)
        hb_avg.addWidget(self.btn_avg_pick)
        hb_avg.addWidget(self.lbl_avg_csv)
        hb_avg.addWidget(self.btn_avg_calc)
        hb_avg.addWidget(self.btn_avg_cancel)
        hb_avg.addStretch(1)

        middle_avg_layout.addWidget(threshold_row)
        middle_avg_layout.addSpacing(6)
        middle_avg_layout.addLayout(hb_avg)
        middle_avg_layout.addStretch(1)

        vb_avg.addWidget(middle_avg, 1)

        lbl_avg_hint_bottom = QLabel(
            "Файлы с пустой таблицей антител будут автоматически пропущены."
        )
        lbl_avg_hint_bottom.setWordWrap(True)
        lbl_avg_hint_bottom.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        lbl_avg_hint_bottom.setStyleSheet("color: #555555;")
        vb_avg.addWidget(lbl_avg_hint_bottom)

        self.btn_avg_pick.clicked.connect(self.pick_avg_csv)
        self.btn_avg_calc.clicked.connect(self.calc_avg_excel)
        self.btn_avg_cancel.clicked.connect(self._clear_avg_csv_selection)

        row.addWidget(gb_concl, 1)
        row.addWidget(gb_avg, 1)
        v.addLayout(row)

        self.btn_run = QPushButton("ИМПОРТИРОВАТЬ")
        self.btn_run.clicked.connect(self.run_import)
        v.addWidget(self.btn_run, 0)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.status.setMinimumHeight(_STATUS_LABEL_MIN_HEIGHT)
        self.status.setMaximumHeight(_STATUS_LABEL_MAX_HEIGHT)
        v.addWidget(self.status, 0)

        self._set_root_dir_available_state(
            available=self._root_dir_available,
            status_text=(
                "⚠️ Файловая база недоступна. Приложение запущено с ограничениями. "
                "Проверьте путь в окне «Настройки»."
                if not self._root_dir_available
                else None
            ),
        )

        if self._root_dir_available:
            self._schedule_file_tree_index_rebuild(force=False)

        self._update_main_content_minimum_size()

    def _update_main_content_minimum_size(self) -> None:
        self.ensurePolished()

        central = self.centralWidget()
        if central is not None and central.layout() is not None:
            central.layout().activate()

        if self.right_panel.layout() is not None:
            self.right_panel.layout().activate()

        self.right_panel.adjustSize()

        explorer_visible = (
            self._root_dir_available and self.btn_toggle_explorer.isChecked()
        )

        left_width = 0
        left_height = 0

        if explorer_visible:
            # ВАЖНО:
            # для расчёта минимальной ширины main_content
            # нельзя использовать текущую ширину панели проводника,
            # иначе при перетаскивании splitter'а внутренний виджет
            # становится шире окна и правая часть "отрывается" от его границы.
            #
            # Здесь нужна только минимально допустимая ширина левой панели,
            # а не её текущее значение.
            left_width = EXPLORER_MIN_WIDTH
            left_height = self.root_explorer.sizeHint().height()

        handle_width = self.main_splitter.handleWidth() if explorer_visible else 0

        margins = self.main_content_layout.contentsMargins()
        right_hint = self.right_panel.sizeHint()

        total_width = (
            margins.left()
            + left_width
            + handle_width
            + right_hint.width()
            + margins.right()
        )

        total_height = (
            margins.top() + max(right_hint.height(), left_height) + margins.bottom()
        )

        self.main_content.setMinimumSize(total_width, total_height)

    def calculate_initial_size(self) -> QSize:
        self._update_main_content_minimum_size()

        base = self.main_content.minimumSize()

        extra_width = _INITIAL_WINDOW_EXTRA_WIDTH
        extra_height = _INITIAL_WINDOW_EXTRA_HEIGHT

        return QSize(
            int(base.width() * _INITIAL_WINDOW_WIDTH_FACTOR + extra_width),
            int(base.height() + extra_height),
        )

    def center_on_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return

        screen_geometry = screen.availableGeometry()
        frame_geometry = self.frameGeometry()
        frame_geometry.moveCenter(screen_geometry.center())
        self.move(frame_geometry.topLeft())

    def showEvent(self, event) -> None:
        super().showEvent(event)

        if not self._center_on_screen_pending:
            return

        self._center_on_screen_pending = False

        if self.isMaximized() or self.isFullScreen():
            return

        QTimer.singleShot(0, self.center_on_screen)

    def closeEvent(self, event) -> None:
        if self._import_in_progress:
            QMessageBox.warning(
                self,
                "Операция выполняется",
                "Сейчас выполняется импорт или изменение файлов.\n\n"
                "Дождитесь завершения операции, затем закройте приложение.",
            )
            event.ignore()
            return

        super().closeEvent(event)

    def open_settings_dialog(self) -> None:
        if self._import_in_progress:
            self._warn(
                "Во время импорта изменение настроек недоступно.\n\n"
                "Дождитесь завершения текущей операции."
            )
            return

        dlg = AppSettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self._clear_patient_autocomplete()
            self._clear_selected_patient_folder_choice()
            self._apply_default_conclusion_place_from_prefs()
            self._refresh_root_dir_availability()

    def _ensure_postgres_available_for_import(self) -> bool:
        prefs = load_effective_app_preferences()

        try:
            probe_db_settings(
                db_user=prefs.db_user,
                db_password=prefs.db_password,
                db_host=prefs.db_host,
                db_port=prefs.db_port,
                db_name=prefs.db_name,
            )
            return True
        except Exception as e:
            QMessageBox.critical(
                self,
                "PostgreSQL недоступен",
                "Не удалось подключиться к базе данных PostgreSQL.\n\n"
                "Импорт не будет выполнен.\n"
                "Проверьте параметры подключения в окне «Настройки».\n\n"
                f"{e}",
            )
            return False

    def _effective_root_dir(self) -> Path:
        return load_effective_app_preferences().root_dir

    def _create_file_index_service(self) -> FileTreeIndexService:
        app_data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if app_data_dir:
            base_dir = Path(app_data_dir)
        else:
            base_dir = Path.home() / ".hla_laboratory_system"

        db_path = base_dir / "file_tree_index.sqlite3"
        return FileTreeIndexService(db_path)

    def _set_root_dir_available_state(
        self,
        *,
        available: bool,
        status_text: str | None = None,
    ) -> None:
        self._root_dir_available = available

        if available:
            explorer_visible = load_effective_app_preferences().explorer_visible

            self.btn_toggle_explorer.setEnabled(True)

            self.btn_toggle_explorer.blockSignals(True)
            self.btn_toggle_explorer.setChecked(explorer_visible)
            self.btn_toggle_explorer.blockSignals(False)

            if not explorer_visible:
                # Сначала гарантированно убираем проводник из раскладки,
                # и только потом обновляем его внутреннее состояние.
                self._set_explorer_visible(False)
                self.root_explorer.set_root_path(self._effective_root_dir())
            else:
                self._set_explorer_visible(True)
        else:
            self.btn_toggle_explorer.blockSignals(True)
            self.btn_toggle_explorer.setChecked(False)
            self.btn_toggle_explorer.blockSignals(False)
            self.btn_toggle_explorer.setEnabled(False)

            self._set_explorer_visible(False)

        if status_text:
            self.status.setText(status_text)

    def _refresh_root_dir_availability(self) -> None:
        try:
            probe_root_dir_settings(root_dir=self._effective_root_dir())
        except Exception as e:
            self._set_root_dir_available_state(
                available=False,
                status_text=(
                    "⚠️ Настройки сохранены, но файловая база недоступна. "
                    "Проверьте путь в окне «Настройки».\n\n"
                    f"{e}"
                ),
            )
        else:
            self._set_root_dir_available_state(
                available=True,
                status_text=f"✅ Настройки сохранены. Файловая база: {self._effective_root_dir()}",
            )
            self._schedule_file_tree_index_rebuild(force=True)

    def _refresh_root_explorer_after_import(self) -> None:
        if not self._root_dir_available:
            return

        try:
            self.root_explorer.refresh()
        except Exception:
            pass

    def _wait_for_file_tree_index_build_to_finish(self) -> bool:
        if not self._file_index_build_in_progress:
            return True

        self.status.setText(
            "⏳ Завершается фоновое обновление локального индекса перед импортом..."
        )
        self._process_non_input_events()

        deadline = time.monotonic() + _FILE_INDEX_BUILD_WAIT_TIMEOUT_SECONDS
        while self._file_index_build_in_progress:
            if time.monotonic() >= deadline:
                self.status.setText(
                    "⚠ Фоновое обновление локального индекса не завершилось вовремя. "
                    "Операция остановлена, чтобы избежать зависания или конфликтов "
                    "с файловой базой."
                )
                return False
            self._process_non_input_events()
            time.sleep(0.03)

        return True

    def _wait_for_patient_autocomplete_tasks_to_finish(self) -> bool:
        """
        Дожидается завершения фоновых задач автоподсказок по папкам пациентов.
        Это убирает конкуренцию с rename_patient_folder(...) по тем же каталогам.
        """
        self._autocomplete_timer_last.stop()
        self._autocomplete_timer_new.stop()

        self._active_autocomplete_request_id = {"last": 0, "new": 0}
        self._pending_autocomplete_text = {"last": "", "new": ""}

        if not self._autocomplete_thread_pool.waitForDone(
            _PATIENT_AUTOCOMPLETE_WAIT_TIMEOUT_MS
        ):
            self.status.setText(
                "⚠ Фоновые подсказки по папкам пациентов не завершились вовремя. "
                "Операция остановлена, чтобы избежать конфликта с переименованием."
            )
            return False

        for completer in (self._last_name_completer, self._new_last_name_completer):
            try:
                popup = completer.popup()
                if popup is not None:
                    popup.hide()
            except Exception:
                pass

        self._clear_patient_autocomplete()
        self._release_pending_fs_handles()
        return True

    def _prepare_ui_for_patient_dir_rename(self) -> bool:
        if not self._wait_for_file_tree_index_build_to_finish():
            return False

        if not self._wait_for_patient_autocomplete_tasks_to_finish():
            return False

        if self._root_dir_available:
            try:
                self.root_explorer.suspend_filesystem_model()
            except Exception:
                pass

        self._release_pending_fs_handles()
        return True

    def _restore_ui_after_patient_dir_rename(self) -> None:
        if self._root_dir_available:
            try:
                self.root_explorer.resume_filesystem_model()
            except Exception:
                pass

        self._process_non_input_events()

    def _resume_file_tree_index_updates_after_import(self) -> None:
        """
        После завершения импорта запускает одну отложенную переиндексацию,
        если во время импорта были запросы на обновление индекса
        или индекс нужно было перестроить после изменения файловой базы.
        """
        if self._import_in_progress:
            return

        if self._file_index_build_in_progress:
            return

        if not (
            self._file_index_build_queued or self._file_index_rebuild_deferred_by_import
        ):
            return

        self._file_index_build_queued = False
        self._file_index_rebuild_deferred_by_import = False
        self._schedule_file_tree_index_rebuild(force=True)

    def _schedule_file_tree_index_rebuild(self, *, force: bool = False) -> None:
        if not self._root_dir_available:
            return

        # Во время импорта никакую новую индексацию НЕ запускаем.
        # Иначе она может пересечься с rename/delete/copy по тем же папкам
        # и вызвать ложную "блокировку" на Windows.
        if self._import_in_progress:
            self._file_index_rebuild_deferred_by_import = True
            return

        root_dir = self._effective_root_dir()
        if root_dir is None:
            return

        if self._file_index_build_in_progress:
            if force:
                self._file_index_build_queued = True
            return

        skipped_dir_count = self._file_index_service.skipped_dir_count()
        if (
            not force
            and self._file_index_service.is_ready_for(root_dir)
            and skipped_dir_count <= 0
        ):
            self.root_explorer.notify_index_ready(
                entry_count=self._file_index_service.entry_count(),
                skipped_dir_count=skipped_dir_count,
            )
            return

        self._file_index_build_in_progress = True
        self.root_explorer.notify_index_build_started()

        task = FileTreeIndexBuildTask(
            index_service=self._file_index_service,
            root_dir=root_dir,
        )
        task.signals.finished.connect(self._on_file_tree_index_build_finished)
        task.signals.failed.connect(self._on_file_tree_index_build_failed)
        self._file_index_thread_pool.start(task)

    def _on_file_tree_index_build_finished(
        self,
        root_dir_text: str,
        entry_count: int,
    ) -> None:
        self._file_index_build_in_progress = False

        current_root = self._effective_root_dir()
        if current_root is not None and str(current_root.resolve(strict=False)) == str(
            Path(root_dir_text).resolve(strict=False)
        ):
            self.root_explorer.notify_index_ready(
                entry_count=entry_count,
                skipped_dir_count=self._file_index_service.skipped_dir_count(),
            )

        if self._file_index_build_queued and not self._import_in_progress:
            self._file_index_build_queued = False
            self._schedule_file_tree_index_rebuild(force=True)

    def _on_file_tree_index_build_failed(
        self,
        _root_dir_text: str,
        error_text: str,
    ) -> None:
        self._file_index_build_in_progress = False
        self.root_explorer.notify_index_build_failed(error_text)

        if self._file_index_build_queued and not self._import_in_progress:
            self._file_index_build_queued = False
            self._schedule_file_tree_index_rebuild(force=True)

    def _ensure_root_dir_available_for_import(self) -> bool:
        root_dir = self._effective_root_dir()

        try:
            probe_root_dir_settings(root_dir=root_dir)
        except Exception as e:
            self._set_root_dir_available_state(
                available=False,
                status_text=(
                    "❌ Файловая база недоступна. Импорт не выполнен. "
                    "Проверьте путь в окне «Настройки»."
                ),
            )
            QMessageBox.critical(
                self,
                "Файловая база недоступна",
                "Не удалось подключиться к файловой базе.\n\n"
                "Импорт не будет выполнен.\n"
                "Проверьте путь к файловой базе в окне «Настройки».\n\n"
                f"{e}",
            )
            return False

        if not self._root_dir_available:
            self._set_root_dir_available_state(
                available=True,
                status_text=f"✅ Файловая база доступна: {root_dir}",
            )

        return True

    def _set_explorer_visible(self, visible: bool) -> None:
        if visible and not self._root_dir_available:
            self.root_explorer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            self.root_explorer.setMinimumWidth(0)
            self.root_explorer.setMaximumWidth(0)
            self.root_explorer.hide()

            viewport_width = max(self.outer_scroll.viewport().width(), 1)
            self.main_splitter.setSizes([0, viewport_width])
            self._update_main_content_minimum_size()
            return

        if visible:
            self.root_explorer.setSizePolicy(
                QSizePolicy.Preferred,
                QSizePolicy.Expanding,
            )
            self.root_explorer.setMinimumWidth(EXPLORER_MIN_WIDTH)
            self.root_explorer.setMaximumWidth(EXPLORER_MAX_WIDTH)

            self.root_explorer.set_root_path(self._effective_root_dir())
            self.root_explorer.show()

            left_width = min(
                max(self._explorer_last_width, EXPLORER_MIN_WIDTH),
                EXPLORER_MAX_WIDTH,
            )

            # splitter находится внутри QScrollArea, поэтому ориентироваться
            # нужно на ширину viewport, а не на полную ширину MainWindow.
            viewport_width = max(self.outer_scroll.viewport().width(), left_width + 1)
            main_width = max(viewport_width - left_width, 1)

            self.main_splitter.setSizes([left_width, main_width])

            self.root_explorer.updateGeometry()
            self.right_panel.updateGeometry()
            self.main_splitter.updateGeometry()
            self._update_main_content_minimum_size()
            return

        sizes = self.main_splitter.sizes()
        if len(sizes) > 0 and sizes[0] > 0:
            self._explorer_last_width = min(
                max(sizes[0], EXPLORER_MIN_WIDTH),
                EXPLORER_MAX_WIDTH,
            )

        # ВАЖНО:
        # скрытый проводник не должен сохранять минимальную/максимальную ширину,
        # иначе после обновления модели QSplitter снова начинает учитывать его
        # в раскладке.
        self.root_explorer.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.root_explorer.setMinimumWidth(0)
        self.root_explorer.setMaximumWidth(0)
        self.root_explorer.hide()

        viewport_width = max(self.outer_scroll.viewport().width(), 1)
        self.main_splitter.setSizes([0, viewport_width])

        self.root_explorer.updateGeometry()
        self.right_panel.updateGeometry()
        self.main_splitter.updateGeometry()
        self._update_main_content_minimum_size()

    def _save_explorer_visible_pref(self, visible: bool) -> None:
        save_explorer_visibility_preference(visible)

    def _on_explorer_panel_toggled(self, checked: bool) -> None:
        self._set_explorer_visible(checked)
        self._save_explorer_visible_pref(checked)

    def _remember_explorer_width(self, _pos: int, _index: int) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) < 2:
            return

        left = sizes[0]
        right = sizes[1]

        # Если панель скрыта, не вмешиваемся
        if not self.root_explorer.isVisible():
            return

        clamped_left = min(max(left, EXPLORER_MIN_WIDTH), EXPLORER_MAX_WIDTH)

        if clamped_left != left:
            total = left + right
            self.main_splitter.blockSignals(True)
            try:
                self.main_splitter.setSizes(
                    [clamped_left, max(total - clamped_left, 1)]
                )
            finally:
                self.main_splitter.blockSignals(False)
            left = clamped_left

        self._explorer_last_width = left

    def _apply_default_conclusion_place_from_prefs(self) -> None:
        place = load_effective_app_preferences().conclusion_place

        if place == "rnpc":
            self.rb_concl_rnpc.setChecked(True)
        else:
            self.rb_concl_mnpc.setChecked(True)

    def _init_patient_autocomplete(self) -> None:
        self._last_name_model = QStringListModel(self)
        self._new_last_name_model = QStringListModel(self)

        self._last_name_completer = PatientFolderCompleter(self._last_name_model, self)
        self._new_last_name_completer = PatientFolderCompleter(
            self._new_last_name_model, self
        )

        for comp in (self._last_name_completer, self._new_last_name_completer):
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
            comp.setMaxVisibleItems(12)

        self.last_name.setCompleter(self._last_name_completer)
        self.new_last_name.setCompleter(self._new_last_name_completer)

        self.last_name.textEdited.connect(
            lambda text: self._schedule_patient_autocomplete(text, target="last")
        )
        self.new_last_name.textEdited.connect(
            lambda text: self._schedule_patient_autocomplete(text, target="new")
        )

        self._last_name_completer.activated[QModelIndex].connect(
            lambda index: self._apply_patient_autocomplete(index, target="last")
        )
        self._new_last_name_completer.activated[QModelIndex].connect(
            lambda index: self._apply_patient_autocomplete(index, target="new")
        )

        self.organ.currentIndexChanged.connect(self._clear_patient_autocomplete)
        self.organ.currentIndexChanged.connect(
            self._clear_selected_patient_folder_choice
        )

    def _current_organ_dir_for_autocomplete(self) -> Path | None:
        if not self._root_dir_available:
            return None

        organ = self.organ.currentText().strip()
        if not organ:
            return None
        return self._effective_root_dir() / organ

    def _clear_patient_autocomplete(self) -> None:
        self._last_name_lookup = {}
        self._new_last_name_lookup = {}
        self._last_name_model.setStringList([])
        self._new_last_name_model.setStringList([])

    def _clear_selected_patient_folder_choice(self) -> None:
        self._selected_patient_dir_from_autocomplete = None
        self._selected_patient_snapshot = None

    def _current_patient_form_state(self) -> dict[str, object]:
        return {
            "organ": self.organ.currentText().strip(),
            "last_name": self.last_name.text().strip(),
            "new_last_name_enabled": self.new_last_name.isEnabled(),
            "new_last_name": (
                self.new_last_name.text().strip()
                if self.new_last_name.isEnabled()
                else ""
            ),
            "first_name": self.first_name.text().strip(),
            "middle_name": self.middle_name.text().strip(),
            "sex": self.sex.currentData(),
            "birth_date": self.birth_date.get_date(),
        }

    def _remember_selected_patient_folder_choice(self, parsed) -> None:
        self._selected_patient_dir_from_autocomplete = parsed.folder
        self._selected_patient_snapshot = self._current_patient_form_state()

    def _get_locked_patient_dir_from_autocomplete(self) -> Path | None:
        patient_dir = self._selected_patient_dir_from_autocomplete

        if patient_dir is None or self._selected_patient_snapshot is None:
            return None

        if not patient_dir.exists() or not patient_dir.is_dir():
            self._clear_selected_patient_folder_choice()
            return None

        if self._current_patient_form_state() != self._selected_patient_snapshot:
            return None

        return patient_dir

    def _confirm_birth_date_mismatch_for_folder(
        self,
        patient_dir: Path,
        birth_d: date,
    ) -> bool:
        folder_birth = split_patient_folder_name(patient_dir.name)[2]

        if folder_birth and folder_birth != format_ddmmyyyy(birth_d):
            ans = QMessageBox.warning(
                self,
                "Проверка даты рождения",
                "Вы выбрали папку НОВОГО формата, где дата рождения в названии отличается от введённой.\n\n"
                f"В папке: {folder_birth}\n"
                f"Введено: {format_ddmmyyyy(birth_d)}\n\n"
                "Продолжить, используя выбранную папку БЕЗ переименования?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            return ans == QMessageBox.Yes

        return True

    def _set_patient_autocomplete_items(self, target: str, items: list) -> None:
        texts = [item.folder_name for item in items]
        lookup = {item.folder_name: item for item in items}

        if target == "last":
            self._last_name_lookup = lookup
            self._last_name_model.setStringList(texts)
        else:
            self._new_last_name_lookup = lookup
            self._new_last_name_model.setStringList(texts)

    def _schedule_patient_autocomplete(self, text: str, *, target: str) -> None:
        self._pending_autocomplete_text[target] = (text or "").strip()

        if target == "last":
            self._autocomplete_timer_last.start()
        else:
            self._autocomplete_timer_new.start()

    def _start_patient_autocomplete_search(self, target: str) -> None:
        organ_dir = self._current_organ_dir_for_autocomplete()
        query = self._pending_autocomplete_text.get(target, "").strip()

        if organ_dir is None or not query or not organ_dir.exists():
            self._set_patient_autocomplete_items(target, [])
            return

        self._autocomplete_request_seq += 1
        request_id = self._autocomplete_request_seq
        self._active_autocomplete_request_id[target] = request_id

        task = PatientAutocompleteTask(
            request_id=request_id,
            target=target,
            organ_dir=organ_dir,
            query=query,
        )
        task.signals.finished.connect(self._on_patient_autocomplete_finished)
        task.signals.failed.connect(self._on_patient_autocomplete_failed)
        self._autocomplete_thread_pool.start(task)

    def _on_patient_autocomplete_finished(
        self,
        request_id: int,
        target: str,
        matches: list,
    ) -> None:
        if self._active_autocomplete_request_id.get(target) != request_id:
            return

        current_text = (
            self.last_name.text().strip()
            if target == "last"
            else self.new_last_name.text().strip()
        )
        expected_text = self._pending_autocomplete_text.get(target, "").strip()

        if current_text != expected_text:
            return

        self._set_patient_autocomplete_items(target, matches)

        completer = (
            self._last_name_completer
            if target == "last"
            else self._new_last_name_completer
        )

        if matches:
            completer.complete()
        else:
            popup = completer.popup()
            if popup is not None:
                popup.hide()

    def _on_patient_autocomplete_failed(
        self,
        request_id: int,
        target: str,
        _error_text: str,
    ) -> None:
        if self._active_autocomplete_request_id.get(target) != request_id:
            return

        self._set_patient_autocomplete_items(target, [])

    def _apply_patient_autocomplete(
        self,
        index: QModelIndex,
        *,
        target: str,
    ) -> None:
        folder_name = index.data(Qt.DisplayRole)
        lookup = (
            self._last_name_lookup if target == "last" else self._new_last_name_lookup
        )
        parsed = lookup.get(folder_name)

        if parsed is None:
            return

        QTimer.singleShot(
            0,
            lambda p=parsed: self._apply_patient_autocomplete_deferred(p),
        )

    def _apply_patient_autocomplete_deferred(self, parsed) -> None:
        self._fill_patient_fields_from_folder(parsed)
        self._clear_patient_autocomplete()

    def _fill_patient_fields_from_folder(self, parsed) -> None:
        self.last_name.setText(parsed.last_name)

        if parsed.new_last_name:
            self.chk_new_lastname.setChecked(True)
            self.new_last_name.setText(parsed.new_last_name)
        else:
            self.chk_new_lastname.setChecked(False)
            self.new_last_name.setText("")

        self.first_name.setText(parsed.first_name)
        self.middle_name.setText(parsed.middle_name)

        if parsed.sex in ("f", "m"):
            idx = self.sex.findData(parsed.sex)
            self.sex.setCurrentIndex(idx)
        else:
            self.sex.setCurrentIndex(-1)

        if parsed.birth_date is not None:
            self.birth_date.set_date(parsed.birth_date)
        else:
            self.birth_date.clear_date()

        self._normalize_names()
        self._remember_selected_patient_folder_choice(parsed)

        self.status.setText(f"👤 Выбрана папка пациента: {parsed.folder_name}")

    def _confirm_delete_checkbox(self, checked: bool, hla_class: int) -> bool:
        if not checked:
            return True

        existing_file = self._existing_class_file_for_current_test_folder(hla_class)

        if existing_file is not None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Подтверждение удаления")
            box.setText(
                f"Вы действительно хотите заменить (удалить) результат Class {'I' if hla_class == 1 else 'II'} "
                "в папке пациента и базе данных?"
            )
            box.setInformativeText(
                f"Файл «{existing_file}» и соответствующая запись в базе данных "
                "за выбранную дату будут заменены новыми."
            )
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)

            ans = box.exec()
            return ans == QMessageBox.Yes

        else:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle("Нет файла для удаления")
            box.setInformativeText(
                f"Файла «{class_result_file_name(hla_class)}» в папке исследования "
                "за выбранную дату не существует."
            )
            box.setStandardButtons(QMessageBox.Ok)
            box.setDefaultButton(QMessageBox.Ok)

            box.exec()
            return False

    def _confirm_delete_conclusion_checkbox(self, checked: bool) -> bool:
        if not checked:
            return True

        existing_files = self._existing_conclusion_files_for_test_forlder()

        if len(existing_files) == 1:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Подтверждение удаления")
            box.setText("Вы действительно хотите заменить заключение?")

            box.setInformativeText(
                f"Файл заключения «{existing_files[0]}» в папке "
                "исследования за выбранную дату будет заменён."
            )

            box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)

            ans = box.exec()
            return ans == QMessageBox.Yes

        else:
            box = QMessageBox(self)

            if len(existing_files) > 1:
                box.setIcon(QMessageBox.Critical)
                box.setWindowTitle("Ошибка")
                box.setText("Найдено несколько файлов заключения")
                box.setInformativeText(
                    "В папке исследования за выбранную дату несколько файлов заключения:\n"
                    + "\n".join(f"• {name}" for name in existing_files)
                )
            else:
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle("Внимание")
                box.setText("Файл заключения не найден")
                box.setInformativeText(
                    f"В папке исследования за выбранную дату файлы «{NEGATIVE_CONCLUSION_FILE_NAME}» "
                    f"и «{POSITIVE_CONCLUSION_FILE_NAME}» отсутствуют."
                )

            box.setStandardButtons(QMessageBox.Ok)
            box.setDefaultButton(QMessageBox.Ok)

            box.exec()
            return False

    def _on_delete_c1_toggled(self, checked: bool) -> None:
        if checked:
            confirmed = self._confirm_delete_checkbox(checked, 1)
            if not confirmed:
                self.chk_delete_c1.blockSignals(True)
                self.chk_delete_c1.setChecked(False)
                self.chk_delete_c1.blockSignals(False)
                return

        self.btn_c1_cancel.setEnabled(not checked)

        if checked:
            self.btn_c1_cancel.setToolTip(
                "Недоступно при удалении результата для замены новым"
            )
        else:
            self.btn_c1_cancel.setToolTip("")

    def _on_delete_c2_toggled(self, checked: bool) -> None:
        if checked:
            confirmed = self._confirm_delete_checkbox(checked, 2)
            if not confirmed:
                self.chk_delete_c2.blockSignals(True)
                self.chk_delete_c2.setChecked(False)
                self.chk_delete_c2.blockSignals(False)
                return

        self.btn_c2_cancel.setEnabled(not checked)

        if checked:
            self.btn_c2_cancel.setToolTip(
                "Недоступно при удалении результата для замены новым"
            )
        else:
            self.btn_c2_cancel.setToolTip("")

    def _on_delete_jpg_toggled(self, checked: bool) -> None:
        if checked:
            confirmed = self._confirm_delete_conclusion_checkbox(checked)
            if not confirmed:
                self.chk_delete_jpg.blockSignals(True)
                self.chk_delete_jpg.setChecked(False)
                self.chk_delete_jpg.blockSignals(False)
                return

        self.btn_jpg_cancel.setEnabled(not checked)

        if checked:
            self.btn_jpg_cancel.setToolTip(
                "Недоступно при удалении результата для замены новым"
            )
        else:
            self.btn_jpg_cancel.setToolTip("")

    def pick_conclusion_jpg(self):
        if self.chk_delete_study.isChecked():
            self._warn(
                "При включённом режиме «Удалить исследование» "
                "выбор новых файлов недоступен."
            )
            return

        fn, _ = QFileDialog.getOpenFileName(
            self,
            "Выбор JPG заключения",
            self._default_jpg_dialog_dir(),
            "JPG (*.jpg *.jpeg)",
        )
        if not fn:
            return

        p = Path(fn)

        if p.suffix.lower() not in {".jpg", ".jpeg"}:
            QMessageBox.warning(
                self,
                "Неверный формат файла",
                "Выбран не JPG-файл.\nФайл не будет добавлен, выберите корректный JPG.",
                QMessageBox.Ok,
            )
            return

        self.conclusion_jpg_path = p
        self.lbl_jpg.setText(p.name)
        self.lbl_jpg.setToolTip(str(p))

    def clear_conclusion_jpg(self):
        self.conclusion_jpg_path = None
        self.lbl_jpg.setText("не выбрано")
        self.lbl_jpg.setToolTip("")

    def _selected_delete_classes(self) -> list[int]:
        classes: list[int] = []
        if self.chk_delete_c1.isChecked():
            classes.append(1)
        if self.chk_delete_c2.isChecked():
            classes.append(2)
        return classes

    def _has_any_class_action(self) -> bool:
        return bool(
            self.class1_path
            or self.class2_path
            or self.conclusion_jpg_path
            or self.chk_delete_c1.isChecked()
            or self.chk_delete_c2.isChecked()
            or self.chk_delete_study.isChecked()
            or self.chk_delete_jpg.isChecked()
        )

    def _try_get_current_patient_dir_for_conclusion_delete(self) -> Path | None:
        if not self._root_dir_available:
            return None

        organ = self.organ.currentText().strip()
        test_d = self.test_date.get_date()
        birth_d = self.birth_date.get_date()
        sex = self.sex.currentData()

        if not organ or test_d is None or birth_d is None or not sex:
            return None

        last_name = self.last_name.text().strip() or ""
        new_last_name = (
            self.new_last_name.text().strip() if self.new_last_name.isEnabled() else ""
        )
        first_name = self.first_name.text().strip()
        middle_name = self.middle_name.text().strip() or ""

        if not first_name:
            return None
        if not last_name and not new_last_name:
            return None

        locked_patient_dir = self._get_locked_patient_dir_from_autocomplete()
        if locked_patient_dir is not None:
            return locked_patient_dir

        organ_dir = self._effective_root_dir() / organ
        if not organ_dir.exists():
            return None

        search = build_patient_folder_search(
            organ_dir=organ_dir,
            last_name=last_name,
            new_last_name=new_last_name,
            first_name=first_name,
            middle_name=middle_name,
            birth_date=birth_d,
            sex=sex,
        )

        if search.exact_match_folder is not None:
            return search.exact_match_folder

        return None

    def _existing_class_file_for_current_test_folder(
        self, hla_class: int
    ) -> str | None:
        patient_dir = self._try_get_current_patient_dir_for_conclusion_delete()
        test_d = self.test_date.get_date()

        if patient_dir is None or test_d is None:
            return None

        file_name = class_result_file_name(hla_class)
        file_path = patient_dir / format_ddmmyyyy(test_d) / file_name

        if file_path.is_file():
            return file_name

        return None

    def _existing_conclusion_files_for_test_forlder(self) -> list[str]:
        patient_dir = self._try_get_current_patient_dir_for_conclusion_delete()
        test_d = self.test_date.get_date()

        if patient_dir is None or test_d is None:
            return []

        test_dir = patient_dir / format_ddmmyyyy(test_d)

        files = []
        for name in CONCLUSION_FILE_NAMES:
            if (test_dir / name).is_file():
                files.append(name)

        return files

    def _class_xlsx_will_remain_after_planned_actions(
        self,
        *,
        test_dir: Path,
        delete_classes: list[int],
    ) -> bool:
        """
        Возвращает True, если после уже запланированных удалений
        в папке исследования всё ещё останется хотя бы один class-xlsx.

        Используется для сценария импорта только JPG заключения:
        «скрининг.jpg» нельзя сохранять в папку, где после операции
        останутся результаты Class I / Class II.
        """
        if not test_dir.exists() or not test_dir.is_dir():
            return False

        classes_to_delete = set(delete_classes)

        for hla_class in (1, 2):
            xlsx_path = test_dir / class_result_file_name(hla_class)

            if not xlsx_path.is_file():
                continue

            if hla_class in classes_to_delete:
                continue

            return True

        return False

    def _find_import_conflicts(
        self,
        *,
        test_dir: Path,
        organ: str,
        patient_folder_name: str,
        test_date: date,
    ):
        source_dir = self._effective_root_dir() / organ / "source_files"

        return find_import_conflicts(
            test_dir=test_dir,
            source_dir=source_dir,
            organ=organ,
            patient_folder_name=patient_folder_name,
            analysis_date=test_date,
            class1_selected=self.class1_path is not None,
            class2_selected=self.class2_path is not None,
            conclusion_selected=self.conclusion_jpg_path is not None,
            delete_class1=self.chk_delete_c1.isChecked(),
            delete_class2=self.chk_delete_c2.isChecked(),
            delete_conclusion=self.chk_delete_jpg.isChecked(),
        )

    def _warn_import_conflicts(self, test_dir: Path, conflicts) -> None:
        study_lines: list[str] = []
        source_lines: list[str] = []

        test_dir_resolved = test_dir.resolve(strict=False)

        for conflict in conflicts:
            path = getattr(conflict, "path", None)
            if not path:
                continue

            path = Path(path)
            line = f"• {conflict.file_name}\n  {conflict.reason}\n  Путь: {path}"

            path_resolved = path.resolve(strict=False)

            try:
                path_resolved.relative_to(test_dir_resolved)
                study_lines.append(line)
                continue
            except ValueError:
                pass

            if "source_files" in path_resolved.parts:
                source_lines.append(line)

        parts: list[str] = []

        if study_lines:
            parts.append("Файлы в папке исследования:\n\n" + "\n\n".join(study_lines))

        if source_lines:
            parts.append("Файлы в папке source_files:\n\n" + "\n\n".join(source_lines))

        self._warn(
            "Найдены существующие файлы, которые конфликтуют с текущим импортом.\n"
            "Чтобы продолжить сначала удалите их через замену.\n\n" + "\n\n".join(parts)
        )

    def _has_selected_csvs(self) -> bool:
        return bool(self.class1_path or self.class2_path)

    def _normalize_csv_patient_compare_value(self, value: str) -> str:
        """
        Нормализация значения patient для безопасного сравнения между двумя CSV:
        - убираем лишние пробелы;
        - схлопываем множественные пробелы в один;
        - сравниваем без учёта регистра.

        Ничего более агрессивного здесь не делаем,
        чтобы не менять смысл данных.
        """
        return " ".join((value or "").split()).casefold()

    def _validate_selected_csv_pair_consistency(self) -> tuple[bool, str]:
        """
        Проверяет согласованность двух выбранных CSV перед импортом.

        Проверяем ТОЛЬКО:
        - patient
        - batch_date

        Если выбран только один CSV — проверка считается успешной.
        """
        if self.class1_path is None or self.class2_path is None:
            return True, ""

        try:
            parsed_class1 = parse_luminex_csv(self.class1_path)
        except Exception as e:
            return (
                False,
                "Не удалось повторно прочитать выбранный CSV Class I перед импортом.\n\n"
                f"Файл: {self.class1_path}\n\n"
                f"{e}",
            )

        try:
            parsed_class2 = parse_luminex_csv(self.class2_path)
        except Exception as e:
            return (
                False,
                "Не удалось повторно прочитать выбранный CSV Class II перед импортом.\n\n"
                f"Файл: {self.class2_path}\n\n"
                f"{e}",
            )

        patient1_raw = parsed_class1.patient or ""
        patient2_raw = parsed_class2.patient or ""

        patient1 = self._normalize_csv_patient_compare_value(patient1_raw)
        patient2 = self._normalize_csv_patient_compare_value(patient2_raw)

        batch_date1 = (parsed_class1.batch_date or "").strip()
        batch_date2 = (parsed_class2.batch_date or "").strip()

        mismatch_parts: list[str] = []

        if patient1 != patient2:
            mismatch_parts.append(
                "Имя пациента не совпадает:\n"
                f"• Class I: {patient1_raw or '(пусто)'}\n"
                f"• Class II: {patient2_raw or '(пусто)'}"
            )

        if batch_date1 != batch_date2:
            mismatch_parts.append(
                "Дата исследования не совпадает:\n"
                f"• Class I: {batch_date1 or '(пусто)'}\n"
                f"• Class II: {batch_date2 or '(пусто)'}"
            )

        if mismatch_parts:
            return (
                False,
                "Импорт запрещён: выбранные CSV Class I и Class II относятся к разным исследованиям.\n\n"
                + "\n\n".join(mismatch_parts)
                + "\n\n"
                + "Выберите два CSV одного пациента и с одинаковой датой исследования.",
            )

        return True, ""

    def _default_dialog_dir(self) -> str:
        prefs = load_effective_app_preferences()

        if prefs.dialog_dir is not None:
            return str(prefs.dialog_dir)

        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        return desktop or str(self._effective_root_dir())

    def _default_jpg_dialog_dir(self) -> str:
        prefs = load_effective_app_preferences()

        if prefs.jpg_dialog_dir is not None:
            return str(prefs.jpg_dialog_dir)

        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        return desktop or str(self._effective_root_dir())

    def _default_conclusion_save_dir(self) -> str:
        prefs = load_effective_app_preferences()

        if prefs.conclusion_save_dir is not None:
            return str(prefs.conclusion_save_dir)

        desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        return desktop or str(self._effective_root_dir())

    def pick_csv(self, hla_class: int):
        if self.chk_delete_study.isChecked():
            self._warn(
                "При включённом режиме «Удалить исследование» "
                "выбор новых файлов недоступен."
            )
            return

        fn, _ = QFileDialog.getOpenFileName(
            self, "Выбор CSV", self._default_dialog_dir(), "CSV (*.csv)"
        )
        if not fn:
            return

        p = Path(fn)
        expected = "I" if hla_class == 1 else "II"

        try:
            hla_class_str = parse_luminex_csv(p).hla_class
        except Exception as e:
            self._err(f"Не удалось прочитать CSV:\n{e}")
            return

        if hla_class_str not in ("I", "II"):
            QMessageBox.warning(
                self,
                "Не удалось определить Class",
                "В выбранном CSV не удалось определить Class (I/II).\n"
                "Выберите корректный файл.",
                QMessageBox.Ok,
            )
            return

        if hla_class_str != expected:
            QMessageBox.warning(
                self,
                "Неверный Class",
                f"Выбран файл Class {hla_class_str}, но ожидается Class {expected}.\n\n"
                "Выберите корректный файл.",
                QMessageBox.Ok,
            )
            return

        if hla_class == 1:
            self.class1_path = p
            self.lbl_c1.setText(p.name)
            self.lbl_c1.setToolTip(str(p))
        else:
            self.class2_path = p
            self.lbl_c2.setText(p.name)
            self.lbl_c2.setToolTip(str(p))

        self._refresh_avg_csv_label()

    def clear_csv(self, hla_class: int):
        removed_path: Path | None = None

        if hla_class == 1:
            removed_path = self.class1_path
            self.class1_path = None
            self.lbl_c1.setText("не выбрано")
        else:
            removed_path = self.class2_path
            self.class2_path = None
            self.lbl_c2.setText("не выбрано")

        if removed_path is not None and self.avg_csv_paths:
            removed_resolved = removed_path.resolve()
            self.avg_csv_paths = [
                p for p in self.avg_csv_paths if p.resolve() != removed_resolved
            ]

        self._refresh_avg_csv_label()

    def _refresh_avg_csv_label(self):
        if self.avg_csv_paths:
            paths = self.avg_csv_paths
        else:
            paths = []
            if self.class1_path:
                paths.append(self.class1_path)
            if self.class2_path:
                paths.append(self.class2_path)

        if not paths:
            self.lbl_avg_csv.setText("не выбрано")
            self.lbl_avg_csv.setToolTip("")
        elif len(paths) == 1:
            self.lbl_avg_csv.setText(paths[0].name)
            self.lbl_avg_csv.setToolTip(paths[0].name)
        else:
            self.lbl_avg_csv.setText(f"выбрано файлов: {len(paths)}")
            self.lbl_avg_csv.setToolTip("\n".join(p.name for p in paths))

    def pick_avg_csv(self):
        fns, _ = QFileDialog.getOpenFileNames(
            self,
            "Выбор CSV (можно выбрать несколько файлов)",
            self._default_dialog_dir(),
            "CSV (*.csv)",
        )
        if not fns:
            return

        self.avg_csv_paths = [Path(fn) for fn in fns]
        self._refresh_avg_csv_label()

    def _effective_avg_csv_paths(self) -> list[Path]:
        if self.avg_csv_paths:
            return [p for p in self.avg_csv_paths if p]

        fallback: list[Path] = []
        if self.class1_path:
            fallback.append(self.class1_path)
        if self.class2_path:
            fallback.append(self.class2_path)

        return fallback

    def _clear_avg_csv_selection(self):
        self.avg_csv_paths = []
        self._refresh_avg_csv_label()

    def _confirm_avg_threshold_input(self) -> None:
        self.ed_avg_min_titer.clearFocus()
        self.btn_avg_calc.setFocus()

    def calc_avg_excel(self):
        csv_paths = self._effective_avg_csv_paths()

        if not csv_paths:
            self._info_message(
                "Сначала выберите CSV в разделе «Титр антител к A, B и DRB1 антигенам» "
                "или в разделе «CSV-файлы анализатора и JPG заключения»."
            )
            return

        min_titer_text = (self.ed_avg_min_titer.text() or "").strip()
        min_titer = int(min_titer_text) if min_titer_text else 0

        try:
            result = build_avg_excels(csv_paths, min_titer=min_titer)
        except Exception as e:
            self._err(f"Не удалось вычислить:\n{e}")
            return

        outputs = result.outputs
        skipped = result.skipped_empty

        if not outputs and not skipped:
            self._warn("Не удалось создать файлы. Проверьте выбранные CSV.")
            return

        skipped_lines = [
            f"{item.csv_path.name} — Class {item.hla_class}: {item.reason}."
            for item in skipped
        ]

        if not outputs and skipped:
            self._clear_avg_csv_selection()
            self._info(
                "Во всех выбранных CSV после фильтрации не осталось данных для расчёта.\n\n"
                + "\n".join(skipped_lines)
            )
            return

        if outputs and skipped:
            self._clear_avg_csv_selection()
            self._info_with_explorer(
                "Обработка выполнена частично.\n\n"
                + "Пропущены файлы:\n"
                + "\n".join(skipped_lines)
                + "\n\n"
                + "Созданы файлы:\n"
                + "\n".join(str(path) for path in outputs),
                outputs,
            )
            return

        self._clear_avg_csv_selection()
        self._info_with_explorer(
            "Готово:\n" + "\n".join(str(path) for path in outputs),
            outputs,
        )

    def _process_non_input_events(self) -> None:
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _flush_qt_deferred_deletes(self) -> None:
        """
        Принудительно обрабатывает DeferredDelete-события Qt.
        Это нужно, чтобы deleteLater() реально уничтожил старые QObject,
        а не только поставил их в очередь.
        """
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _release_pending_fs_handles(self) -> None:
        """
        Перед критичным rename/delete на Windows:
        - форсируем сборку мусора Python;
        - даём Qt дообработать DeferredDelete;
        - даём системе короткое время отпустить файловые дескрипторы.
        """
        gc.collect()
        self._flush_qt_deferred_deletes()
        time.sleep(0.05)
        gc.collect()
        self._flush_qt_deferred_deletes()

    def _set_ui_locked_for_import(self, locked: bool) -> None:
        self.btn_settings.setEnabled(not locked)
        self.btn_about.setEnabled(not locked)
        self.btn_toggle_explorer.setEnabled(not locked and self._root_dir_available)

        self.root_explorer.setEnabled(not locked)

        self.test_date.setEnabled(not locked)
        self.organ.setEnabled(not locked)
        self.last_name.setEnabled(not locked)
        self.chk_new_lastname.setEnabled(not locked)
        self.new_last_name.setEnabled(not locked and self.chk_new_lastname.isChecked())
        self.first_name.setEnabled(not locked)
        self.middle_name.setEnabled(not locked)
        self.sex.setEnabled(not locked)
        self.birth_date.setEnabled(not locked)

        self.btn_c1.setEnabled(not locked and not self.chk_delete_study.isChecked())
        self.btn_c2.setEnabled(not locked and not self.chk_delete_study.isChecked())
        self.btn_jpg.setEnabled(not locked and not self.chk_delete_study.isChecked())

        self.btn_c1_cancel.setEnabled(not locked and not self.chk_delete_c1.isChecked())
        self.btn_c2_cancel.setEnabled(not locked and not self.chk_delete_c2.isChecked())
        self.btn_jpg_cancel.setEnabled(
            not locked and not self.chk_delete_jpg.isChecked()
        )

        self.chk_delete_c1.setEnabled(
            not locked and not self.chk_delete_study.isChecked()
        )
        self.chk_delete_c2.setEnabled(
            not locked and not self.chk_delete_study.isChecked()
        )
        self.chk_delete_jpg.setEnabled(
            not locked and not self.chk_delete_study.isChecked()
        )
        self.chk_delete_study.setEnabled(not locked)

        self.rb_concl_mnpc.setEnabled(not locked)
        self.rb_concl_rnpc.setEnabled(not locked)
        self.concl_num_register.setEnabled(not locked)
        self.concl_head_of.setEnabled(not locked)
        self.concl_acting.setEnabled(not locked)
        self.concl_bio1.setEnabled(not locked)
        self.concl_doctor.setEnabled(not locked)
        self.concl_bio2.setEnabled(not locked)
        self.concl_screening.setEnabled(not locked)
        self.btn_concl_save.setEnabled(not locked)
        self.btn_concl_print.setEnabled(not locked)

        self.ed_avg_min_titer.setEnabled(not locked)
        self.btn_avg_pick.setEnabled(not locked)
        self.btn_avg_calc.setEnabled(not locked)
        self.btn_avg_cancel.setEnabled(not locked)

    def _begin_import_busy_state(self, text: str) -> None:
        self._import_in_progress = True
        self._set_ui_locked_for_import(True)
        self.btn_run.setEnabled(False)
        self.btn_run.setText("ИМПОРТ...")
        self.status.setText(text)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._process_non_input_events()

    def _end_import_busy_state(self) -> None:
        self._import_in_progress = False
        self._set_ui_locked_for_import(False)
        self.btn_run.setEnabled(True)
        self.btn_run.setText("ИМПОРТИРОВАТЬ")

        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

        QTimer.singleShot(0, self._resume_file_tree_index_updates_after_import)

    def _err(self, msg: str):
        QMessageBox.critical(self, "Ошибка", msg)

    def _warn(self, msg: str):
        QMessageBox.warning(self, "Внимание", msg)

    def _info_message(self, msg: str):
        QMessageBox.information(self, "Информация", msg)

    def _ask_password(
        self,
        *,
        title: str = "Подтверждение",
        prompt: str = "Введите пароль:",
    ) -> bool:
        while True:
            password, ok = QInputDialog.getText(
                self,
                title,
                prompt,
                QLineEdit.Password,
            )

            if not ok:
                return False

            if password == APP_PASSWORD:
                return True

            self._warn("Неверный пароль.")

    def _open_paths_in_explorer(self, paths: list[Path]) -> None:
        existing = [Path(p) for p in paths if p and Path(p).exists()]
        if not existing:
            raise RuntimeError("Созданные файлы не найдены.")

        if not sys.platform.startswith("win"):
            raise RuntimeError("Открытие проводника реализовано только для Windows.")

        if len(existing) == 1 and existing[0].is_file():
            subprocess.Popen(["explorer", "/select,", str(existing[0])])
            return

        parent_dirs = {p.parent.resolve() for p in existing}
        if len(parent_dirs) == 1:
            folder = next(iter(parent_dirs))
            os.startfile(str(folder))
            return

        os.startfile(str(existing[0].parent))

    def _navigate_after_import(self, patient_dir: Path, test_date: date) -> None:
        if not patient_dir:
            return

        test_dir = patient_dir / format_ddmmyyyy(test_date)
        target_path = test_dir if test_dir.exists() else patient_dir

        QTimer.singleShot(
            100,
            lambda p=target_path: self.root_explorer._navigate_to_path(p),
        )

    def _info_with_explorer(self, msg: str, paths: list[Path]) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Готово")
        box.setText(msg)

        open_btn = box.addButton("Открыть в проводнике", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.setDefaultButton(open_btn)

        box.exec()

        if box.clickedButton() is open_btn:
            try:
                self._open_paths_in_explorer(paths)
            except Exception as e:
                self._err(f"Не удалось открыть проводник:\n{e}")

    def _info(self, msg: str):
        QMessageBox.information(self, "Готово", msg)

    def show_about_dialog(self) -> None:
        QMessageBox.information(
            self,
            "О программе",
            (
                f"{__title__}\n"
                f"Версия: {__version__}\n\n"
                f"Автор и правообладатель: {__author__}\n"
                f"Контакт: {__email__}\n"
                f"{__copyright__}\n"
                f"Лицензия собственного кода: {__license__}\n\n"
                "Сторонние лицензии см. в файлах:\n"
                "THIRD_PARTY_LICENSES.txt и папке licenses/"
            ),
        )

    def _ask_negative_conclusion_without_csv(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Сформировать заключение")
        box.setText(
            "Ни одного CSV Class I или Class II не выбрано.\n\n"
            "Вы хотите сформировать отрицательное заключение скрининга?"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        return box.exec() == QMessageBox.Yes

    def on_toggle_new_lastname(self, checked: bool):
        self.new_last_name.setEnabled(checked)
        if not checked:
            self.new_last_name.setText("")
        else:
            self.new_last_name.setFocus()
            self.new_last_name.selectAll()

    def _normalize_names(self):
        self.last_name.setText(cap_hyphenated_lastname(self.last_name.text()))

        if self.new_last_name.isEnabled():
            self.new_last_name.setText(
                cap_hyphenated_lastname(self.new_last_name.text())
            )
            if normalize_for_compare(self.last_name.text()) == normalize_for_compare(
                self.new_last_name.text()
            ):
                self.new_last_name.setText("")
        else:
            self.new_last_name.setText("")

        self.first_name.setText(cap_ru(self.first_name.text()))
        self.middle_name.setText(cap_ru(self.middle_name.text()))

    def _reset_after_success(self):
        today = date.today()

        # Данные пациента
        self.test_date.set_date(today)
        self.birth_date.clear_date()
        self.organ.setCurrentIndex(-1)

        self.last_name.setText("")
        self.chk_new_lastname.setChecked(False)
        self.new_last_name.setText("")
        self.first_name.setText("")
        self.middle_name.setText("")
        self.sex.setCurrentIndex(-1)

        # CSV-файлы анализатора и JPG заключения
        self.clear_csv(1)
        self.clear_csv(2)
        self.clear_conclusion_jpg()
        self.chk_delete_c1.setChecked(False)
        self.chk_delete_c2.setChecked(False)
        self.chk_delete_study.setChecked(False)
        self.chk_delete_jpg.setChecked(False)

        # При желании можно также очистить выбор в блоке суммарного титра
        self._clear_avg_csv_selection()

        self.last_name.setFocus()

        self._clear_selected_patient_folder_choice()

    def validate_inputs(self) -> tuple[bool, str]:
        ok, msg = self.test_date.validate()
        if not ok:
            return False, "Дата поступления материала: " + msg

        organ = self.organ.currentText().strip()
        if not organ:
            return False, "Выберите орган"

        ln = self.last_name.text().strip()
        nln = (
            self.new_last_name.text().strip() if self.new_last_name.isEnabled() else ""
        )

        if not ln and not nln:
            return False, "Заполните Фамилию или включите и заполните Новую фамилию"

        if ln and not is_valid_ru_lastname(ln):
            return (
                False,
                "Фамилия: первые 2 символа должны быть буквами, далее буквы/дефис, минимум 2 буквы",
            )

        if nln and not is_valid_ru_lastname(nln):
            return (
                False,
                "Новая фамилия: первые 2 символа должны быть буквами, далее буквы/дефис, минимум 2 буквы",
            )

        fn = self.first_name.text().strip()
        if not is_valid_ru_lastname(fn):
            return (
                False,
                "Имя: первые 2 символа должны быть буквами, далее буквы/дефис, минимум 2 буквы",
            )

        mn = self.middle_name.text().strip()
        if mn and not is_valid_ru_name(mn):
            return (
                False,
                "Отчество: только русские буквы, минимум 2 символа (или пусто)",
            )

        sex = self.sex.currentData()
        if not sex:
            return False, "Укажите пол"

        ok, msg = self.birth_date.validate()
        if not ok:
            return False, "Дата рождения: " + msg

        if self.chk_delete_study.isChecked() and (
            self.class1_path or self.class2_path or self.conclusion_jpg_path
        ):
            return (
                False,
                "При включённом «Удалить исследование» нельзя выбирать новые файлы.",
            )

        if not self._has_any_class_action():
            return (
                False,
                "Выберите хотя бы один CSV или JPG-файл для импорта "
                "или отметьте «Удалить исследование».",
            )

        return True, ""

    def _collect_import_form_state(self) -> ImportFormState:
        root_dir = self._effective_root_dir()
        organ = self.organ.currentText()
        test_date = self.test_date.get_date()
        birth_date = self.birth_date.get_date()

        assert test_date is not None
        assert birth_date is not None

        return ImportFormState(
            root_dir=root_dir,
            organ=organ,
            test_date=test_date,
            birth_date=birth_date,
            last_name=self.last_name.text().strip() or "",
            new_last_name=(
                self.new_last_name.text().strip()
                if self.new_last_name.isEnabled()
                else ""
            ),
            first_name=self.first_name.text().strip(),
            middle_name=self.middle_name.text().strip() or "",
            sex=self.sex.currentData(),
            delete_classes=self._selected_delete_classes(),
            delete_conclusion=self.chk_delete_jpg.isChecked(),
            delete_whole_study=self.chk_delete_study.isChecked(),
            has_csv_selection=self._has_selected_csvs(),
            has_jpg_selection=self.conclusion_jpg_path is not None,
        )

    def _resolve_patient_target(
        self,
        state: ImportFormState,
        organ_dir: Path,
    ) -> ResolvedPatientTarget | None:
        target = ResolvedPatientTarget()

        locked_patient_dir = self._get_locked_patient_dir_from_autocomplete()
        if locked_patient_dir is not None:
            target.current_patient_dir = locked_patient_dir
            target.target_patient_dir = locked_patient_dir
            target.old_patient_code_for_db = build_db_patient_code(
                state.organ,
                locked_patient_dir.name,
            )
            target.used_existing_patient_dir = True

            folder_birth = split_patient_folder_name(locked_patient_dir.name)[2]
            if folder_birth:
                target.preserve_existing_patient_in_db = True

            if not self._confirm_birth_date_mismatch_for_folder(
                locked_patient_dir,
                state.birth_date,
            ):
                return None

            return target

        search = build_patient_folder_search(
            organ_dir=organ_dir,
            last_name=state.last_name,
            new_last_name=state.new_last_name,
            first_name=state.first_name,
            middle_name=state.middle_name,
            birth_date=state.birth_date,
            sex=state.sex,
        )

        if search.exact_match_folder is not None:
            target.current_patient_dir = search.exact_match_folder
            target.target_patient_dir = search.exact_match_folder
            target.old_patient_code_for_db = build_db_patient_code(
                state.organ,
                search.exact_match_folder.name,
            )
            target.used_existing_patient_dir = True

            folder_birth = split_patient_folder_name(search.exact_match_folder.name)[2]
            if folder_birth:
                target.preserve_existing_patient_in_db = True

            if not self._confirm_birth_date_mismatch_for_folder(
                search.exact_match_folder,
                state.birth_date,
            ):
                return None

            return target

        if search.options:
            items = [
                (option.label, option.folder_name, option.is_new_format)
                for option in search.options
            ]

            dialog = MatchDialogAny(
                items=items,
                desired_name=search.desired_patient_name,
                parent=self,
            )
            if dialog.exec() != QDialog.Accepted:
                return None

            assert dialog.choice is not None
            kind, folder_name, do_rename = dialog.choice

            if kind == "new":
                if state.has_delete_actions:
                    self._warn(
                        "Удаление исследования или заключения возможно только "
                        "при использовании существующей папки пациента без переименования."
                    )
                    return None

                target.create_new_after_password = True
                target.create_new_kwargs = {
                    "organ_dir": organ_dir,
                    "last_name": state.base_last_name,
                    "new_last_name": state.bracket_last_name,
                    "first_name": state.first_name,
                    "middle_name": state.middle_name,
                    "birth_date": state.birth_date,
                    "sex": state.sex,
                }
                target.target_patient_dir = organ_dir / search.desired_patient_name
                return target

            assert folder_name is not None
            current_patient_dir = organ_dir / folder_name
            folder_birth = split_patient_folder_name(current_patient_dir.name)[2]

            if folder_birth and not do_rename:
                target.preserve_existing_patient_in_db = True
                if not self._confirm_birth_date_mismatch_for_folder(
                    current_patient_dir,
                    state.birth_date,
                ):
                    return None

            if do_rename:
                target.rename_after_password = True
                target.current_patient_dir = current_patient_dir
                target.target_patient_dir = (
                    current_patient_dir.parent / search.desired_patient_name
                )
                target.rename_target_patient_name = search.desired_patient_name
                target.old_patient_code_for_db = build_db_patient_code(
                    state.organ,
                    current_patient_dir.name,
                )
                target.new_patient_code_for_db = build_db_patient_code(
                    state.organ,
                    search.desired_patient_name,
                )
                target.used_existing_patient_dir = True
                return target

            target.current_patient_dir = current_patient_dir
            target.target_patient_dir = current_patient_dir
            target.old_patient_code_for_db = build_db_patient_code(
                state.organ,
                current_patient_dir.name,
            )
            target.used_existing_patient_dir = True
            return target

        if state.has_delete_actions:
            self._warn(
                "Удаление исследования или заключения возможно только "
                "для уже существующего пациента.\n\n"
                "Совпадающая папка пациента не найдена."
            )
            return None

        target.create_new_after_password = True
        target.create_new_kwargs = {
            "organ_dir": organ_dir,
            "last_name": state.base_last_name,
            "new_last_name": state.bracket_last_name,
            "first_name": state.first_name,
            "middle_name": state.middle_name,
            "birth_date": state.birth_date,
            "sex": state.sex,
        }
        target.target_patient_dir = organ_dir / search.desired_patient_name
        return target

    def _confirm_rename_patient_target(
        self,
        *,
        state: ImportFormState,
        target: ResolvedPatientTarget,
    ) -> bool:
        if not target.rename_after_password:
            return True

        assert target.current_patient_dir is not None
        assert target.rename_target_patient_name is not None

        current_patient_dir = target.current_patient_dir
        folder_birth = split_patient_folder_name(current_patient_dir.name)[2]

        extra = ""
        if folder_birth and folder_birth != format_ddmmyyyy(state.birth_date):
            extra = (
                "\n\nВНИМАНИЕ: дата рождения в названии выбранной папки "
                "отличается от введённой.\n"
                f"В папке: {folder_birth}\n"
                f"Введено: {format_ddmmyyyy(state.birth_date)}\n"
            )

        if (
            QMessageBox.question(
                self,
                "Подтверждение",
                (
                    f"Переименовать папку:\n{current_patient_dir.name}\n"
                    f"→ {target.rename_target_patient_name} ?{extra}"
                ),
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return False

        warn_text = (
            "ВНИМАНИЕ!\n\n"
            "Будет изменён существующий пациент:\n"
            "• папка пациента будет переименована\n"
            "• будет обновлён patient_code в PostgreSQL\n"
            "• будут обновлены данные пациента (ФИО/пол/дата рождения)\n\n"
            "Если данные введены неправильно, это может привести к "
            "несоответствию информации в базе.\n\n"
            "Продолжить?"
        )

        return (
            QMessageBox.warning(
                self,
                "Подтвердите изменение данных",
                warn_text,
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            == QMessageBox.Yes
        )

    def _create_new_patient_folder_after_password(
        self,
        *,
        state: ImportFormState,
        target: ResolvedPatientTarget,
    ) -> None:
        if not target.create_new_after_password:
            return

        assert target.create_new_kwargs is not None

        created_dir = create_new_patient_folder(**target.create_new_kwargs)
        target.current_patient_dir = created_dir
        target.target_patient_dir = created_dir
        target.old_patient_code_for_db = build_db_patient_code(
            state.organ,
            created_dir.name,
        )
        target.create_new_after_password = False

    def _rename_patient_folder_with_retry(
        self,
        patient_dir: Path,
        new_patient_folder_name: str,
        *,
        attempts: int = 5,
    ) -> Path:
        """
        Пытается переименовать папку пациента несколько раз.
        Это защита от кратковременных ложных блокировок на Windows,
        когда файловый дескриптор отпускается не мгновенно.
        """
        last_error: Exception | None = None

        self._release_pending_fs_handles()

        for attempt in range(attempts):
            try:
                return rename_patient_folder(
                    patient_dir,
                    new_patient_folder_name,
                )
            except Exception as e:
                last_error = e

                if attempt >= attempts - 1:
                    raise

                self._release_pending_fs_handles()

        assert last_error is not None
        raise last_error

    def _rename_patient_after_success(
        self,
        *,
        state: ImportFormState,
        target: ResolvedPatientTarget,
        src_dir: Path,
        snapshot,
    ) -> None:
        if not target.rename_after_password:
            return

        assert target.current_patient_dir is not None
        assert target.target_patient_dir is not None
        assert target.rename_target_patient_name is not None
        assert target.old_patient_code_for_db is not None

        old_patient_dir = target.current_patient_dir
        old_folder_name = old_patient_dir.name

        if not self._prepare_ui_for_patient_dir_rename():
            raise RuntimeError(
                "Не удалось безопасно подготовить файловую базу к переименованию "
                "папки пациента. Дождитесь завершения фоновых задач и повторите "
                "попытку."
            )
        try:
            try:
                renamed_patient_dir = self._rename_patient_folder_with_retry(
                    old_patient_dir,
                    target.rename_target_patient_name,
                )
                if snapshot is not None:
                    snapshot.renamed_patient_dir = renamed_patient_dir
            except Exception as e:
                raise RuntimeError(
                    f"Не удалось переименовать папку пациента: {e}"
                ) from e

            try:
                rename_source_files_for_patient(
                    src_dir=src_dir,
                    organ=state.organ,
                    old_patient_folder_name=old_folder_name,
                    new_patient_folder_name=renamed_patient_dir.name,
                )
            except Exception as e:
                try:
                    self._rename_patient_folder_with_retry(
                        renamed_patient_dir,
                        old_folder_name,
                    )
                except Exception as rollback_error:
                    raise RuntimeError(
                        "Не удалось переименовать файлы пациента в source_files, "
                        "и не удалось откатить переименование папки пациента.\n\n"
                        f"Ошибка source_files: {e}\n"
                        f"Ошибка отката: {rollback_error}"
                    ) from rollback_error

                raise RuntimeError(
                    f"Не удалось переименовать файлы пациента в source_files: {e}"
                ) from e

            target.new_patient_code_for_db = build_db_patient_code(
                state.organ,
                renamed_patient_dir.name,
            )

            try:
                updated_patient_id = rename_existing_patient_record(
                    current_patient_code=target.old_patient_code_for_db,
                    new_patient_code=target.new_patient_code_for_db,
                    last_name=state.base_last_name,
                    new_last_name=state.bracket_last_name or None,
                    first_name=state.first_name,
                    middle_name=state.middle_name or None,
                    sex=state.sex,
                    birth_date=state.birth_date,
                )

                if updated_patient_id is not None and snapshot is not None:
                    snapshot.renamed_patient_code = target.new_patient_code_for_db
            except Exception as e:
                try:
                    rename_source_files_for_patient(
                        src_dir=src_dir,
                        organ=state.organ,
                        old_patient_folder_name=renamed_patient_dir.name,
                        new_patient_folder_name=old_folder_name,
                    )
                    self._rename_patient_folder_with_retry(
                        renamed_patient_dir,
                        old_folder_name,
                    )
                except Exception as rollback_error:
                    raise RuntimeError(
                        "Не удалось обновить patient_code в PostgreSQL, "
                        "и не удалось полностью откатить переименование пациента.\n\n"
                        f"Ошибка PostgreSQL: {e}\n"
                        f"Ошибка отката: {rollback_error}"
                    ) from rollback_error

                raise RuntimeError(
                    f"Не удалось обновить данные пациента в PostgreSQL: {e}"
                ) from e

            target.current_patient_dir = renamed_patient_dir
            target.target_patient_dir = renamed_patient_dir
            target.old_patient_code_for_db = target.new_patient_code_for_db
            target.new_patient_code_for_db = None
            target.rename_after_password = False
            target.rename_target_patient_name = None
            target.preserve_existing_patient_in_db = True

        finally:
            self._restore_ui_after_patient_dir_rename()

    def _cleanup_created_patient_folder_on_rollback(
        self,
        patient_dir: Path,
    ) -> Exception | None:
        try:
            if patient_dir.exists() and patient_dir.is_dir():
                fs_delete_dir_tree(patient_dir)
        except Exception as exc:
            return exc

        return None

    def _build_patient_data(
        self,
        state: ImportFormState,
        target: ResolvedPatientTarget,
    ) -> PatientData:
        patient_code = (
            target.old_patient_code_for_db
            if target.old_patient_code_for_db is not None
            else build_db_patient_code(
                state.organ,
                target.patient_dir_for_operations().name,
            )
        )

        return PatientData(
            organ=state.organ,
            patient_code=patient_code,
            last_name=state.base_last_name,
            new_last_name=state.bracket_last_name or None,
            first_name=state.first_name,
            middle_name=state.middle_name or None,
            sex=state.sex,
            birth_date=state.birth_date,
        )

    def _build_import_success_message(
        self,
        *,
        state: ImportFormState,
        deleted_conclusion_files_count: int,
    ) -> str:
        if (
            state.delete_classes
            and state.delete_conclusion  # намерение замены в UI
            and deleted_conclusion_files_count > 0  # реальный факт замены
            and state.has_csv_selection
            and state.has_jpg_selection
        ):
            return "Замена старого исследования новым выполнена"

        if (
            state.delete_classes
            and state.delete_conclusion
            and deleted_conclusion_files_count > 0
            and not state.has_csv_selection
            and state.has_jpg_selection
        ):
            return "Замена исследования на отрицательный скрининг выполнена"

        if (
            state.delete_conclusion
            and deleted_conclusion_files_count > 0
            and state.has_jpg_selection
        ):
            return "Замена старого скрининга новым выполнена"

        if state.has_csv_selection and state.has_jpg_selection:
            return "Импорт исследования выполнен успешно"

        if state.has_jpg_selection:
            return "Импорт отрицательного скрининга выполнен"

        return "⚠️ Неожиданное состояние импорта!"

    def run_import(self):
        if self._import_in_progress:
            self.status.setText(
                "⏳ Импорт уже выполняется. Дождитесь завершения текущей операции."
            )
            return

        ok, message = self.validate_inputs()
        if not ok:
            self._info_message(message)
            return

        csv_pair_ok, csv_pair_error = self._validate_selected_csv_pair_consistency()
        if not csv_pair_ok:
            self._warn(csv_pair_error)
            return

        final_status: str | None = None
        snapshot = None
        created_patient_dir = False
        selected_patient_dir: Path | None = None

        self._begin_import_busy_state("⏳ Выполняется поключение к файловой базе...")

        try:
            if not self._ensure_root_dir_available_for_import():
                return

            self.status.setText(
                "⏳ Подключение к файловой базе выполнено. Проверяется подключение к PostgreSQL..."
            )
            self._process_non_input_events()

            if not self._ensure_postgres_available_for_import():
                return

            self.status.setText(
                "⏳ Подключение к PostgreSQL выполнено. Подготовка исследования..."
            )
            self._process_non_input_events()

            if not self._wait_for_file_tree_index_build_to_finish():
                self._warn(
                    "Фоновое обновление локального индекса не завершилось вовремя.\n\n"
                    "Импорт остановлен, чтобы избежать зависания или конфликтов с "
                    "файловой базой. Дождитесь завершения индексации и повторите "
                    "попытку."
                )
                return

            self._normalize_names()
            state = self._collect_import_form_state()

            if (
                state.has_delete_actions
                and not state.has_file_import
                and not state.delete_whole_study
            ):
                self._warn(
                    "Не выбран файл для замены.\n\n"
                    "Операция удаления отдельных результатов или заключения без замены "
                    "не разрешается. Выберите новые файлы для замены или используйте "
                    "режим «Удалить исследование»."
                )
                return

            if state.has_csv_selection and not state.has_jpg_selection:
                self._warn(
                    "Импорт отдельных результатов Class I/II без заключения не разрешается, "
                    "так как на их основе должно был сформировано последнее.\n\n"
                    "Выберите соответствующий данному исследованию файл заключения."
                )
                return

            organ_dir, src_dir = get_base_tree_paths(state.root_dir, state.organ)
            target = self._resolve_patient_target(state, organ_dir)
            if target is None:
                return

            test_dir_path = target.patient_dir_for_existing_checks() / format_ddmmyyyy(
                state.test_date
            )

            if (
                state.has_jpg_selection
                and not state.has_csv_selection
                and self._class_xlsx_will_remain_after_planned_actions(
                    test_dir=test_dir_path,
                    delete_classes=state.delete_classes,
                )
            ):
                self._warn(
                    "В папке исследования есть результаты Class I и/или Class II.\n\n"
                    f"Заменить положительное исследование на «{NEGATIVE_CONCLUSION_FILE_NAME}» "
                    "можно только после удаления "
                    f"«{class_result_file_name(1)}» и/или «{class_result_file_name(2)}» \n\n"
                    "Если вы хотите заменить текущее исследование отрицательным результаом скрининга, "
                    "дополнительно отметьте «Заменить Class I» и/или «Заменить Class II»."
                )
                return

            if state.delete_whole_study:
                if not test_dir_path.exists() or not test_dir_path.is_dir():
                    self._warn(
                        "Исследование за выбранную дату не найдено.\n\n"
                        "Полное удаление без замены невозможно."
                    )
                    return

                if not self._confirm_delete_whole_study(test_dir_path):
                    return

            if state.has_file_import:
                existing_checks_patient_dir = target.patient_dir_for_existing_checks()

                conflicts = self._find_import_conflicts(
                    test_dir=test_dir_path,
                    organ=state.organ,
                    patient_folder_name=existing_checks_patient_dir.name,
                    test_date=state.test_date,
                )
                if conflicts:
                    self._warn_import_conflicts(test_dir_path, conflicts)
                    return

            has_only_one_csv = state.has_csv_selection and (
                (self.class1_path is not None) ^ (self.class2_path is not None)
            )

            if has_only_one_csv and state.has_jpg_selection:
                existing_other_class = None

                if self.class1_path is not None:
                    if 2 not in state.delete_classes:
                        existing_other_class = (
                            self._existing_class_file_for_current_test_folder(2)
                        )
                else:
                    if 1 not in state.delete_classes:
                        existing_other_class = (
                            self._existing_class_file_for_current_test_folder(1)
                        )

                if existing_other_class is not None:
                    self._warn(
                        "В папке исследования присутствуют файлы обоих классов (Class I и Class II).\n\n"
                        "Для замены выбранного исследование результатом одного класса, "
                        f"отметьте к удаления (замены) файл «{existing_other_class}»."
                    )
                    return

            if not self._confirm_rename_patient_target(
                state=state,
                target=target,
            ):
                return

            if not self._ask_password(title="Подтверждение выполнения операции"):
                return

            self.status.setText("⏳ Выполняется обработка исследования...")
            self._process_non_input_events()

            if target.create_new_after_password:
                self._create_new_patient_folder_after_password(
                    state=state,
                    target=target,
                )
                created_patient_dir = True

            selected_patient_dir = target.patient_dir_for_operations()

            patient_code_for_snapshot = (
                target.old_patient_code_for_db
                if target.old_patient_code_for_db is not None
                else build_db_patient_code(state.organ, selected_patient_dir.name)
            )

            touched_classes = sorted(
                set(
                    (
                        [1, 2]
                        if state.delete_whole_study
                        else (
                            state.delete_classes
                            + ([1] if self.class1_path is not None else [])
                            + ([2] if self.class2_path is not None else [])
                        )
                    )
                )
            )
            include_conclusion = (
                state.delete_whole_study
                or state.delete_conclusion
                or state.has_jpg_selection
            )

            snapshot = capture_study_state_snapshot(
                root_dir=state.root_dir,
                organ=state.organ,
                patient_code=patient_code_for_snapshot,
                patient_dir=selected_patient_dir,
                test_date=state.test_date,
                classes=touched_classes,
                include_conclusion=include_conclusion,
                include_all_patient_source_files=target.rename_after_password,
                include_full_test_dir_tree=state.delete_whole_study,
            )

            # ВАЖНО:
            # Папку пациента переименовываем ДО импорта файлов.
            # Так мы не пытаемся rename'ить каталог сразу после записи Excel/JPG/CSV,
            # что на Windows сильно повышает вероятность ложных PermissionError.
            if target.rename_after_password and not state.delete_whole_study:
                self._rename_patient_after_success(
                    state=state,
                    target=target,
                    src_dir=src_dir,
                    snapshot=snapshot,
                )
                selected_patient_dir = target.final_patient_dir()

            patient_code_for_ops = (
                target.old_patient_code_for_db
                if target.old_patient_code_for_db is not None
                else build_db_patient_code(state.organ, selected_patient_dir.name)
            )

            if state.delete_whole_study:
                delete_entire_study(
                    root_dir=state.root_dir,
                    organ=state.organ,
                    patient_code=patient_code_for_ops,
                    patient_dir=selected_patient_dir,
                    test_date=state.test_date,
                )

                cleanup_study_state_snapshot(snapshot)
                snapshot = None

                self._refresh_root_explorer_after_import()
                self._navigate_after_import(selected_patient_dir, state.test_date)
                self._schedule_file_tree_index_rebuild(force=True)

                final_status = f"🗑️ Удалено: {selected_patient_dir / format_ddmmyyyy(state.test_date)}"
                self._info("Исследование удалено успешно")
                self._reset_after_success()
                return

            replacing_classes: list[int] = []
            if self.class1_path is not None and 1 in state.delete_classes:
                replacing_classes.append(1)
            if self.class2_path is not None and 2 in state.delete_classes:
                replacing_classes.append(2)

            pure_delete_classes = [
                hla_class
                for hla_class in state.delete_classes
                if hla_class not in replacing_classes
            ]

            replacing_conclusion = (
                state.delete_conclusion
                and state.has_jpg_selection
                and not state.has_csv_selection
            )

            pure_delete_conclusion = (
                state.delete_conclusion and not state.has_jpg_selection
            )

            deleted_conclusion_files_count = 0
            imported_test_dir = selected_patient_dir / format_ddmmyyyy(state.test_date)

            if state.has_csv_selection:
                result = do_import(
                    root_dir=state.root_dir,
                    patient=self._build_patient_data(state, target),
                    inp=ImportInput(
                        patient_dir=selected_patient_dir,
                        test_date=state.test_date,
                        class1_csv=self.class1_path,
                        class2_csv=self.class2_path,
                        overwrite_existing=True,
                        conclusion_jpg=self.conclusion_jpg_path,
                        preserve_existing_patient_in_db=target.preserve_existing_patient_in_db,
                    ),
                )
                imported_test_dir = result.test_dir

            if pure_delete_classes:
                delete_existing_results(
                    root_dir=state.root_dir,
                    organ=state.organ,
                    patient_code=patient_code_for_ops,
                    patient_dir=selected_patient_dir,
                    test_date=state.test_date,
                    classes=pure_delete_classes,
                )

            if pure_delete_conclusion or replacing_conclusion:
                deleted_conclusion_files, _ = delete_conclusion_jpg(
                    patient_dir=selected_patient_dir,
                    test_date=state.test_date,
                )
                deleted_conclusion_files_count += len(deleted_conclusion_files)

            if state.has_jpg_selection and not state.has_csv_selection:
                assert self.conclusion_jpg_path is not None

                save_conclusion_jpg(
                    patient_dir=selected_patient_dir,
                    test_date=state.test_date,
                    jpg_path=self.conclusion_jpg_path,
                    negative_screening=True,
                )

            cleanup_study_state_snapshot(snapshot)
            snapshot = None

            self._refresh_root_explorer_after_import()
            self._navigate_after_import(selected_patient_dir, state.test_date)
            self._schedule_file_tree_index_rebuild(force=True)

            final_status = f"✅ Готово: {imported_test_dir}"
            self._info(
                self._build_import_success_message(
                    state=state,
                    deleted_conclusion_files_count=deleted_conclusion_files_count,
                )
            )
            self._reset_after_success()

        except Exception as e:
            rollback_error: Exception | None = None

            if snapshot is not None:
                try:
                    restore_study_state_snapshot(snapshot)
                except Exception as rollback_exc:
                    rollback_error = rollback_exc
                finally:
                    cleanup_study_state_snapshot(snapshot)
                    snapshot = None

            if created_patient_dir and selected_patient_dir is not None:
                cleanup_error = self._cleanup_created_patient_folder_on_rollback(
                    selected_patient_dir
                )
                if cleanup_error is not None:
                    cleanup_runtime_error = RuntimeError(
                        "Не удалось удалить созданную при импорте папку пациента: "
                        f"{selected_patient_dir}\n\n{cleanup_error}"
                    )
                    if rollback_error is None:
                        rollback_error = cleanup_runtime_error
                    else:
                        rollback_error = RuntimeError(
                            f"{rollback_error}\n\nТакже:\n{cleanup_runtime_error}"
                        )

            self._refresh_root_explorer_after_import()
            self._schedule_file_tree_index_rebuild(force=True)

            if rollback_error is not None:
                self._err(
                    "Операция не выполнена.\n\n"
                    f"Основная ошибка:\n{e}\n\n"
                    "Кроме того, не удалось полностью выполнить откат изменений:\n"
                    f"{rollback_error}"
                )
            else:
                self._err(f"Операция не выполнена:\n{e}")

            self._clear_selected_patient_folder_choice()
            return

        finally:
            self._end_import_busy_state()

            if final_status is not None:
                self.status.setText(final_status)
            elif self.status.text().startswith("⏳"):
                self.status.setText("❌ Операция не выполнена.")

    def _setup_staff_combo(
        self, cb: QComboBox, items: list[str], default: str = ""
    ) -> None:
        cb.setEditable(True)
        cb.addItems(items)
        cb.setCurrentText(default)

        le = cb.lineEdit()
        if le is None:
            return

        rx_staff = QRegularExpression(
            r"^(?:|[А-Яа-яЁё]{2,}(?:-[А-Яа-яЁё]+)?(?: [А-Яа-яЁё](?:\.? ?[А-Яа-яЁё]\.?)?)?)$"
        )
        v = QRegularExpressionValidator(rx_staff, le)
        le.setValidator(v)
        le.editingFinished.connect(lambda cb=cb: self._normalize_staff_combo(cb))

    def _normalize_staff_combo(self, cb: QComboBox) -> None:
        le = cb.lineEdit()
        if le is None:
            return
        cb.setEditText(normalize_staff_name(le.text()))

    def _set_button_checked_silently(self, button, checked: bool) -> None:
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)

    def _set_checkbox_silently(self, checkbox: QCheckBox, checked: bool) -> None:
        checkbox.blockSignals(True)
        checkbox.setChecked(checked)
        checkbox.blockSignals(False)

    def _set_import_selection_enabled(self, enabled: bool) -> None:
        for btn in (
            self.btn_c1,
            self.btn_c2,
            self.btn_jpg,
            self.btn_c1_cancel,
            self.btn_c2_cancel,
            self.btn_jpg_cancel,
        ):
            btn.setEnabled(enabled)

    def _on_delete_study_whole_toggled(self, checked: bool) -> None:
        for cb in (self.chk_delete_c1, self.chk_delete_c2, self.chk_delete_jpg):
            cb.setEnabled(not checked)

        if checked:
            had_selected_files = any(
                (self.class1_path, self.class2_path, self.conclusion_jpg_path)
            )

            if had_selected_files:
                self.clear_csv(1)
                self.clear_csv(2)
                self.clear_conclusion_jpg()

            self._set_checkbox_silently(self.chk_delete_c1, False)
            self._set_checkbox_silently(self.chk_delete_c2, False)
            self._set_checkbox_silently(self.chk_delete_jpg, False)
            self._set_import_selection_enabled(False)

            if had_selected_files:
                self._warn(
                    "При включённом режиме «Удалить исследование» "
                    "выбор новых файлов недоступен. Ранее выбранные файлы были сняты."
                )
            return

        self._set_import_selection_enabled(True)

    def _confirm_delete_whole_study(self, test_dir: Path) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Подтверждение удаления")
        box.setText(
            "Вы действительно хотите удалить существующее исследование целиком без замены?"
        )
        box.setInformativeText(
            "Будет удалена папка исследования со всеми файлами, "
            "исходные CSV-файлы и записи в базе данных.\n\n"
            f"Папка исследования:\n{test_dir}"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Yes

    def _replace_staff_combo_items(
        self,
        cb: QComboBox,
        *,
        items: list[str],
        default: str = "",
    ) -> None:
        default = normalize_staff_name(default)

        cb.blockSignals(True)
        cb.clear()
        cb.addItems(items)

        if default:
            cb.setCurrentText(default)
        else:
            cb.setCurrentIndex(-1)
            cb.setEditText("")

        cb.blockSignals(False)

    def _current_conclusion_place(self) -> str | None:
        if self.rb_concl_mnpc.isChecked():
            return "mnpc"
        if self.rb_concl_rnpc.isChecked():
            return "rnpc"
        return None

    def _update_conclusion_head_combo(self) -> None:
        place = self._current_conclusion_place()

        if place == "mnpc":
            default = M_ACTING_HEAD if self.concl_acting.isChecked() else M_HEAD_DEFAULT
            self._replace_staff_combo_items(
                self.concl_head_of,
                items=M_HEAD_OF_ITEMS,
                default=default,
            )
            return

        if place == "rnpc":
            default = R_ACTING_HEAD if self.concl_acting.isChecked() else R_HEAD_DEFAULT
            self._replace_staff_combo_items(
                self.concl_head_of,
                items=R_HEAD_OF_ITEMS,
                default=default,
            )
            return

        self._replace_staff_combo_items(self.concl_head_of, items=[], default="")

    def _update_conclusion_bio1_combo(self) -> None:
        place = self._current_conclusion_place()

        if place == "mnpc":
            if self.concl_doctor.isChecked():
                self._replace_staff_combo_items(
                    self.concl_bio1,
                    items=M_DOCTORS,
                    default=M_DOCTOR_DEFAULT,
                )
            else:
                self._replace_staff_combo_items(
                    self.concl_bio1,
                    items=M_BIOLOGISTS,
                    default="",
                )
            return

        if place == "rnpc":
            if self.concl_doctor.isChecked():
                self._replace_staff_combo_items(
                    self.concl_bio1,
                    items=R_DOCTORS,
                    default="",
                )
            else:
                self._replace_staff_combo_items(
                    self.concl_bio1,
                    items=R_BIOLOGISTS,
                    default="",
                )
            return

        self._replace_staff_combo_items(self.concl_bio1, items=[], default="")

    def _update_conclusion_bio2_combo(self) -> None:
        place = self._current_conclusion_place()

        if place == "mnpc":
            self._replace_staff_combo_items(
                self.concl_bio2,
                items=M_BIOLOGISTS,
                default="",
            )
            return

        if place == "rnpc":
            self._replace_staff_combo_items(
                self.concl_bio2,
                items=R_BIOLOGISTS,
                default="",
            )
            return

        self._replace_staff_combo_items(self.concl_bio2, items=[], default="")

    def _apply_conclusion_place_preset(self) -> None:
        self._update_conclusion_head_combo()
        self._update_conclusion_bio1_combo()
        self._update_conclusion_bio2_combo()

    def _on_concl_place_changed(self, place: str, checked: bool) -> None:
        if not checked:
            return

        if place == "mnpc":
            self._set_button_checked_silently(self.concl_acting, False)
            self._set_button_checked_silently(self.concl_doctor, False)
        else:
            self._set_button_checked_silently(self.concl_acting, False)
            self._set_button_checked_silently(self.concl_doctor, True)

        self._apply_conclusion_place_preset()

    def _on_concl_acting_toggled(self, _checked: bool) -> None:
        self._update_conclusion_head_combo()

    def _on_concl_doctor_toggled(self, _checked: bool) -> None:
        self._update_conclusion_bio1_combo()

    def _collect_conclusion_payload(self):
        self._normalize_names()
        return build_conclusion_payload(
            class1_csv=self.class1_path,
            class2_csv=self.class2_path,
            ask_negative_without_csv=self._ask_negative_conclusion_without_csv,
            num_register_text=self.concl_num_register.text(),
            conclusion_place=self._current_conclusion_place(),
            head_of_text=self.concl_head_of.currentText(),
            biologist1_text=self.concl_bio1.currentText(),
            biologist2_text=self.concl_bio2.currentText(),
            acting=self.concl_acting.isChecked(),
            doctor=self.concl_doctor.isChecked(),
            screening=self.concl_screening.isChecked(),
            last_name=self.last_name.text().strip(),
            new_last_name=(
                self.new_last_name.text().strip()
                if self.new_last_name.isEnabled()
                else ""
            ),
            first_name=self.first_name.text().strip(),
            middle_name=self.middle_name.text().strip() or "",
        )

    def _print_docx_default_printer(self, docx_path: Path) -> None:
        if not sys.platform.startswith("win"):
            raise RuntimeError(
                "Печать .docx реализована только для Windows (os.startfile)."
            )

        try:
            os.startfile(str(docx_path), "print")
        except OSError as e:
            raise RuntimeError(
                "Не удалось отправить файл на печать. "
                "Проверьте, что в системе настроено приложение по умолчанию для .docx (например, Word). "
                f"Ошибка: {e}"
            )

    def _delete_file_silently(self, path: Path) -> None:
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except Exception:
            pass

    def _schedule_temp_file_cleanup(self, path: Path, delay_sec: int = 180) -> None:
        delay_sec = max(10, int(delay_sec))

        if sys.platform.startswith("win"):
            cleanup_code = (
                "import pathlib, sys, time\n"
                "p = pathlib.Path(sys.argv[1])\n"
                "delay = int(sys.argv[2])\n"
                "time.sleep(delay)\n"
                "for _ in range(30):\n"
                "    try:\n"
                "        if p.exists():\n"
                "            p.unlink()\n"
                "        break\n"
                "    except Exception:\n"
                "        time.sleep(10)\n"
            )

            try:
                subprocess.Popen(
                    [sys.executable, "-c", cleanup_code, str(path), str(delay_sec)],
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    close_fds=True,
                )
                return
            except Exception:
                pass

        QTimer.singleShot(
            delay_sec * 1000, lambda p=path: self._delete_file_silently(p)
        )

    def on_conclusion_save(self) -> None:
        try:
            payload = self._collect_conclusion_payload()
            if payload is None:
                return
        except Exception as e:
            self._info_message(str(e))
            return

        try:
            save_dir = Path(self._default_conclusion_save_dir())
            save_dir.mkdir(parents=True, exist_ok=True)
            output_path = save_dir / payload.suggested_filename

            overwrite = False

            if output_path.exists():
                answer = QMessageBox.warning(
                    self,
                    "Файл уже существует",
                    "Файл заключения с таким именем уже существует.\n\n"
                    f"{output_path}\n\n"
                    "Заменить его?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )

                if answer != QMessageBox.Yes:
                    return

                overwrite = True

            result_path = save_conclusion_docx(
                payload=payload,
                output_path=output_path,
                overwrite=overwrite,
            )

        except Exception as e:
            self._err(f"Не удалось сформировать заключение:\n{e}")
            return

        self.concl_num_register.setText("")
        self.concl_screening.setChecked(False)

        self._info_with_explorer(
            f"Заключение сохранено:\n{result_path}",
            [result_path],
        )

    def on_conclusion_print(self) -> None:
        try:
            payload = self._collect_conclusion_payload()
            if payload is None:
                return
        except Exception as e:
            self._info_message(str(e))
            return

        tmp_name = f"__hla_print__{Path(payload.suggested_filename).stem}__{uuid4().hex[:8]}.docx"
        tmp_path = Path(tempfile.gettempdir()) / tmp_name

        try:
            save_conclusion_docx(payload=payload, output_path=tmp_path)
            self._print_docx_default_printer(tmp_path)
            self._schedule_temp_file_cleanup(tmp_path, delay_sec=180)
        except Exception as e:
            self._delete_file_silently(tmp_path)
            self._err(f"Не удалось распечатать:\n{e}")
            return

        self.status.setText("🖨️ Заключение отправлено на печать.")
