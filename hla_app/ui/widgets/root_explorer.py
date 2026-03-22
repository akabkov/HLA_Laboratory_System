"""Боковой файловый проводник приложения.

Модуль содержит модель файловой системы, proxy-сортировку, делегат имен,
поисковую строку и основной `RootExplorerWidget` с индексным и обычным поиском,
навигацией по дереву и файловыми действиями. Если проблема связана с боковой
панелью, поиском или открытием папок, почти весь нужный код находится здесь.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import warnings
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QDir,
    QEvent,
    QEventLoop,
    QModelIndex,
    QSize,
    QSortFilterProxyModel,
    QStandardPaths,
    QStorageInfo,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QDesktopServices,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFontMetrics,
    QIcon,
    QKeyEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFileSystemModel,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSpacerItem,
    QStyledItemDelegate,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from hla_app.config.managed_files import (
    MANAGED_STUDY_FILE_NAMES,
    class_result_file_name,
)
from hla_app.config.settings import DEFAULT_DIALOG_DIR, ORGANS
from hla_app.services.app_prefs import load_effective_app_preferences
from hla_app.services.file_tree_index import FileTreeIndexService
from hla_app.services.shared_write_lock import (
    SharedWriteLockBusyError,
    acquire_shared_write_lock,
)
from hla_app.storage.fs_ops import copy_file as fs_copy_file
from hla_app.storage.fs_ops import create_dir as fs_create_dir
from hla_app.storage.fs_ops import delete_dir_tree as fs_delete_dir_tree
from hla_app.storage.fs_ops import delete_empty_dir as fs_delete_empty_dir
from hla_app.storage.fs_ops import delete_file as fs_delete_file
from hla_app.storage.fs_ops import rename_path as fs_rename_path
from hla_app.storage.fs_ops import split_patient_folder_name
from hla_app.utils.validators import parse_ddmmyyyy

# --- Регулярные выражения и константы бокового файлового проводника ---
# Эвристика для папок пациентов:
# Старый формат:
#   Иванов_А
#   Иванов_А.Е
# Новый формат:
#   Иванов_Алексей
#   Иванов_Алексей.Евгеньевич
#   Иванов(Петров)_Мария.Сергеевна
#
# Также допускаем дефисы в имени и/или отчестве:
#   Сидорова_Анна-Мария
#   Сидорова_Анна-Мария.Петровна
#   Сидорова_Анна.Петровна-Ивановна
#   Иванова(Петрова)_Анна-Мария.Петровна
_PATIENT_LAST_BLOCK_RE = re.compile(r"^[А-Яа-яЁё-]+(?:\([А-Яа-яЁё-]+\))?$")

# Одна часть имени:
# - либо полное имя/отчество с возможными дефисами: Анна, Анна-Мария
# - либо один инициал: А
_PATIENT_NAME_PART_RE = r"(?:[А-Яа-яЁё]+(?:-[А-Яа-яЁё]+)*|[А-Яа-яЁё])"

# Блок имени в имени папки:
# - А
# - А.Е
# - Алексей
# - Алексей.Евгеньевич
# - Анна-Мария
# - Анна-Мария.Петровна
# - Анна.Петровна-Ивановна
_PATIENT_NAME_BLOCK_RE = re.compile(
    rf"^{_PATIENT_NAME_PART_RE}(?:\.{_PATIENT_NAME_PART_RE})?$"
)

_INVALID_FS_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

# Ограничение высоты списка результатов поиска
_INDEX_SEARCH_RESULTS_MAX_RATIO = 0.20
_SEARCH_NAVIGATION_RETRY_LIMIT = 50
_SEARCH_NAVIGATION_RETRY_INTERVAL_MS = 100
_SEARCH_RESULTS_ROW_EXTRA_HEIGHT_PX = 16

# Базовые размеры панели проводника
EXPLORER_MIN_WIDTH = 200
EXPLORER_MAX_WIDTH = 400
EXPLORER_AVG_WIDTH = EXPLORER_MIN_WIDTH + (EXPLORER_MAX_WIDTH - EXPLORER_MIN_WIDTH) / 2
_SEARCH_NAVIGATION_HORIZONTAL_OFFSET_PX = -32


# --- Вспомогательные модель, proxy, delegate и специализированный tree view ---
class ExplorerFileSystemModel(QFileSystemModel):
    def __init__(self, owner: "RootExplorerWidget"):
        super().__init__(owner)
        self._owner = owner
        self.setReadOnly(False)

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.EditRole and index.isValid():
            old_path = Path(self.filePath(index))
            is_dir = old_path.is_dir()

            protected, reason = self._owner._is_protected_path(
                old_path,
                is_dir_hint=is_dir,
            )
            if protected:
                self._owner._warn_protected(old_path, reason)
                return False

            new_name = str(value).strip()

            err = self._owner._validate_fs_name(
                new_name,
                "папка" if is_dir else "файл",
            )
            if err:
                QMessageBox.warning(self._owner, "Некорректное имя", err)
                return False

            if new_name == old_path.name:
                return False

            new_path = old_path.with_name(new_name)

            protected, reason = self._owner._is_protected_path(
                new_path,
                is_dir_hint=is_dir,
            )
            if protected:
                self._owner._warn_protected(new_path, reason)
                return False

            if new_path.exists():
                QMessageBox.warning(
                    self._owner,
                    "Объект уже существует",
                    f"Объект уже существует:\n{new_path}",
                )
                return False

        return super().setData(index, value, role)


class ExplorerSortProxyModel(QSortFilterProxyModel):
    def lessThan(self, left, right) -> bool:
        source_model = self.sourceModel()
        if source_model is None or left.column() != 0 or right.column() != 0:
            return super().lessThan(left, right)

        try:
            left_path = Path(source_model.filePath(left))
            right_path = Path(source_model.filePath(right))
        except Exception:
            return super().lessThan(left, right)

        if left_path.parent == right_path.parent:
            try:
                left_date = (
                    parse_ddmmyyyy(left_path.name) if source_model.isDir(left) else None
                )
                right_date = (
                    parse_ddmmyyyy(right_path.name)
                    if source_model.isDir(right)
                    else None
                )
            except Exception:
                left_date = None
                right_date = None

            if left_date is not None and right_date is not None:
                return left_date < right_date

        return super().lessThan(left, right)


class ExplorerNameDelegate(QStyledItemDelegate):
    def __init__(self, owner: "RootExplorerWidget"):
        super().__init__(owner)
        self._owner = owner

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            orig_path = self._owner._path_from_index(index)
            if orig_path is not None:
                editor.setProperty("_orig_path", str(orig_path))
            editor.setProperty("_committed", False)
        return editor

    def setEditorData(self, editor, index):
        super().setEditorData(editor, index)

        if not isinstance(editor, QLineEdit):
            return

        orig_path_raw = editor.property("_orig_path")
        if not orig_path_raw:
            QTimer.singleShot(0, editor.selectAll)
            return

        orig_path = Path(orig_path_raw)
        ctx = self._owner._inline_edit_context

        def apply_selection():
            # Для обычного переименования файла:
            # выделяем только имя без расширения.
            if (
                ctx
                and ctx.get("kind") == "rename"
                and Path(ctx.get("path")) == orig_path
                and orig_path.is_file()
            ):
                stem = orig_path.stem
                if stem:
                    editor.setSelection(0, len(stem))
                else:
                    editor.selectAll()
                return

            # Для папок и новой папки — выделяем всё
            editor.selectAll()

        QTimer.singleShot(0, apply_selection)

    def setModelData(self, editor, model, index):
        if not isinstance(editor, QLineEdit):
            super().setModelData(editor, model, index)
            return

        orig_path_raw = editor.property("_orig_path")
        if not orig_path_raw:
            super().setModelData(editor, model, index)
            return

        orig_path = Path(orig_path_raw)
        ctx = self._owner._inline_edit_context
        new_name = editor.text().strip()
        is_dir = orig_path.is_dir()

        if ctx and Path(ctx.get("path")) == orig_path:
            kind = str(ctx.get("kind") or "")

            err = self._owner._validate_fs_name(
                new_name,
                "папка" if is_dir else "файл",
            )
            if err:
                QMessageBox.warning(self._owner, "Некорректное имя", err)
                return

            final_path = orig_path.with_name(new_name)

            protected, reason = self._owner._is_protected_path(
                final_path,
                is_dir_hint=is_dir,
            )
            if protected:
                self._owner._warn_protected(final_path, reason)
                return

            if final_path.exists() and final_path != orig_path:
                QMessageBox.warning(
                    self._owner,
                    "Объект уже существует",
                    f"Объект уже существует:\n{final_path}",
                )
                return

            # Для rename без изменения имени просто завершаем редактирование.
            if kind == "rename" and final_path == orig_path:
                editor.setProperty("_committed", True)
                self._owner._pending_inline_commit = None
                self._owner._inline_edit_context = None
                return

            # Для new_folder даже при имени по умолчанию
            # всё равно нужна последующая проверка пароля.
            editor.setProperty("_committed", True)
            self._owner._pending_inline_commit = {
                "kind": kind,
                "path": str(orig_path),
                "new_name": new_name,
            }
            return

        super().setModelData(editor, model, index)

    def destroyEditor(self, editor, index):
        try:
            orig_path_raw = (
                editor.property("_orig_path") if editor is not None else None
            )
            committed = (
                bool(editor.property("_committed")) if editor is not None else False
            )
        except Exception:
            orig_path_raw = None
            committed = False

        pending_cleanup_path: Path | None = None
        should_apply_pending_commit = False

        if orig_path_raw:
            orig_path = Path(orig_path_raw)
            ctx = self._owner._inline_edit_context
            pending = self._owner._pending_inline_commit

            if pending and Path(str(pending.get("path") or "")) == orig_path:
                should_apply_pending_commit = True

            if ctx and Path(ctx.get("path")) == orig_path:
                if (
                    ctx.get("kind") == "new_folder"
                    and not committed
                    and not should_apply_pending_commit
                ):
                    pending_cleanup_path = orig_path

                self._owner._inline_edit_context = None

        super().destroyEditor(editor, index)

        if should_apply_pending_commit:
            QTimer.singleShot(0, self._owner._apply_pending_inline_commit)
            return

        if pending_cleanup_path is not None:
            QTimer.singleShot(
                0,
                lambda p=pending_cleanup_path: self._owner._cleanup_aborted_new_folder(
                    p
                ),
            )


class ExplorerTreeView(QTreeView):
    escapePressed = Signal()
    searchRequested = Signal()
    emptyAreaClicked = Signal()

    def __init__(self, owner: "RootExplorerWidget"):
        super().__init__(owner)
        self._owner = owner

        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setDefaultDropAction(Qt.CopyAction)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.escapePressed.emit()
            event.accept()
            return

        if event.key() == Qt.Key_F and event.modifiers() & Qt.ControlModifier:
            self.searchRequested.emit()
            event.accept()
            return

        if event.key() == Qt.Key_Backspace and event.modifiers() == Qt.NoModifier:
            left_event = QKeyEvent(
                event.type(),
                Qt.Key_Left,
                Qt.NoModifier,
            )
            super().keyPressEvent(left_event)
            event.accept()
            return

        if (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
            and event.modifiers() == Qt.NoModifier
        ):
            index = self.currentIndex()
            if index.isValid():
                self._owner._activate_index(index)
                event.accept()
                return

        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = self._event_pos(event)

        if event.button() == Qt.LeftButton and not self.indexAt(pos).isValid():
            selection_model = self.selectionModel()
            if selection_model is not None:
                selection_model.clear()

            self.clearSelection()
            self.setFocus(Qt.MouseFocusReason)
            self.emptyAreaClicked.emit()
            event.accept()
            return

        super().mousePressEvent(event)

    def _event_pos(self, event) -> object:
        if hasattr(event, "position"):
            return event.position().toPoint()
        return event.pos()

    def _extract_local_paths(self, event) -> list[Path]:
        mime = event.mimeData()
        if mime is None or not mime.hasUrls():
            return []

        paths: list[Path] = []
        for url in mime.urls():
            if not url.isLocalFile():
                return []
            path = Path(url.toLocalFile())
            if path.exists():
                paths.append(path)

        return paths

    def _can_accept_event(self, event) -> bool:
        source_paths = self._extract_local_paths(event)
        if not source_paths:
            return False

        pos = self._event_pos(event)
        target_dir = self._owner._drop_target_dir_for_pos(pos)
        if target_dir is None:
            return False

        allowed, _reason = self._owner._can_manage_children_in(target_dir)
        return allowed

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._can_accept_event(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._can_accept_event(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        source_paths = self._extract_local_paths(event)
        if not source_paths:
            event.ignore()
            return

        pos = self._event_pos(event)
        target_dir = self._owner._drop_target_dir_for_pos(pos)
        if target_dir is None:
            event.ignore()
            return

        if self._owner._handle_external_drop(source_paths, target_dir):
            event.acceptProposedAction()
        else:
            event.ignore()


class ExplorerSearchLineEdit(QLineEdit):
    escapePressed = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.escapePressed.emit()
            event.accept()
            return

        super().keyPressEvent(event)


# --- Основной виджет проводника, поиска и файловых действий ---
class RootExplorerWidget(QWidget):
    def __init__(
        self,
        *,
        root_dir: Path,
        password_callback: Callable[[], bool] | None = None,
        index_service: FileTreeIndexService | None = None,
        request_index_rebuild_callback: Callable[[], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)

        # Панель проводника не должна раздувать весь splitter из-за длинных имен.
        self.setMinimumWidth(EXPLORER_MIN_WIDTH)
        self.setMaximumWidth(EXPLORER_MAX_WIDTH)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        # --- Служебное состояние проводника и поисковой навигации ---
        self._root_dir = Path(root_dir)
        self._password_callback = password_callback
        self._index_service = index_service
        self._request_index_rebuild_callback = request_index_rebuild_callback
        self._inline_edit_context: dict | None = None
        self._pending_inline_commit: dict | None = None
        self._pending_navigation_path: Path | None = None
        self._pending_navigation_attempts = 0
        self._search_mode: str | None = None
        self._force_root_scope_on_initial_load = True

        # --- Основной layout, заголовок и элементы поиска ---
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget(self)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(8, 8, 8, 8)
        header_layout.setSpacing(4)

        self.lbl_title = QLabel("Проводник")
        self.lbl_title.setStyleSheet("font-weight: 600;")

        header_layout.addWidget(self.lbl_title)

        self.lbl_root_path = QLabel("")
        self.lbl_root_path.setWordWrap(True)
        self.lbl_root_path.setStyleSheet("color: #555555;")
        header_layout.addWidget(self.lbl_root_path)

        self.lbl_index_status = QLabel("")
        self.lbl_index_status.setWordWrap(True)
        self.lbl_index_status.setStyleSheet("color: #666666; font-size: 11px;")
        header_layout.addWidget(self.lbl_index_status)

        self.search_edit = ExplorerSearchLineEdit(self)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFocusPolicy(Qt.ClickFocus)
        self.search_edit.setToolTip(
            "Поиск по имени файла или папки в текущей области поиска.\n"
            "Если выделение находится внутри папки органа, поиск выполняется по всей папке этого органа.\n"
            "Если ничего не выделено, поиск выполняется от корня боковой панели."
        )

        search_icon = QIcon.fromTheme("edit-find")
        if not search_icon.isNull():
            self.search_edit.addAction(search_icon, QLineEdit.LeadingPosition)

        # --- Таймеры поиска и повторной навигации к найденному пути ---
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)

        self._pending_navigation_timer = QTimer(self)
        self._pending_navigation_timer.setSingleShot(True)
        self._pending_navigation_timer.setInterval(_SEARCH_NAVIGATION_RETRY_INTERVAL_MS)

        self.search_edit.textChanged.connect(self._on_search_text_changed)
        self.search_edit.escapePressed.connect(self._clear_search_from_escape)
        self._search_timer.timeout.connect(self._apply_search_now)
        self._pending_navigation_timer.timeout.connect(self._retry_pending_navigation)

        # --- Текущее состояние поисковой сессии ---
        self._search_active = False
        self._search_base_dir: Path | None = None
        self._search_saved_expanded_paths: set[str] = set()
        self._search_saved_current_path: str | None = None

        self._search_reapply_scheduled = False
        self._search_applying = False
        self._active_context_menu: QMenu | None = None

        header_layout.addWidget(self.search_edit)

        outer.addWidget(header)

        # --- Список результатов indexed-поиска ---
        self.search_results = QListWidget(self)
        self.search_results.setVisible(False)
        self.search_results.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.search_results.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.search_results.itemActivated.connect(self._on_search_result_activated)
        self.search_results.itemClicked.connect(self._on_search_result_clicked)

        outer.addWidget(self.search_results)

        # --- QFileSystemModel, proxy-сортировка и дерево проводника ---
        self.model = ExplorerFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot)
        self._proxy_model = ExplorerSortProxyModel(self)
        self._proxy_model.setDynamicSortFilter(True)

        # --- Настройка дерева файлов и редактирования имен ---
        self.tree = ExplorerTreeView(self)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)

        # Горизонтальный скролл должен жить внутри дерева, а не растягивать окно.
        self.tree.setMinimumWidth(0)
        self.tree.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.tree.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tree.setTextElideMode(Qt.ElideNone)

        # Колонку дерева оставляем интерактивной, чтобы длинные имена
        # показывались через внутренний горизонтальный скролл.
        tree_header = self.tree.header()
        tree_header.setMinimumSectionSize(0)
        tree_header.setStretchLastSection(False)
        tree_header.setSectionResizeMode(0, QHeaderView.Interactive)

        self.name_delegate = ExplorerNameDelegate(self)
        self.tree.setItemDelegate(self.name_delegate)
        self._apply_model_to_tree()

        self.tree.doubleClicked.connect(self._on_double_clicked)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.escapePressed.connect(self._collapse_tree_to_root)
        self.tree.searchRequested.connect(self._focus_search_field)
        self.tree.emptyAreaClicked.connect(self._on_tree_empty_area_clicked)
        self.tree.expanded.connect(self._on_tree_expanded)
        self.tree.collapsed.connect(self._on_tree_collapsed)

        outer.addWidget(self.tree, 1)

        QTimer.singleShot(0, self.tree.setFocus)

    def minimumSizeHint(self) -> QSize:
        # Минимальный размер панели не должен зависеть от содержимого модели.
        return QSize(EXPLORER_MIN_WIDTH, EXPLORER_MIN_WIDTH * 2)

    def sizeHint(self) -> QSize:
        # Предпочтительная ширина боковой панели.
        return QSize(EXPLORER_AVG_WIDTH, EXPLORER_AVG_WIDTH * 2)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._update_tree_column_width)

    # --- Базовая инициализация дерева, модели и безопасное переподключение сигналов ---
    def _update_tree_column_width(self) -> None:
        if not self.isVisible():
            return

        if self.tree.model() is None:
            return

        # Подстраиваем первую колонку под содержимое, чтобы горизонтальный
        # скролл появлялся внутри дерева, а не во всем окне.
        self.tree.resizeColumnToContents(0)

    def _safe_disconnect_signal(self, signal, slot) -> None:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Failed to disconnect .*",
                category=RuntimeWarning,
            )
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _bind_model_signals(self) -> None:
        self._safe_disconnect_signal(
            self.model.rowsInserted,
            self._on_model_rows_inserted,
        )
        self._safe_disconnect_signal(
            self.model.directoryLoaded,
            self._on_model_directory_loaded,
        )

        self.model.rowsInserted.connect(self._on_model_rows_inserted)
        self.model.directoryLoaded.connect(self._on_model_directory_loaded)

    def _map_from_source(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        proxy_model = getattr(self, "_proxy_model", None)
        if proxy_model is None:
            return index

        return proxy_model.mapFromSource(index)

    def _map_to_source(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()

        proxy_model = getattr(self, "_proxy_model", None)
        if proxy_model is None:
            return index

        return proxy_model.mapToSource(index)

    def _tree_index_for_path(self, path: Path | str) -> QModelIndex:
        if getattr(self, "model", None) is None:
            return QModelIndex()

        source_index = self.model.index(str(path))
        return self._map_from_source(source_index)

    def _apply_model_to_tree(self) -> None:
        root_index = self.model.setRootPath(str(self._root_dir))
        self._proxy_model.setSourceModel(self.model)
        self.tree.setModel(self._proxy_model)
        self.tree.setRootIndex(self._map_from_source(root_index))
        self.tree.sortByColumn(0, Qt.AscendingOrder)

        self._bind_model_signals()

        selection_model = self.tree.selectionModel()
        if selection_model is not None:
            self._safe_disconnect_signal(
                selection_model.currentChanged,
                self._on_tree_current_changed,
            )
            selection_model.currentChanged.connect(self._on_tree_current_changed)

        for col in (1, 2, 3):
            self.tree.hideColumn(col)

        self.lbl_root_path.setText(self._root_dir.name or str(self._root_dir))
        self.lbl_root_path.setToolTip(str(self._root_dir))
        self._update_search_placeholder()
        self._apply_root_level_visibility()
        QTimer.singleShot(0, self._update_tree_column_width)

    def _collect_expanded_paths(self) -> set[str]:
        expanded: set[str] = set()
        tree_model = self.tree.model()
        if tree_model is None:
            return expanded

        def walk(parent_index):
            rows = tree_model.rowCount(parent_index)
            for row in range(rows):
                index = tree_model.index(row, 0, parent_index)
                if not index.isValid():
                    continue

                path = self._path_from_index(index)
                if path is None:
                    continue

                if self.tree.isExpanded(index):
                    expanded.add(str(path))

                if tree_model.hasChildren(index):
                    walk(index)

        walk(self.tree.rootIndex())
        return expanded

    def _restore_expanded_paths(self, expanded_paths: set[str]) -> None:
        for path_str in expanded_paths:
            index = self._tree_index_for_path(path_str)
            if index.isValid():
                self.tree.expand(index)

    def _rebuild_model(self) -> None:
        old_model = self.model

        expanded_paths = self._collect_expanded_paths()

        current_index = self.tree.currentIndex()
        current_path = self._path_from_index(current_index)

        self.model = ExplorerFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot)

        self._apply_model_to_tree()

        self._restore_expanded_paths(expanded_paths)

        if current_path:
            new_index = self._tree_index_for_path(current_path)
            if new_index.isValid():
                self.tree.setCurrentIndex(new_index)
                self.tree.scrollTo(new_index)

        old_model.deleteLater()

        if self.search_edit.text().strip():
            self._apply_search_now()

    def set_root_path(self, root_dir: Path | str) -> None:
        self._reset_search_state()
        self._root_dir = Path(root_dir)
        self._force_root_scope_on_initial_load = True
        self._apply_model_to_tree()

        if self.search_edit.text().strip():
            self._begin_search_session()
            self._apply_search_now()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

        if self.search_results.isVisible():
            self._update_search_results_height()

    def refresh(self) -> None:
        self._rebuild_model()

    def _flush_qt_deferred_deletes(self) -> None:
        """
        Гарантированно обрабатывает отложенные удаления QObject через deleteLater().
        Это критично перед rename/delete на Windows, чтобы QFileSystemModel и связанные
        watcher'ы реально отпустили каталоги.
        """
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def suspend_filesystem_model(self) -> None:
        """
        Полностью отсоединяет QFileSystemModel от дерева.
        Это нужно перед критичными rename/delete-операциями,
        чтобы модель Qt не держала внутренние файловые наблюдатели
        на каталогах пациента.
        """
        self._reset_search_state()
        self._inline_edit_context = None
        self._pending_inline_commit = None

        selection_model = self.tree.selectionModel()
        if selection_model is not None:
            selection_model.clear()

        self.tree.setCurrentIndex(QModelIndex())
        self.tree.clearSelection()
        self.tree.setRootIndex(QModelIndex())

        old_model = getattr(self, "model", None)

        self.tree.setModel(None)
        self._proxy_model.setSourceModel(None)
        self.model = None

        if old_model is not None:
            try:
                old_model.deleteLater()
            except Exception:
                pass

        self._flush_qt_deferred_deletes()

    def resume_filesystem_model(self) -> None:
        """
        Восстанавливает QFileSystemModel после завершения критичной
        файловой операции.
        """
        if getattr(self, "model", None) is not None:
            return

        self._flush_qt_deferred_deletes()

        self.model = ExplorerFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot)
        self._apply_model_to_tree()

        self._flush_qt_deferred_deletes()

    # --- Состояние локального индекса и область видимости боковой панели ---
    def notify_index_build_started(self) -> None:
        self.lbl_index_status.setText(
            "Поиск: локальный индекс обновляется. "
            "До завершения используется резервный режим."
        )
        self.lbl_index_status.setToolTip("Локальный индекс перестраивается в фоне.")

    def notify_index_ready(
        self,
        *,
        entry_count: int | None = None,
        skipped_dir_count: int | None = None,
    ) -> None:
        skipped = max(0, int(skipped_dir_count or 0))

        if skipped > 0:
            text = "Поиск: локальный индекс построен частично. Используется резервный режим."
            if entry_count is not None:
                text += f" Записей: {entry_count}."
            text += f" Пропущено каталогов: {skipped}"

            tooltip_parts = [
                "Локальный индекс построен, но часть каталогов не удалось прочитать.",
                "Быстрый поиск отключён до полной успешной перестройки индекса.",
            ]
            if entry_count is not None:
                tooltip_parts.append(f"Записей: {entry_count}")
            tooltip_parts.append(f"Пропущено каталогов: {skipped}")

            tooltip = "\n".join(tooltip_parts)
        else:
            text = "Поиск: локальный индекс готов."
            if entry_count is not None:
                text += f" Записей: {entry_count}"
            tooltip = text

        self.lbl_index_status.setText(text)
        self.lbl_index_status.setToolTip(tooltip)
        self.reapply_current_search()

    def notify_index_build_failed(self, error_text: str) -> None:
        self.lbl_index_status.setText(
            "Поиск: не удалось обновить локальный индекс. Используется резервный режим."
        )
        self.lbl_index_status.setToolTip(error_text)

    def _root_organs_filter_enabled(self) -> bool:
        return bool(load_effective_app_preferences().limit_root_explorer_to_organs)

    def _allowed_root_dirs(self) -> list[Path]:
        result: list[Path] = []
        for name in ORGANS:
            path = self._root_dir / name
            if path.exists() and path.is_dir():
                result.append(path)
        return result

    def _is_allowed_root_item(self, path: Path) -> bool:
        if not self._root_organs_filter_enabled():
            return True
        return path.is_dir() and path.name in ORGANS

    def _is_path_in_sidebar_scope(self, path: Path) -> bool:
        try:
            rel = path.resolve(strict=False).relative_to(
                self._root_dir.resolve(strict=False)
            )
        except Exception:
            return False

        if rel == Path("."):
            return True

        if not self._root_organs_filter_enabled():
            return True

        parts = rel.parts
        return bool(parts) and parts[0] in ORGANS

    def _is_source_files_path(self, path: Path) -> bool:
        try:
            rel_parts = (
                path.resolve(strict=False)
                .relative_to(self._root_dir.resolve(strict=False))
                .parts
            )
        except Exception:
            return False

        return len(rel_parts) >= 2 and rel_parts[1] == "source_files"

    def _search_roots_for_base_dir(self, base_dir: Path) -> list[Path]:
        try:
            base_resolved = base_dir.resolve(strict=False)
            root_resolved = self._root_dir.resolve(strict=False)
        except Exception:
            base_resolved = base_dir
            root_resolved = self._root_dir

        if not self._root_organs_filter_enabled():
            if base_dir.exists() and base_dir.is_dir():
                return [base_dir]
            return []

        if base_resolved == root_resolved:
            return self._allowed_root_dirs()

        if (
            self._is_path_in_sidebar_scope(base_dir)
            and base_dir.exists()
            and base_dir.is_dir()
        ):
            return [base_dir]

        return []

    def _apply_root_level_visibility(self) -> None:
        root_index = self.tree.rootIndex()
        if not root_index.isValid():
            return

        tree_model = self.tree.model()
        if tree_model is None:
            return

        rows = tree_model.rowCount(root_index)
        for row in range(rows):
            index = tree_model.index(row, 0, root_index)
            if not index.isValid():
                continue

            path = self._path_from_index(index)
            if path is None:
                continue

            hide = not self._is_allowed_root_item(path)
            self.tree.setRowHidden(row, root_index, hide)

    def _indexed_search_paths(
        self,
        text: str,
        base_dir: Path,
        *,
        limit: int = 100,
    ) -> list[Path]:
        if self._index_service is None:
            return []

        paths: list[Path] = []
        seen: set[str] = set()

        for search_root in self._search_roots_for_base_dir(base_dir):
            if not search_root.exists() or not search_root.is_dir():
                continue

            try:
                hits = self._index_service.search(
                    root_dir=self._root_dir,
                    base_dir=search_root,
                    text=text,
                    limit=limit,
                )
            except Exception:
                continue

            for hit in hits:
                path = hit.path

                if not self._is_path_in_sidebar_scope(path):
                    continue

                key = str(path.resolve(strict=False))
                if key in seen:
                    continue

                seen.add(key)
                paths.append(path)

        paths.sort(key=lambda p: (0 if p.is_dir() else 1, str(p).casefold()))
        return paths[:limit]

    def reapply_current_search(self) -> None:
        if self.search_edit.text().strip():
            self._apply_search_now()

    def _reset_search_state(self) -> None:
        self._search_timer.stop()
        self._cancel_pending_navigation()
        self._search_reapply_scheduled = False
        self._search_applying = False
        self._search_active = False
        self._search_base_dir = None
        self._search_saved_expanded_paths = set()
        self._search_saved_current_path = None
        self._search_mode = None
        self._clear_search_results()

    def _reset_tree_selection_to_root_scope(self) -> None:
        selection_model = self.tree.selectionModel()
        if selection_model is not None:
            selection_model.clear()

        self.tree.setCurrentIndex(QModelIndex())
        self.tree.clearSelection()
        self._update_search_placeholder()

    def _request_index_rebuild(self) -> None:
        if self._request_index_rebuild_callback is None:
            return

        try:
            self._request_index_rebuild_callback()
        except Exception:
            pass

    def _can_use_indexed_search(self) -> bool:
        if self._index_service is None:
            return False

        try:
            if not self._index_service.is_ready_for(self._root_dir):
                return False

            return self._index_service.skipped_dir_count() <= 0
        except Exception:
            return False

    def _yield_during_legacy_search(self) -> None:
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _update_search_results_height(self) -> None:
        if not self.search_results.isVisible():
            return

        count = self.search_results.count()
        if count <= 0:
            self.search_results.setFixedHeight(0)
            return

        frame = self.search_results.frameWidth() * 2

        content_height = 0
        for row in range(count):
            row_height = (
                self.search_results.sizeHintForRow(row)
                + _SEARCH_RESULTS_ROW_EXTRA_HEIGHT_PX
            )
            if row_height <= 0:
                row_height = (
                    self.search_results.fontMetrics().height()
                    + _SEARCH_RESULTS_ROW_EXTRA_HEIGHT_PX
                )
            content_height += row_height

        spacing = max(0, count - 1) * self.search_results.spacing()
        content_height += spacing + frame

        tree_height = max(1, self.tree.viewport().height())
        max_height = max(
            self.search_results.fontMetrics().height()
            + frame
            + _SEARCH_RESULTS_ROW_EXTRA_HEIGHT_PX,
            int(tree_height * _INDEX_SEARCH_RESULTS_MAX_RATIO),
        )

        final_height = min(content_height, max_height)
        self.search_results.setFixedHeight(final_height)

    def _clear_search_results(self) -> None:
        self.search_results.clear()
        self.search_results.setVisible(False)
        self.search_results.setFixedHeight(0)

    def _cancel_pending_navigation(self) -> None:
        self._pending_navigation_timer.stop()
        self._pending_navigation_path = None
        self._pending_navigation_attempts = 0

    def _schedule_pending_navigation_retry(self, path: Path) -> bool:
        path = Path(path)

        if self._pending_navigation_path != path:
            self._pending_navigation_path = path
            self._pending_navigation_attempts = 0

        self._pending_navigation_attempts += 1

        if self._pending_navigation_attempts > _SEARCH_NAVIGATION_RETRY_LIMIT:
            self._cancel_pending_navigation()
            return False

        if not self._pending_navigation_timer.isActive():
            self._pending_navigation_timer.start()

        return True

    def _retry_pending_navigation(self) -> None:
        path = self._pending_navigation_path
        if path is None:
            return

        self._navigate_to_path(path)

    def _search_result_display_text(self, path: Path) -> str:
        base_dir = self._search_scope_dir()

        try:
            rel = path.relative_to(base_dir)
            rel_text = str(rel)
        except Exception:
            rel_text = path.name

        if not rel_text:
            rel_text = path.name or str(path)

        prefix = "📁" if path.is_dir() else "📄"
        return f"{prefix} {rel_text}"

    # --- Результаты поиска и навигация к найденным путям в дереве ---
    def _populate_search_results(self, paths: list[Path]) -> None:
        self.search_results.clear()

        if not paths:
            item = QListWidgetItem("Ничего не найдено")
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.search_results.addItem(item)
            self.search_results.setVisible(True)
            self._update_search_results_height()
            return

        for path in paths:
            item = QListWidgetItem(self._search_result_display_text(path))
            item.setData(Qt.UserRole, str(path))
            self.search_results.addItem(item)

        self.search_results.setVisible(True)
        self._update_search_results_height()

    def _on_search_result_clicked(self, item: QListWidgetItem) -> None:
        path_text = item.data(Qt.UserRole)
        if not path_text:
            return

        path = Path(path_text)
        QTimer.singleShot(0, lambda p=path: self._navigate_to_path(p))

    def _on_search_result_activated(self, item: QListWidgetItem) -> None:
        path_text = item.data(Qt.UserRole)
        if not path_text:
            return

        path = Path(path_text)
        QTimer.singleShot(0, lambda p=path: self._navigate_to_path(p))

    def _relative_parts_under_root(self, path: Path) -> tuple[str, ...] | None:
        try:
            return (
                path.resolve(strict=False)
                .relative_to(self._root_dir.resolve(strict=False))
                .parts
            )
        except Exception:
            return None

    def _should_expand_search_match_fully(self, path: Path) -> bool:
        if not path.exists() or not path.is_dir():
            return False

        rel_parts = self._relative_parts_under_root(path)
        if rel_parts is None:
            return False

        return self._looks_like_patient_folder_rel(
            rel_parts
        ) or self._looks_like_study_folder_rel(rel_parts)

    def _expand_dir_subtree(self, dir_path: Path) -> None:
        def _on_walk_error(_exc):
            return None

        for root_text, _dir_names, _file_names in os.walk(
            dir_path,
            topdown=True,
            onerror=_on_walk_error,
            followlinks=False,
        ):
            current_dir = Path(root_text)
            index = self._tree_index_for_path(current_dir)
            if index.isValid():
                self.tree.expand(index)

    def _prime_tree_path_for_navigation(self, path: Path) -> None:
        if getattr(self, "model", None) is None:
            return

        expand_target = path if path.is_dir() else path.parent
        ancestors: list[Path] = []
        current = expand_target

        while True:
            ancestors.append(current)
            if current == self._root_dir:
                break
            if current == current.parent:
                break
            current = current.parent

        for ancestor in reversed(ancestors):
            source_index = self.model.index(str(ancestor))
            if not source_index.isValid():
                continue

            try:
                if self.model.canFetchMore(source_index):
                    self.model.fetchMore(source_index)
            except Exception:
                pass

            index = self._map_from_source(source_index)
            if index.isValid():
                self.tree.expand(index)

    def _expand_search_match_if_needed(self, path: Path) -> None:
        if not path.is_dir():
            return

        index = self._tree_index_for_path(path)
        if not index.isValid():
            return

        if self._should_expand_search_match_fully(path):
            self._expand_dir_subtree(path)
        else:
            self.tree.expand(index)

    def _navigate_to_path(self, path: Path) -> None:
        path = Path(path)

        if self._root_organs_filter_enabled() and not self._is_path_in_sidebar_scope(
            path
        ):
            self._cancel_pending_navigation()
            QMessageBox.information(
                self,
                "Объект вне области проводника",
                "Этот путь находится вне папок, отображаемых в боковой панели.",
            )
            return

        if not path.exists():
            self._cancel_pending_navigation()
            QMessageBox.information(
                self,
                "Объект не найден",
                "Найденный объект уже не существует.\nЛокальный индекс будет перестроен.",
            )
            self._request_index_rebuild()
            return

        self._prime_tree_path_for_navigation(path)

        index = self._tree_index_for_path(path)
        if not index.isValid():
            if self._schedule_pending_navigation_retry(path):
                return

            self._cancel_pending_navigation()
            QMessageBox.warning(
                self,
                "Не удалось перейти",
                "Не удалось дождаться загрузки каталога в боковой панели.\n"
                f"Путь:\n{path}",
            )
            return

        self._cancel_pending_navigation()

        def apply_focus_and_scroll():
            if not index.isValid():
                return

            # Уводим фокус со списка результатов обратно в дерево.
            self.search_results.clearSelection()
            self.search_results.clearFocus()

            # Выделяем найденный элемент в дереве.
            self.tree.setCurrentIndex(index)

            # Стараемся показать найденный элемент ближе к началу viewport.
            self.tree.scrollTo(index, QAbstractItemView.PositionAtCenter)
            rect = self.tree.visualRect(index)
            if rect.isValid():
                hbar = self.tree.horizontalScrollBar()
                hbar.setValue(
                    hbar.value() + rect.left() + _SEARCH_NAVIGATION_HORIZONTAL_OFFSET_PX
                )

            # Возвращаем фокус самому дереву.
            self.tree.setFocus(Qt.OtherFocusReason)

        # Первый проход делаем сразу.
        apply_focus_and_scroll()

        # Второй проход нужен после дорисовки и relayout дерева Qt.
        QTimer.singleShot(0, apply_focus_and_scroll)

        if path.is_dir():
            QTimer.singleShot(
                0, lambda p=Path(path): self._expand_search_match_if_needed(p)
            )

    # --- Текстовый поиск: indexed-mode, legacy fallback и фильтрация дерева ---
    def _search_scope_dir(self) -> Path:
        if self._search_active and self._search_base_dir is not None:
            return self._search_base_dir
        return self._current_search_base_dir()

    def _is_search_excluded_path(self, path: Path) -> bool:
        try:
            if path.resolve(strict=False) == self._root_dir.resolve(strict=False):
                return False
        except Exception:
            pass

        if not self._root_organs_filter_enabled():
            return self._is_source_files_path(path)

        if not self._is_path_in_sidebar_scope(path):
            return True

        return self._is_source_files_path(path)

    def _build_search_placeholder(self) -> str:
        base_dir = self._search_scope_dir()

        try:
            base_dir_resolved = base_dir.resolve(strict=False)
        except Exception:
            base_dir_resolved = base_dir

        try:
            root_resolved = self._root_dir.resolve(strict=False)
        except Exception:
            root_resolved = self._root_dir

        if base_dir_resolved == root_resolved:
            # Если root_dir не совпадает с корнем тома, показываем имя базы,
            # а не системную метку диска.
            if root_resolved.parent != root_resolved:
                folder_name = (root_resolved.name or "").strip()
                if folder_name:
                    return f"Поиск в: {folder_name}"
                return f"Поиск в: {str(root_resolved)}"

            # Метку тома показываем только если сама файловая база — корень диска.
            storage = QStorageInfo(str(root_resolved))
            display_name = (storage.displayName() or "").strip()
            drive = (root_resolved.drive or "").rstrip("\\/")

            if display_name and drive:
                if drive.casefold() in display_name.casefold():
                    return f"Поиск в: {display_name}"
                return f"Поиск в: {display_name} ({drive})"

            if display_name:
                return f"Поиск в: {display_name}"

            if drive:
                return f"Поиск в: {drive}"

            return f"Поиск в: {root_resolved.name or str(root_resolved)}"

        folder_name = (base_dir_resolved.name or "").strip()
        if folder_name:
            return f"Поиск в: {folder_name}"

        return f"Поиск в: {str(base_dir_resolved)}"

    def _update_search_placeholder(self) -> None:
        if not hasattr(self, "search_edit"):
            return

        base_dir = self._search_scope_dir()
        self.search_edit.setPlaceholderText(self._build_search_placeholder())
        self.search_edit.setToolTip(
            "Поиск по имени файла или папки в текущей области поиска.\n"
            "Если выбранный путь находится внутри папки органа, поиск выполняется по всей папке этого органа.\n"
            "Если выбран файл вне папки органа, поиск выполняется в его родительской папке.\n\n"
            f"Текущая область поиска:\n{base_dir}"
        )

    def _begin_search_session(self) -> None:
        if self._search_active:
            return

        self._search_saved_expanded_paths = self._collect_expanded_paths()

        current_index = self.tree.currentIndex()
        current_path = self._path_from_index(current_index)
        self._search_saved_current_path = str(current_path) if current_path else None

        self._search_base_dir = self._current_search_base_dir()
        self._search_active = True
        self._update_search_placeholder()

    def _finish_search_session(self) -> None:
        self._search_reapply_scheduled = False

        if self._search_mode == "legacy":
            self._restore_tree_after_search()

        self._clear_search_results()

        self._search_active = False
        self._search_base_dir = None
        self._search_saved_expanded_paths = set()
        self._search_saved_current_path = None
        self._search_mode = None
        self._update_search_placeholder()

    def _restore_tree_after_search(self) -> None:
        root_index = self._tree_index_for_path(self._root_dir)
        if root_index.isValid():
            self._clear_row_hidden_recursive(root_index)

        self._apply_root_level_visibility()
        self.tree.collapseAll()
        self._restore_expanded_paths(self._search_saved_expanded_paths)

        if self._search_saved_current_path:
            saved_index = self._tree_index_for_path(self._search_saved_current_path)
            if saved_index.isValid():
                self.tree.setCurrentIndex(saved_index)
                self.tree.scrollTo(saved_index)

    def _is_dir_visible_in_tree(self, dir_path: Path) -> bool:
        """
        Возвращает True, если папка реально видима в текущем состоянии дерева.

        Логика:
        - корень файловой базы всегда считается видимым;
        - папка первого уровня (например, ПОЧКА / ПЕЧЕНЬ) видима всегда,
        пока сама существует в дереве;
        - для более глубоких папок все родительские папки до корня
        должны быть развернуты.
        """
        try:
            root_resolved = self._root_dir.resolve(strict=False)
            dir_resolved = dir_path.resolve(strict=False)
            dir_resolved.relative_to(root_resolved)
        except Exception:
            return False

        if dir_resolved == root_resolved:
            return True

        current = dir_resolved

        while current != root_resolved:
            parent = current.parent
            if parent == current:
                return False

            # Для узлов первого уровня отдельная проверка развернутости не нужна.
            if parent != root_resolved:
                parent_index = self._tree_index_for_path(parent)
                if not parent_index.isValid():
                    return False
                if not self.tree.isExpanded(parent_index):
                    return False

            current = parent

        return True

    def _current_search_base_dir(self) -> Path:
        index = self.tree.currentIndex()
        path = self._path_from_index(index)

        if path is None:
            return self._root_dir

        base_dir = path.parent if path.is_file() else path

        try:
            rel_parts = (
                base_dir.resolve(strict=False)
                .relative_to(self._root_dir.resolve(strict=False))
                .parts
            )
        except Exception:
            rel_parts = ()

        # По бизнес-логике поиск внутри любого уровня папки органа
        # всегда выполняется по корню этого органа.
        if rel_parts and rel_parts[0] in ORGANS:
            organ_dir = self._root_dir / rel_parts[0]
            if organ_dir.exists() and organ_dir.is_dir():
                return organ_dir

        if self._root_organs_filter_enabled() and not self._is_path_in_sidebar_scope(
            base_dir
        ):
            return self._root_dir

        if not self._is_dir_visible_in_tree(base_dir):
            return self._root_dir

        return base_dir

    def _on_tree_current_changed(self, _current, _previous) -> None:
        if self._search_active:
            return

        self._update_search_placeholder()

    def _on_tree_empty_area_clicked(self) -> None:
        if self._search_active:
            self._search_base_dir = self._root_dir
            self._update_search_placeholder()

            if self.search_edit.text().strip():
                self._apply_search_now()
            return

        self._update_search_placeholder()

    def _on_tree_expanded(self, _index) -> None:
        QTimer.singleShot(0, self._update_tree_column_width)
        self._schedule_search_reapply()

    def _on_tree_collapsed(self, _index) -> None:
        if not self._search_active:
            self._update_search_placeholder()

        QTimer.singleShot(0, self._update_tree_column_width)
        self._schedule_search_reapply()

    def _on_model_rows_inserted(self, _parent, _first: int, _last: int) -> None:
        self._apply_root_level_visibility()
        QTimer.singleShot(0, self._update_tree_column_width)
        self._schedule_search_reapply()

    def _on_model_directory_loaded(self, path: str) -> None:
        self._apply_root_level_visibility()

        try:
            loaded_path = Path(path).resolve(strict=False)
            root_path = self._root_dir.resolve(strict=False)
        except Exception:
            loaded_path = Path(path)
            root_path = self._root_dir

        if loaded_path == root_path:
            tree_model = self.tree.model()
            root_index = self._tree_index_for_path(self._root_dir)
            if tree_model is not None and root_index.isValid():
                for row in range(tree_model.rowCount(root_index)):
                    child_index = tree_model.index(row, 0, root_index)
                    if child_index.isValid():
                        self.tree.expand(child_index)

        if self._force_root_scope_on_initial_load and not self._search_active:
            if loaded_path == root_path:
                self._force_root_scope_on_initial_load = False
                QTimer.singleShot(0, self._reset_tree_selection_to_root_scope)

        QTimer.singleShot(0, self._update_tree_column_width)
        self._schedule_search_reapply()

    def _schedule_search_reapply(self) -> None:
        if not self._search_active:
            return

        if not self.search_edit.text().strip():
            return

        if self._search_mode == "indexed":
            return

        if self._search_applying or self._search_reapply_scheduled:
            return

        self._search_reapply_scheduled = True
        QTimer.singleShot(0, self._reapply_search_if_needed)

    def _reapply_search_if_needed(self) -> None:
        self._search_reapply_scheduled = False

        if not self._search_active:
            return

        if not self.search_edit.text().strip():
            return

        self._apply_search_now()

    def _on_search_text_changed(self, text: str) -> None:
        self._cancel_pending_navigation()

        if text.strip():
            if not self._search_active:
                self._begin_search_session()
            self._search_timer.start()
            return

        self._search_timer.stop()
        self._finish_search_session()

    def _clear_search_from_escape(self) -> None:
        self.search_edit.clear()
        self.tree.setFocus()

    def _focus_search_field(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _collapse_tree_to_root(self) -> None:
        self.tree.collapseAll()
        self._reset_tree_selection_to_root_scope()
        self.tree.setFocus(Qt.OtherFocusReason)

    def _add_path_with_ancestors(self, container: set[Path], path: Path) -> None:
        current = path

        while True:
            container.add(current)

            if current == self._root_dir:
                break

            parent = current.parent
            if parent == current:
                break

            current = parent

    def _add_dir_tree_paths(self, container: set[Path], dir_path: Path) -> None:
        self._add_path_with_ancestors(container, dir_path)

        def _on_walk_error(_exc):
            return None

        scanned_dirs = 0

        for root_text, dir_names, file_names in os.walk(
            dir_path,
            topdown=True,
            onerror=_on_walk_error,
            followlinks=False,
        ):
            scanned_dirs += 1
            if scanned_dirs % 25 == 0:
                self._yield_during_legacy_search()

            root_path = Path(root_text)
            container.add(root_path)

            for dir_name in dir_names:
                container.add(root_path / dir_name)

            for file_name in file_names:
                container.add(root_path / file_name)

    def _collect_search_visible_paths(self, text: str, base_dir: Path) -> set[Path]:
        query = text.casefold()
        visible_paths: set[Path] = set()

        search_roots = self._search_roots_for_base_dir(base_dir)
        if not search_roots and base_dir != self._root_dir:
            return visible_paths

        self._add_path_with_ancestors(visible_paths, base_dir)

        def _on_walk_error(_exc):
            return None

        scanned_dirs = 0

        for search_root in search_roots:
            if self._is_search_excluded_path(search_root):
                continue

            if query in search_root.name.casefold():
                self._add_dir_tree_paths(visible_paths, search_root)

            for root_text, dir_names, file_names in os.walk(
                search_root,
                topdown=True,
                onerror=_on_walk_error,
                followlinks=False,
            ):
                scanned_dirs += 1
                if scanned_dirs % 25 == 0:
                    self._yield_during_legacy_search()

                root_path = Path(root_text)

                if self._is_search_excluded_path(root_path):
                    dir_names[:] = []
                    continue

                allowed_dir_names: list[str] = []

                for dir_name in dir_names:
                    dir_path = root_path / dir_name

                    if self._is_search_excluded_path(dir_path):
                        continue

                    allowed_dir_names.append(dir_name)

                    if query in dir_name.casefold():
                        self._add_dir_tree_paths(visible_paths, dir_path)

                dir_names[:] = allowed_dir_names

                for file_name in file_names:
                    file_path = root_path / file_name

                    if self._is_search_excluded_path(file_path):
                        continue

                    if query in file_name.casefold():
                        self._add_path_with_ancestors(visible_paths, file_path)

        return visible_paths

    def _clear_row_hidden_recursive(self, parent_index) -> None:
        tree_model = self.tree.model()
        if tree_model is None:
            return

        rows = tree_model.rowCount(parent_index)

        for row in range(rows):
            index = tree_model.index(row, 0, parent_index)
            if not index.isValid():
                continue

            self.tree.setRowHidden(row, parent_index, False)

            path = self._path_from_index(index)
            if path is not None and path.is_dir():
                self._clear_row_hidden_recursive(index)

    def _apply_row_visibility_recursive(
        self,
        parent_index,
        visible_paths: set[Path],
    ) -> None:
        tree_model = self.tree.model()
        if tree_model is None:
            return

        rows = tree_model.rowCount(parent_index)

        for row in range(rows):
            index = tree_model.index(row, 0, parent_index)
            if not index.isValid():
                continue

            path = self._path_from_index(index)
            if path is None:
                continue

            hidden = path not in visible_paths
            self.tree.setRowHidden(row, parent_index, hidden)

            if path.is_dir():
                self._apply_row_visibility_recursive(index, visible_paths)

    def _expand_paths_for_search(self, visible_paths: set[Path]) -> None:
        for path in sorted(visible_paths, key=lambda p: len(p.parts)):
            if not path.exists() or not path.is_dir():
                continue

            index = self._tree_index_for_path(path)
            if index.isValid():
                self.tree.expand(index)

    def _apply_search_now(self) -> None:
        if self._search_applying:
            return

        self._search_applying = True
        try:
            text = self.search_edit.text().strip()
            if not text:
                self._finish_search_session()
                return

            base_dir = self._search_scope_dir()
            if not base_dir.exists() or not base_dir.is_dir():
                base_dir = self._root_dir

            # Indexed-mode: быстрый поиск по локальному SQLite-индексу.
            if self._can_use_indexed_search():
                if self._search_mode == "legacy":
                    self._restore_tree_after_search()

                self._search_mode = "indexed"

                paths = self._indexed_search_paths(
                    text=text,
                    base_dir=base_dir,
                    limit=100,
                )
                self._populate_search_results(paths)
                return

            # Legacy-mode: прямой обход дерева и сетевых каталогов.
            self._clear_search_results()
            self._search_mode = "legacy"

            visible_paths = self._collect_search_visible_paths(text, base_dir)

            for path in sorted(visible_paths, key=lambda p: len(p.parts)):
                self.model.index(str(path))

            root_index = self._tree_index_for_path(self._root_dir)
            if not root_index.isValid():
                return

            self._apply_row_visibility_recursive(root_index, visible_paths)
            self._expand_paths_for_search(visible_paths)

        finally:
            self._search_applying = False

    # --- Файловые операции проводника: inline rename, drag&drop и контекстное меню ---
    def _get_default_open_dialog_dir(self) -> Path:
        try:
            prefs = load_effective_app_preferences()
            dialog_dir = prefs.dialog_dir
            if dialog_dir is not None:
                path = Path(dialog_dir).expanduser()
                if path.exists() and path.is_dir():
                    return path
        except Exception:
            pass

        if DEFAULT_DIALOG_DIR is not None:
            path = Path(DEFAULT_DIALOG_DIR).expanduser()
            if path.exists() and path.is_dir():
                return path

        desktop_dir = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
        if desktop_dir:
            path = Path(desktop_dir)
            if path.exists() and path.is_dir():
                return path

        documents_dir = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation
        )
        if documents_dir:
            path = Path(documents_dir)
            if path.exists() and path.is_dir():
                return path

        return Path.home()

    def _start_inline_edit(self, path: Path) -> None:
        index = self._tree_index_for_path(path)
        if not index.isValid():
            self.refresh()
            index = self._tree_index_for_path(path)
            if not index.isValid():
                return

        self._expand_path_chain(path)

        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)

        QTimer.singleShot(0, lambda idx=index: self.tree.edit(idx))

    def _make_unique_folder_name(
        self, parent_dir: Path, base_name: str = "Новая папка"
    ) -> str:
        candidate = base_name
        n = 2
        while (parent_dir / candidate).exists():
            candidate = f"{base_name} ({n})"
            n += 1
        return candidate

    def _cleanup_aborted_new_folder(self, path: Path) -> None:
        try:
            if path.exists() and path.is_dir():
                with acquire_shared_write_lock(
                    operation_name="отмена создания папки в проводнике"
                ):
                    fs_delete_empty_dir(path)
        except SharedWriteLockBusyError as e:
            QMessageBox.warning(self, "Операция недоступна", str(e))
        except Exception as e:
            QMessageBox.warning(
                self,
                "Не удалось удалить временную папку",
                f"{e}",
            )

        self.refresh()

        parent_index = self._tree_index_for_path(path.parent)
        if parent_index.isValid():
            self.tree.expand(parent_index)
            self.tree.setCurrentIndex(parent_index)
            self.tree.scrollTo(parent_index)

        self.tree.viewport().update()

    def _path_from_index(self, index) -> Path | None:
        if not index.isValid():
            return None

        source_index = self._map_to_source(index)
        if not source_index.isValid() or getattr(self, "model", None) is None:
            return None

        return Path(self.model.filePath(source_index))

    def _run_after_menu_close(self, callback: Callable[[], None]) -> None:
        QTimer.singleShot(0, callback)

    def _expand_path_chain(self, path: Path) -> None:
        target_dir = path if path.is_dir() else path.parent

        chain: list[Path] = []
        current = target_dir

        while True:
            chain.append(current)

            if current == self._root_dir:
                break

            if current == current.parent:
                break

            current = current.parent

        for dir_path in reversed(chain):
            index = self._tree_index_for_path(dir_path)
            if index.isValid():
                self.tree.expand(index)

    def _select_path_in_tree(self, path: Path) -> None:
        self._expand_path_chain(path)

        index = self._tree_index_for_path(path)
        if not index.isValid():
            return

        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        self.tree.viewport().update()

    def _apply_pending_inline_commit(self) -> None:
        pending = self._pending_inline_commit
        self._pending_inline_commit = None

        if not pending:
            return

        kind = str(pending.get("kind") or "")
        old_path_text = str(pending.get("path") or "")
        new_name = str(pending.get("new_name") or "").strip()

        if not old_path_text:
            return

        old_path = Path(old_path_text)

        if not old_path.exists():
            self.refresh()
            return

        is_dir = old_path.is_dir()

        err = self._validate_fs_name(
            new_name,
            "папка" if is_dir else "файл",
        )
        if err:
            QMessageBox.warning(self, "Некорректное имя", err)
            if kind == "new_folder":
                self._cleanup_aborted_new_folder(old_path)
            else:
                self.refresh()
                self._select_path_in_tree(old_path)
            return

        final_path = old_path.with_name(new_name)

        protected, reason = self._is_protected_path(
            final_path,
            is_dir_hint=is_dir,
        )
        if protected:
            self._warn_protected(final_path, reason)
            if kind == "new_folder":
                self._cleanup_aborted_new_folder(old_path)
            else:
                self.refresh()
                self._select_path_in_tree(old_path)
            return

        if final_path.exists() and final_path != old_path:
            QMessageBox.warning(
                self,
                "Объект уже существует",
                f"Объект уже существует:\n{final_path}",
            )
            if kind == "new_folder":
                self._cleanup_aborted_new_folder(old_path)
            else:
                self.refresh()
                self._select_path_in_tree(old_path)
            return

        if not self._request_password():
            if kind == "new_folder":
                self._cleanup_aborted_new_folder(old_path)
            else:
                self.refresh()
                self._select_path_in_tree(old_path)
            return

        # Новая папка может остаться с именем по умолчанию:
        # пароль уже подтвержден, дополнительный rename не требуется.
        if final_path == old_path:
            self.refresh()
            self._select_path_in_tree(old_path)
            self._request_index_rebuild()
            return

        try:
            with acquire_shared_write_lock(
                operation_name="переименование объекта в проводнике"
            ):
                fs_rename_path(old_path, final_path)
        except SharedWriteLockBusyError as e:
            QMessageBox.warning(self, "Операция недоступна", str(e))
            if kind == "new_folder":
                self._cleanup_aborted_new_folder(old_path)
            else:
                self.refresh()
                self._select_path_in_tree(old_path)
            return
        except Exception as e:
            QMessageBox.warning(
                self,
                "Не удалось переименовать",
                f"{e}",
            )
            if kind == "new_folder":
                self._cleanup_aborted_new_folder(old_path)
            else:
                self.refresh()
                self._select_path_in_tree(old_path)
            return

        self.refresh()
        self._select_path_in_tree(final_path)
        self._request_index_rebuild()

    def _activate_index(self, index) -> None:
        path = self._path_from_index(index)
        if path is None:
            return

        if path.is_dir():
            if self.tree.isExpanded(index):
                self.tree.collapse(index)
            else:
                self.tree.expand(index)
            return

        if path.is_file():
            self._open_path(path)

    def _on_double_clicked(self, index) -> None:
        path = self._path_from_index(index)
        if path is None:
            return

        if path.is_file():
            self._open_path(path)

    def _show_context_menu(self, pos) -> None:
        index = self.tree.indexAt(pos)
        clicked_path = self._path_from_index(index)

        # Контекстное меню по пустому месту относим к корню панели, а не к выделению.
        path = clicked_path if clicked_path is not None else self._root_dir

        if self._active_context_menu is not None:
            try:
                self._active_context_menu.close()
            except Exception:
                pass
            self._active_context_menu = None

        menu = QMenu(self.tree)
        menu.setAttribute(Qt.WA_DeleteOnClose, True)
        self._active_context_menu = menu
        menu.aboutToHide.connect(
            lambda m=menu: (
                setattr(self, "_active_context_menu", None)
                if self._active_context_menu is m
                else None
            )
        )

        act_open = menu.addAction("Открыть")
        act_reveal = menu.addAction(
            "Показать в проводнике" if path.is_file() else "Открыть в проводнике"
        )

        menu.addSeparator()

        act_refresh = menu.addAction("Обновить")

        target_dir = path if path.is_dir() else path.parent

        can_manage_children, _ = self._can_manage_children_in(target_dir)
        path_protected, _ = self._is_protected_path(
            path,
            is_dir_hint=path.is_dir(),
        )

        act_add_files = None
        act_new_folder = None
        act_rename = None
        act_delete = None

        if target_dir.exists() and can_manage_children:
            menu.addSeparator()
            act_add_files = menu.addAction("Добавить файлы...")
            act_new_folder = menu.addAction("Создать папку...")

        if path != self._root_dir and not path_protected:
            menu.addSeparator()
            act_rename = menu.addAction("Переименовать...")
            act_delete = menu.addAction("Удалить")

        act_open.triggered.connect(
            lambda _checked=False, p=path: self._run_after_menu_close(
                lambda p=p: self._open_path(p)
            )
        )
        act_reveal.triggered.connect(
            lambda _checked=False, p=path: self._run_after_menu_close(
                lambda p=p: self._reveal_in_system(p)
            )
        )
        act_refresh.triggered.connect(
            lambda _checked=False: self._run_after_menu_close(self.refresh)
        )

        if act_add_files is not None:
            act_add_files.triggered.connect(
                lambda _checked=False, td=target_dir: self._run_after_menu_close(
                    lambda td=td: self._add_files(td)
                )
            )

        if act_new_folder is not None:
            act_new_folder.triggered.connect(
                lambda _checked=False, td=target_dir: self._run_after_menu_close(
                    lambda td=td: self._create_folder(td)
                )
            )

        if act_rename is not None:
            act_rename.triggered.connect(
                lambda _checked=False, p=path: self._run_after_menu_close(
                    lambda p=p: self._rename_path(p)
                )
            )

        if act_delete is not None:
            act_delete.triggered.connect(
                lambda _checked=False, p=path: self._run_after_menu_close(
                    lambda p=p: self._delete_path(p)
                )
            )

        menu.popup(self.tree.viewport().mapToGlobal(pos))
        menu.setFocus()

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))
                return

            ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            if not ok:
                raise RuntimeError("Системное приложение не открыло файл.")
        except Exception as e:
            QMessageBox.warning(
                self,
                "Не удалось открыть",
                f"Не удалось открыть:\n{path}\n\n{e}",
            )

    def _reveal_in_system(self, path: Path) -> None:
        try:
            if sys.platform.startswith("win"):
                if path.is_file():
                    subprocess.Popen(["explorer", "/select,", str(path)])
                else:
                    os.startfile(str(path))
                return

            target = path.parent if path.is_file() else path
            ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
            if not ok:
                raise RuntimeError("Системный проводник не открылся.")
        except Exception as e:
            QMessageBox.warning(
                self,
                "Не удалось открыть проводник",
                f"{e}",
            )

    def _request_password(self) -> bool:
        if self._password_callback is None:
            return True
        return bool(self._password_callback())

    def _warn_protected(self, path: Path, reason: str) -> None:
        QMessageBox.warning(
            self,
            "Операция запрещена",
            f"{reason}\n\n{path}",
        )

    def _validate_fs_name(self, name: str, what: str) -> str | None:
        name = (name or "").strip()

        if not name:
            return f"Не задано имя для объекта: {what}."

        if name in {".", ".."}:
            return "Недопустимое имя."

        if _INVALID_FS_CHARS_RE.search(name):
            return f"Имя содержит недопустимые символы для Windows:\n{what} = {name}"

        return None

    def _looks_like_patient_folder_rel(self, rel_parts: tuple[str, ...]) -> bool:
        if len(rel_parts) != 2:
            return False

        folder_name = rel_parts[1]
        if folder_name == "source_files":
            return False

        base_name, _last_block, _birth, _sex = split_patient_folder_name(folder_name)

        if "_" not in base_name:
            return False

        parts = base_name.split("_", 1)
        if len(parts) != 2:
            return False

        last_block = parts[0].strip()
        name_block = parts[1].strip()

        return bool(
            _PATIENT_LAST_BLOCK_RE.fullmatch(last_block)
            and _PATIENT_NAME_BLOCK_RE.fullmatch(name_block)
        )

    def _looks_like_study_folder_rel(self, rel_parts: tuple[str, ...]) -> bool:
        if len(rel_parts) != 3:
            return False

        patient_rel = rel_parts[:2]
        if not self._looks_like_patient_folder_rel(patient_rel):
            return False

        return parse_ddmmyyyy(rel_parts[2]) is not None

    def _is_protected_path(
        self,
        path: Path,
        *,
        is_dir_hint: bool | None = None,
    ) -> tuple[bool, str]:
        try:
            rel = path.resolve(strict=False).relative_to(
                self._root_dir.resolve(strict=False)
            )
        except Exception:
            return True, "Операции вне текущего ROOT_DIR запрещены."

        if rel == Path("."):
            return True, (
                "Операции с корневой папкой разрешены только через настройки приложения."
            )

        is_dir = is_dir_hint if is_dir_hint is not None else path.is_dir()
        parts = rel.parts

        if path.name in MANAGED_STUDY_FILE_NAMES:
            return True, (
                f"Операции с файлами «{class_result_file_name(1)}», «{class_result_file_name(2)}» "
                "и файлами заключения разрешены только через предназначенный для импорта раздел приложения."
            )

        if is_dir and len(parts) == 1:
            return True, (
                "Операции с папками первого уровня разрешены только "
                "через предназначенный для импорта раздел приложения."
            )

        if len(parts) >= 2 and parts[1] == "source_files":
            return True, (
                "Операции с папкой source_files и любыми файлами внутри неё "
                "разрешены только через предназначенный для импорта раздел приложения."
            )

        if is_dir and self._looks_like_patient_folder_rel(parts):
            return True, (
                "Операции с папками пациентов разрешены только "
                "через предназначенный для импорта раздел приложения."
            )

        if is_dir and self._looks_like_study_folder_rel(parts):
            return True, (
                "Операции с папками исследований разрешены только "
                "через предназначенный для импорта раздел приложения."
            )

        return False, ""

    def _can_manage_children_in(self, dir_path: Path) -> tuple[bool, str]:
        try:
            rel = dir_path.resolve(strict=False).relative_to(
                self._root_dir.resolve(strict=False)
            )
        except Exception:
            return False, "Операции вне текущего ROOT_DIR запрещены."

        if rel == Path("."):
            return False, ("Создание файлов и папок в корне ROOT_DIR не разрешено.")

        parts = rel.parts

        if len(parts) >= 2 and parts[1] == "source_files":
            return False, (
                "Создание новых файлов и папок в source_files, "
                "а также любые действия с её содержимым разрешены только "
                "через предназначенный для импорта раздел приложения."
            )

        return True, ""

    def _drop_target_dir_for_pos(self, pos) -> Path | None:
        index = self.tree.indexAt(pos)
        path = self._path_from_index(index)

        if path is None:
            return self._root_dir

        return path if path.is_dir() else path.parent

    def _handle_external_drop(
        self,
        source_paths: list[Path],
        target_dir: Path,
    ) -> bool:
        allowed, reason = self._can_manage_children_in(target_dir)
        if not allowed:
            self._warn_protected(target_dir, reason)
            return False

        if not source_paths:
            return False

        if not self._request_password():
            return False

        copied: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        try:
            with acquire_shared_write_lock(
                operation_name="добавление файлов перетаскиванием"
            ):
                for src in source_paths:
                    dst = target_dir / src.name
                    self._copy_incoming_path(src, dst, copied, skipped, failed)
        except SharedWriteLockBusyError as e:
            QMessageBox.warning(self, "Операция недоступна", str(e))
            return False
        except Exception as e:
            QMessageBox.warning(
                self,
                "Операция не выполнена",
                f"{e}",
            )
            return False

        self.refresh()

        if copied:
            self._request_index_rebuild()

        parts: list[str] = []
        if copied:
            parts.append("Добавлены:\n" + "\n".join(copied))
        if skipped:
            parts.append("Пропущены:\n" + "\n".join(skipped))
        if failed:
            parts.append("Ошибки:\n" + "\n".join(failed))

        if parts:
            QMessageBox.information(self, "Готово", "\n\n".join(parts))

        return True

    def _copy_incoming_path(
        self,
        src: Path,
        dst: Path,
        copied: list[str],
        skipped: list[str],
        failed: list[str],
    ) -> None:
        if not src.exists():
            skipped.append(f"{src.name} — объект не найден.")
            return

        if src.is_symlink():
            skipped.append(f"{src.name} — символьные ссылки не поддерживаются.")
            return

        try:
            if src.resolve() == dst.resolve():
                skipped.append(f"{dst.name} — исходный и целевой путь совпадают.")
                return
        except Exception:
            pass

        protected, reason = self._is_protected_path(dst, is_dir_hint=src.is_dir())
        if protected:
            skipped.append(f"{dst.name} — {reason}")
            return

        try:
            if src.is_dir():
                self._copy_incoming_dir(src, dst, copied, skipped, failed)
            elif src.is_file():
                self._copy_incoming_file(src, dst, copied, skipped)
            else:
                skipped.append(f"{src.name} — неподдерживаемый тип объекта.")
        except Exception as e:
            failed.append(f"{src.name} — {e}")

    def _is_copy_dir_into_self_or_descendant(self, src: Path, dst: Path) -> bool:
        try:
            src_resolved = src.resolve()
            dst_resolved = dst.resolve(strict=False)
        except Exception:
            return False

        try:
            return dst_resolved == src_resolved or dst_resolved.is_relative_to(
                src_resolved
            )
        except AttributeError:
            dst_parts = dst_resolved.parts
            src_parts = src_resolved.parts
            return (
                len(dst_parts) >= len(src_parts)
                and dst_parts[: len(src_parts)] == src_parts
            )

    def _copy_incoming_file(
        self,
        src: Path,
        dst: Path,
        copied: list[str],
        skipped: list[str],
    ) -> None:
        if dst.exists():
            if dst.is_dir():
                protected, reason = self._is_protected_path(dst, is_dir_hint=True)
                if protected:
                    skipped.append(f"{dst.name} — {reason}")
                    return

                ans = QMessageBox.warning(
                    self,
                    "Конфликт имён",
                    f"Уже существует папка:\n{dst}\n\nЗаменить её файлом?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if ans != QMessageBox.Yes:
                    skipped.append(f"{dst.name} — пропущен.")
                    return

                fs_delete_dir_tree(dst)
            else:
                protected, reason = self._is_protected_path(dst, is_dir_hint=False)
                if protected:
                    skipped.append(f"{dst.name} — {reason}")
                    return

                ans = QMessageBox.warning(
                    self,
                    "Файл уже существует",
                    f"Файл уже существует:\n{dst}\n\nЗаменить его?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if ans != QMessageBox.Yes:
                    skipped.append(f"{dst.name} — пропущен.")
                    return

        fs_copy_file(src, dst)
        copied.append(str(dst))

    def _copy_incoming_dir(
        self,
        src: Path,
        dst: Path,
        copied: list[str],
        skipped: list[str],
        failed: list[str],
    ) -> None:
        if self._is_copy_dir_into_self_or_descendant(src, dst):
            skipped.append(
                f"{src.name} — нельзя копировать папку в неё саму или в её вложенную папку."
            )
            return

        if dst.exists():
            if dst.is_file():
                ans = QMessageBox.warning(
                    self,
                    "Конфликт имён",
                    f"Уже существует файл:\n{dst}\n\nЗаменить его папкой?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if ans != QMessageBox.Yes:
                    skipped.append(f"{dst.name} — пропущен.")
                    return

                fs_delete_file(dst)
                fs_create_dir(dst, parents=True, exist_ok=False)
                copied.append(str(dst))
        else:
            fs_create_dir(dst, parents=True, exist_ok=False)
            copied.append(str(dst))

        for child in src.iterdir():
            child_dst = dst / child.name
            self._copy_incoming_path(child, child_dst, copied, skipped, failed)

    def _add_files(self, target_dir: Path) -> None:
        allowed, reason = self._can_manage_children_in(target_dir)
        if not allowed:
            self._warn_protected(target_dir, reason)
            return

        start_dir = self._get_default_open_dialog_dir()

        file_names, _ = QFileDialog.getOpenFileNames(
            self,
            "Выберите файлы для добавления",
            str(start_dir),
            "Все файлы (*)",
        )
        if not file_names:
            return

        if not self._request_password():
            return

        copied: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        try:
            with acquire_shared_write_lock(
                operation_name="добавление файлов в проводнике"
            ):
                for file_name in file_names:
                    src = Path(file_name)
                    dst = target_dir / src.name

                    protected, reason = self._is_protected_path(dst, is_dir_hint=False)
                    if protected:
                        skipped.append(f"{dst.name} — {reason}")
                        continue

                    try:
                        if src.resolve() == dst.resolve():
                            skipped.append(
                                f"{dst.name} — исходный и целевой путь совпадают."
                            )
                            continue
                    except Exception:
                        pass

                    if dst.exists():
                        if dst.is_dir():
                            protected, reason = self._is_protected_path(
                                dst, is_dir_hint=True
                            )
                            if protected:
                                skipped.append(f"{dst.name} — {reason}")
                            else:
                                skipped.append(
                                    f"{dst.name} — в целевой папке уже существует папка с таким именем."
                                )
                            continue

                        protected, reason = self._is_protected_path(
                            dst, is_dir_hint=False
                        )
                        if protected:
                            skipped.append(f"{dst.name} — {reason}")
                            continue

                        ans = QMessageBox.warning(
                            self,
                            "Файл уже существует",
                            f"Файл уже существует:\n{dst}\n\nЗаменить его?",
                            QMessageBox.Yes | QMessageBox.Cancel,
                            QMessageBox.Cancel,
                        )
                        if ans != QMessageBox.Yes:
                            skipped.append(f"{dst.name} — пропущен.")
                            continue

                    try:
                        fs_copy_file(src, dst)
                        copied.append(str(dst))
                    except Exception as e:
                        failed.append(f"{dst.name} — {e}")
        except SharedWriteLockBusyError as e:
            QMessageBox.warning(self, "Операция недоступна", str(e))
            return
        except Exception as e:
            QMessageBox.warning(
                self,
                "Не удалось добавить файлы",
                f"{e}",
            )
            return

        self.refresh()

        if copied:
            self._request_index_rebuild()

            first_copied_path = Path(copied[0])
            self._select_path_in_tree(first_copied_path)

        parts: list[str] = []
        if skipped:
            parts.append("Пропущены:\n" + "\n".join(skipped))
        if failed:
            parts.append("Ошибки:\n" + "\n".join(failed))

        if parts:
            QMessageBox.information(self, "Готово", "\n\n".join(parts))

    def _create_folder(self, parent_dir: Path) -> None:
        allowed, reason = self._can_manage_children_in(parent_dir)
        if not allowed:
            self._warn_protected(parent_dir, reason)
            return

        try:
            with acquire_shared_write_lock(
                operation_name="создание папки в проводнике"
            ):
                folder_name = self._make_unique_folder_name(parent_dir)
                new_dir = parent_dir / folder_name

                protected, reason = self._is_protected_path(new_dir, is_dir_hint=True)
                if protected:
                    self._warn_protected(new_dir, reason)
                    return

                fs_create_dir(new_dir, exist_ok=False)
        except SharedWriteLockBusyError as e:
            QMessageBox.warning(self, "Операция недоступна", str(e))
            return
        except Exception as e:
            QMessageBox.warning(
                self,
                "Не удалось создать папку",
                f"{e}",
            )
            return

        self.refresh()

        self._pending_inline_commit = None
        self._inline_edit_context = {
            "kind": "new_folder",
            "path": new_dir,
        }
        self._start_inline_edit(new_dir)

    def _rename_path(self, path: Path) -> None:
        protected, reason = self._is_protected_path(path)
        if protected:
            self._warn_protected(path, reason)
            return

        self._pending_inline_commit = None
        self._inline_edit_context = {
            "kind": "rename",
            "path": path,
        }
        self._start_inline_edit(path)

    def _delete_path(self, path: Path) -> None:
        protected, reason = self._is_protected_path(path)
        if protected:
            self._warn_protected(path, reason)
            return

        path_str = str(path)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Подтверждение удаления")
        box.setText(
            f"Вы действительно хотите удалить:\n{path_str}\n\nОперация необратима."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)

        fm = QFontMetrics(box.font())
        content_width = fm.horizontalAdvance(path_str) + 100

        layout = box.layout()
        if layout is not None:
            layout.addItem(
                QSpacerItem(
                    content_width,
                    0,
                    QSizePolicy.Minimum,
                    QSizePolicy.Expanding,
                ),
                layout.rowCount(),
                0,
                1,
                layout.columnCount(),
            )

        if box.exec() != QMessageBox.Yes:
            return

        if not self._request_password():
            return

        parent_path = path.parent

        try:
            with acquire_shared_write_lock(
                operation_name="удаление объекта в проводнике"
            ):
                if path.is_dir():
                    fs_delete_dir_tree(path)
                else:
                    fs_delete_file(path)

            def finalize():
                self.refresh()
                self._request_index_rebuild()

                parent_index = self._tree_index_for_path(parent_path)
                if parent_index.isValid():
                    self.tree.expand(parent_index)
                    self.tree.setCurrentIndex(parent_index)
                    self.tree.scrollTo(parent_index)

                self.tree.viewport().update()

            QTimer.singleShot(0, finalize)

        except SharedWriteLockBusyError as e:
            QMessageBox.warning(self, "Операция недоступна", str(e))
            return
        except Exception as e:
            QMessageBox.warning(
                self,
                "Не удалось удалить",
                f"{e}",
            )
            return
