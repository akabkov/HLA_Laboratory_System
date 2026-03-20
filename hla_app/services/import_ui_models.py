from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class ImportFormState:
    root_dir: Path
    organ: str
    test_date: date
    birth_date: date
    last_name: str
    new_last_name: str
    first_name: str
    middle_name: str
    sex: str
    delete_classes: list[int]
    delete_conclusion: bool
    delete_whole_study: bool
    has_csv_selection: bool
    has_jpg_selection: bool

    @property
    def base_last_name(self) -> str:
        return self.last_name or self.new_last_name

    @property
    def bracket_last_name(self) -> str:
        return self.new_last_name if self.last_name else ""

    @property
    def has_delete_actions(self) -> bool:
        return bool(
            self.delete_classes or self.delete_conclusion or self.delete_whole_study
        )

    @property
    def has_file_import(self) -> bool:
        return bool(self.has_csv_selection or self.has_jpg_selection)


@dataclass
class ResolvedPatientTarget:
    # Реальная текущая папка пациента на момент выполнения операции.
    # Для существующего пациента это старая папка.
    # Для нового пациента будет заполнена после фактического создания папки.
    current_patient_dir: Path | None = None

    # Финальная папка пациента после всех действий.
    # Если переименования нет, совпадает с current_patient_dir.
    # Для нового пациента до создания может содержать только планируемый путь.
    target_patient_dir: Path | None = None

    old_patient_code_for_db: str | None = None
    new_patient_code_for_db: str | None = None
    preserve_existing_patient_in_db: bool = False
    used_existing_patient_dir: bool = False
    create_new_after_password: bool = False
    create_new_kwargs: dict[str, object] | None = None
    rename_after_password: bool = False
    rename_target_patient_name: str | None = None

    def patient_dir_for_existing_checks(self) -> Path:
        # Для проверок конфликтов можно использовать:
        # - реальную текущую папку, если она уже есть;
        # - либо планируемый путь для нового пациента.
        if self.current_patient_dir is not None:
            return self.current_patient_dir

        if self.target_patient_dir is not None:
            return self.target_patient_dir

        raise RuntimeError("Не выбрана папка пациента.")

    def patient_dir_for_operations(self) -> Path:
        # Для snapshot/delete/import нужна только реальная текущая папка.
        if self.current_patient_dir is None:
            raise RuntimeError("Не выбрана папка пациента.")
        return self.current_patient_dir

    def final_patient_dir(self) -> Path:
        # Для финального пути после rename/use/new.
        if self.target_patient_dir is not None:
            return self.target_patient_dir

        if self.current_patient_dir is not None:
            return self.current_patient_dir

        raise RuntimeError("Не выбрана папка пациента.")
