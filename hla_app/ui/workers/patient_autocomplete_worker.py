from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from hla_app.storage.fs_ops import find_patient_folders_by_lastname_prefix


class PatientAutocompleteSignals(QObject):
    finished = Signal(int, str, list)
    failed = Signal(int, str, str)


class PatientAutocompleteTask(QRunnable):
    def __init__(self, *, request_id: int, target: str, organ_dir: Path, query: str):
        super().__init__()
        self.request_id = request_id
        self.target = target
        self.organ_dir = organ_dir
        self.query = query
        self.signals = PatientAutocompleteSignals()

    def run(self) -> None:
        try:
            matches = find_patient_folders_by_lastname_prefix(
                self.organ_dir,
                self.query,
                limit=12,
            )
            self.signals.finished.emit(self.request_id, self.target, matches)
        except Exception as exc:
            self.signals.failed.emit(self.request_id, self.target, str(exc))
