"""Фоновый воркер построения индекса файлового дерева.

Здесь описаны `Signal`-объект и `QRunnable`, которые запускают тяжелое
построение индекса вне UI-потока и сообщают о прогрессе обратно в интерфейс.
Если индексный поиск падает или зависает при фоновом обновлении, смотреть сюда.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from hla_app.services.file_tree_index import FileTreeIndexService


# --- Сигналы завершения фоновой индексации ---
class FileTreeIndexBuildSignals(QObject):
    finished = Signal(str, int)
    failed = Signal(str, str)


# --- QRunnable-задача перестроения локального индекса ---
class FileTreeIndexBuildTask(QRunnable):
    def __init__(
        self,
        *,
        index_service: FileTreeIndexService,
        root_dir: Path,
    ):
        super().__init__()
        self.index_service = index_service
        self.root_dir = Path(root_dir)
        self.signals = FileTreeIndexBuildSignals()

    def run(self) -> None:
        try:
            count = self.index_service.rebuild(self.root_dir)
            try:
                self.signals.finished.emit(str(self.root_dir), count)
            except RuntimeError:
                return
        except Exception as exc:
            try:
                self.signals.failed.emit(str(self.root_dir), str(exc))
            except RuntimeError:
                return
