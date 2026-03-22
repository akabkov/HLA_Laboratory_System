"""Проверка конфликтов перед импортом исследования.

Модуль вычисляет, какие файлы уже существуют в папке исследования и какие
замены потребуются для нового импорта. Если интерфейс неожиданно просит
заменить файлы или, наоборот, пропускает конфликт, разбираться нужно здесь.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hla_app.config.managed_files import (
    CONCLUSION_FILE_NAMES,
    class_result_file_name,
)
from hla_app.storage.fs_ops import list_source_class_files


# --- Модель найденного конфликта перед запуском импорта ---
@dataclass(frozen=True)
class StudyImportConflict:
    file_name: str
    reason: str
    path: Path | None = None


# --- Публичная проверка конфликтов в папке исследования и source_files ---
def find_import_conflicts(
    *,
    test_dir: Path,
    source_dir: Path | None,
    organ: str,
    patient_folder_name: str,
    analysis_date: date,
    class1_selected: bool,
    class2_selected: bool,
    conclusion_selected: bool,
    delete_class1: bool,
    delete_class2: bool,
    delete_conclusion: bool,
) -> list[StudyImportConflict]:
    conflicts: list[StudyImportConflict] = []

    # --- Конфликты в папке исследования ---
    if test_dir.exists() and test_dir.is_dir():
        class1_file = class_result_file_name(1)
        if class1_selected and (test_dir / class1_file).is_file() and not delete_class1:
            conflicts.append(
                StudyImportConflict(
                    file_name=class1_file,
                    reason=" (для замены отметьте «Заменить Сlass I»)",
                    path=test_dir / class1_file,
                )
            )

        class2_file = class_result_file_name(2)
        if class2_selected and (test_dir / class2_file).is_file() and not delete_class2:
            conflicts.append(
                StudyImportConflict(
                    file_name=class2_file,
                    reason=" (для замены отметьте «Заменить Сlass II»)",
                    path=test_dir / class2_file,
                )
            )

        if conclusion_selected and not delete_conclusion:
            for file_name in CONCLUSION_FILE_NAMES:
                file_path = test_dir / file_name
                if file_path.is_file():
                    conflicts.append(
                        StudyImportConflict(
                            file_name=file_name,
                            reason=(" (для замены отметьте «Заменить заключение»)"),
                            path=file_path,
                        )
                    )

    # --- Конфликты в source_files ---
    if source_dir is not None and source_dir.exists() and source_dir.is_dir():
        if class1_selected and not delete_class1:
            source_class1_files = list_source_class_files(
                src_dir=source_dir,
                organ=organ,
                patient_folder_name=patient_folder_name,
                analysis_date=analysis_date,
                hla_class=1,
            )
            if source_class1_files:
                source_class1 = source_class1_files[0]
                conflicts.append(
                    StudyImportConflict(
                        file_name=source_class1.name,
                        reason=(""),
                        path=source_class1,
                    )
                )

        if class2_selected and not delete_class2:
            source_class2_files = list_source_class_files(
                src_dir=source_dir,
                organ=organ,
                patient_folder_name=patient_folder_name,
                analysis_date=analysis_date,
                hla_class=2,
            )
            if source_class2_files:
                source_class2 = source_class2_files[0]
                conflicts.append(
                    StudyImportConflict(
                        file_name=source_class2.name,
                        reason=(""),
                        path=source_class2,
                    )
                )

    return conflicts
