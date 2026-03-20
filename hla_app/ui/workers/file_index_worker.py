from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from hla_app.services.file_tree_index import FileTreeIndexService


class FileTreeIndexBuildSignals(QObject):
    finished = Signal(str, int)
    failed = Signal(str, str)


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
            self.signals.finished.emit(str(self.root_dir), count)
        except Exception as exc:
            self.signals.failed.emit(str(self.root_dir), str(exc))
