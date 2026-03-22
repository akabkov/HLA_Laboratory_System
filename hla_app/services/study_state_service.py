"""Снимки состояния исследования и восстановление после ошибок.

Файл отвечает за capture/restore состояния пациента и исследования перед
опасными операциями замены или удаления: backup файлов, снимки каталогов,
восстановление `source_files` и тестовых папок. Если rollback сработал не так,
как ожидалось, основная логика находится здесь.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from tempfile import mkdtemp

from sqlalchemy import text

from hla_app.config.managed_files import CONCLUSION_FILE_NAMES, class_result_file_name
from hla_app.db.engine import get_engine
from hla_app.db.repo import (
    delete_orphan_patient_by_code,
    delete_test_by_patient_code,
    insert_antibodies,
    insert_organ,
    insert_patient,
    insert_test,
    update_existing_patient_by_code,
)
from hla_app.storage.fs_ops import (
    build_source_csv_path,
    get_base_tree_paths,
    list_source_class_files,
    list_source_files_for_patient,
)
from hla_app.storage.fs_ops import (
    copy_dir_tree as fs_copy_dir_tree,
)
from hla_app.storage.fs_ops import (
    copy_file as fs_copy_file,
)
from hla_app.storage.fs_ops import (
    delete_dir_tree as fs_delete_dir_tree,
)
from hla_app.storage.fs_ops import (
    delete_file as fs_delete_file,
)
from hla_app.storage.fs_ops import (
    rename_path as fs_rename_path,
)
from hla_app.utils.validators import format_ddmmyyyy


# --- Модели backup-файлов и снимка состояния исследования ---
@dataclass(frozen=True)
class FileBackupRecord:
    original: Path
    backup: Path


@dataclass(frozen=True)
class PatientSnapshot:
    patient_code: str
    last_name: str
    new_last_name: str | None
    first_name: str
    middle_name: str | None
    birth_date: date
    sex: str | None
    recipient_code: int | None


@dataclass(frozen=True)
class TestSnapshot:
    hla_class: int
    pra: int
    antibodies: list[dict]


@dataclass
class StudyStateSnapshot:
    backup_root: Path
    organ: str
    patient_code: str
    patient_dir: Path
    source_dir: Path
    test_date: date
    classes: list[int] = field(default_factory=list)
    test_dir_existed: bool = False
    patient: PatientSnapshot | None = None
    tests: list[TestSnapshot] = field(default_factory=list)
    touched_study_files: list[Path] = field(default_factory=list)
    touched_source_files: list[Path] = field(default_factory=list)
    renamed_patient_dir: Path | None = None
    renamed_patient_code: str | None = None
    study_file_backups: list[FileBackupRecord] = field(default_factory=list)
    source_file_backups: list[FileBackupRecord] = field(default_factory=list)
    full_test_dir_backup: Path | None = None


# --- Вспомогательные функции сбора и backup файлового состояния ---
def _deduplicate_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []

    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)

    return result


def _backup_existing_files(
    paths: list[Path], backup_group_dir: Path
) -> list[FileBackupRecord]:
    backup_group_dir.mkdir(parents=True, exist_ok=True)
    result: list[FileBackupRecord] = []

    for index, original in enumerate(_deduplicate_paths(paths), start=1):
        if not original.exists() or not original.is_file():
            continue

        backup_path = backup_group_dir / f"{index:03d}__{original.name}"
        fs_copy_file(original, backup_path)
        result.append(FileBackupRecord(original=original, backup=backup_path))

    return result


def _backup_directory_tree(src_dir: Path, backup_dir: Path) -> Path | None:
    if not src_dir.exists() or not src_dir.is_dir():
        return None

    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)

    fs_copy_dir_tree(src_dir, backup_dir)
    return backup_dir


# --- Сбор снимков пациента и тестов из PostgreSQL ---
def _collect_patient_snapshot(conn, patient_code: str) -> PatientSnapshot | None:
    row = (
        conn.execute(
            text("""--sql
                SELECT
                    patient_code,
                    last_name,
                    new_last_name,
                    first_name,
                    middle_name,
                    birth_date,
                    sex,
                    recipient_code
                FROM
                    patients
                WHERE
                    patient_code = :patient_code
            """),
            {"patient_code": patient_code},
        )
        .mappings()
        .first()
    )

    if row is None:
        return None

    return PatientSnapshot(
        patient_code=row["patient_code"],
        last_name=row["last_name"],
        new_last_name=row["new_last_name"],
        first_name=row["first_name"],
        middle_name=row["middle_name"],
        birth_date=row["birth_date"],
        sex=row["sex"],
        recipient_code=row["recipient_code"],
    )


def _collect_test_snapshots(
    conn,
    *,
    organ: str,
    patient_code: str,
    test_date: date,
    classes: list[int],
) -> list[TestSnapshot]:
    result: list[TestSnapshot] = []

    for hla_class in sorted(set(classes)):
        test_row = (
            conn.execute(
                text("""--sql
                    SELECT
                        t.id,
                        t.pra
                    FROM
                        tests t
                        JOIN patients p ON t.patient_id = p.id
                        JOIN organs o ON p.organ_id = o.id
                    WHERE
                        o.title = :organ
                        AND p.patient_code = :patient_code
                        AND t.test_date = :test_date
                        AND t.hla_class = :hla_class
                """),
                {
                    "organ": organ,
                    "patient_code": patient_code,
                    "test_date": test_date,
                    "hla_class": hla_class,
                },
            )
            .mappings()
            .first()
        )

        if test_row is None:
            continue

        antibodies_rows = (
            conn.execute(
                text("""--sql
                    SELECT
                        gene,
                        allele_group,
                        allele,
                        pct_positive,
                        raw_value,
                        mfi_lra
                    FROM
                        antibodies
                    WHERE
                        test_id = :test_id
                    ORDER BY
                        gene,
                        allele_group,
                        allele,
                        raw_value
                """),
                {"test_id": test_row["id"]},
            )
            .mappings()
            .all()
        )

        antibodies = [
            {
                "gene": row["gene"],
                "allele_group": row["allele_group"],
                "allele": row["allele"],
                "pct_positive": row["pct_positive"],
                "raw_value": row["raw_value"],
                "mfi_lra": row["mfi_lra"],
            }
            for row in antibodies_rows
        ]

        pra_value = test_row["pra"]
        if pra_value is None:
            raise RuntimeError(
                "В БД обнаружен тест без PRA, что противоречит схеме таблицы tests."
            )

        result.append(
            TestSnapshot(
                hla_class=hla_class,
                pra=int(pra_value),
                antibodies=antibodies,
            )
        )

    return result


# --- Захват полного snapshot перед опасной операцией ---
def capture_study_state_snapshot(
    *,
    root_dir: Path,
    organ: str,
    patient_code: str,
    patient_dir: Path,
    test_date: date,
    classes: list[int],
    include_conclusion: bool,
    include_all_patient_source_files: bool = False,
    include_full_test_dir_tree: bool = False,
) -> StudyStateSnapshot:
    backup_root = Path(mkdtemp(prefix="hla_study_backup__"))

    _, src_dir = get_base_tree_paths(root_dir, organ)
    test_dir = patient_dir / format_ddmmyyyy(test_date)

    touched_study_files: list[Path] = []
    touched_source_files: list[Path] = []

    full_test_dir_backup: Path | None = None

    if include_full_test_dir_tree:
        full_test_dir_backup = _backup_directory_tree(
            test_dir,
            backup_root / "full_test_dir",
        )

    for hla_class in sorted(set(classes)):
        touched_study_files.append(test_dir / class_result_file_name(hla_class))

        expected_source_csv = build_source_csv_path(
            src_dir=src_dir,
            organ=organ,
            patient_folder_name=patient_dir.name,
            analysis_date=test_date,
            hla_class=hla_class,
        )
        touched_source_files.append(expected_source_csv)

        touched_source_files.extend(
            list_source_class_files(
                src_dir=src_dir,
                organ=organ,
                patient_folder_name=patient_dir.name,
                analysis_date=test_date,
                hla_class=hla_class,
            )
        )

    if include_conclusion:
        for file_name in CONCLUSION_FILE_NAMES:
            touched_study_files.append(test_dir / file_name)

    if include_all_patient_source_files:
        touched_source_files.extend(
            list_source_files_for_patient(
                src_dir=src_dir,
                organ=organ,
                patient_folder_name=patient_dir.name,
            )
        )

    touched_study_files = _deduplicate_paths(touched_study_files)
    touched_source_files = _deduplicate_paths(touched_source_files)

    study_file_backups = _backup_existing_files(
        touched_study_files,
        backup_root / "study_files",
    )
    source_file_backups = _backup_existing_files(
        touched_source_files,
        backup_root / "source_files",
    )

    with get_engine().begin() as conn:
        patient_snapshot = _collect_patient_snapshot(conn, patient_code)
        test_snapshots = _collect_test_snapshots(
            conn,
            organ=organ,
            patient_code=patient_code,
            test_date=test_date,
            classes=classes,
        )

    return StudyStateSnapshot(
        backup_root=backup_root,
        organ=organ,
        patient_code=patient_code,
        patient_dir=patient_dir,
        source_dir=src_dir,
        test_date=test_date,
        classes=sorted(set(classes)),
        test_dir_existed=test_dir.exists() and test_dir.is_dir(),
        patient=patient_snapshot,
        tests=test_snapshots,
        touched_study_files=touched_study_files,
        touched_source_files=touched_source_files,
        study_file_backups=study_file_backups,
        source_file_backups=source_file_backups,
        full_test_dir_backup=full_test_dir_backup,
    )


# --- Восстановление файлов, папок и patient_code после ошибки ---
def _restore_file_group(
    *,
    touched_paths: list[Path],
    backups: list[FileBackupRecord],
) -> None:
    for path in _deduplicate_paths(touched_paths):
        try:
            if path.exists() and path.is_file():
                fs_delete_file(path)
        except FileNotFoundError:
            pass

    for record in backups:
        record.original.parent.mkdir(parents=True, exist_ok=True)
        fs_copy_file(record.backup, record.original)


def _restore_full_test_dir_if_needed(snapshot: StudyStateSnapshot) -> bool:
    if snapshot.full_test_dir_backup is None:
        return False

    test_dir = snapshot.patient_dir / format_ddmmyyyy(snapshot.test_date)

    if test_dir.exists():
        if test_dir.is_dir():
            fs_delete_dir_tree(test_dir)
        else:
            fs_delete_file(test_dir)

    test_dir.parent.mkdir(parents=True, exist_ok=True)
    fs_copy_dir_tree(snapshot.full_test_dir_backup, test_dir)
    return True


def _remove_empty_test_dir_if_needed(snapshot: StudyStateSnapshot) -> None:
    test_dir = snapshot.patient_dir / format_ddmmyyyy(snapshot.test_date)

    if snapshot.test_dir_existed:
        return

    try:
        if test_dir.exists() and test_dir.is_dir() and not any(test_dir.iterdir()):
            test_dir.rmdir()
    except Exception:
        pass


def _restore_patient_dir_name_if_needed(snapshot: StudyStateSnapshot) -> None:
    renamed_patient_dir = snapshot.renamed_patient_dir

    if renamed_patient_dir is None or renamed_patient_dir == snapshot.patient_dir:
        return

    if not renamed_patient_dir.exists() or not renamed_patient_dir.is_dir():
        return

    if snapshot.patient_dir.exists():
        raise RuntimeError(
            "Не удалось откатить переименование папки пациента: "
            "существуют и старая, и новая папки.\n\n"
            f"Старая папка: {snapshot.patient_dir}\n"
            f"Новая папка: {renamed_patient_dir}"
        )

    snapshot.patient_dir.parent.mkdir(parents=True, exist_ok=True)
    fs_rename_path(renamed_patient_dir, snapshot.patient_dir)


def _patient_codes_for_cleanup(snapshot: StudyStateSnapshot) -> list[str]:
    codes = [snapshot.patient_code]

    if snapshot.renamed_patient_code and snapshot.renamed_patient_code not in codes:
        codes.append(snapshot.renamed_patient_code)

    return codes


def _source_paths_to_restore(snapshot: StudyStateSnapshot) -> list[Path]:
    paths = list(snapshot.touched_source_files)

    if snapshot.renamed_patient_dir is not None:
        paths.extend(
            list_source_files_for_patient(
                src_dir=snapshot.source_dir,
                organ=snapshot.organ,
                patient_folder_name=snapshot.renamed_patient_dir.name,
            )
        )

    return _deduplicate_paths(paths)


def _restore_patient_identity(
    conn,
    snapshot: StudyStateSnapshot,
    organ_id: int | None,
) -> int | None:
    if snapshot.patient is None:
        for patient_code in _patient_codes_for_cleanup(snapshot):
            delete_orphan_patient_by_code(conn, patient_code)
        return None

    if snapshot.renamed_patient_code:
        reverted_patient_id = update_existing_patient_by_code(
            conn=conn,
            current_patient_code=snapshot.renamed_patient_code,
            new_patient_code=snapshot.patient.patient_code,
            last_name=snapshot.patient.last_name,
            new_last_name=snapshot.patient.new_last_name,
            first_name=snapshot.patient.first_name,
            middle_name=snapshot.patient.middle_name,
            birth_date=snapshot.patient.birth_date,
            sex=snapshot.patient.sex,
            recipient_code=snapshot.patient.recipient_code,
        )
        if reverted_patient_id is not None:
            return reverted_patient_id

        delete_orphan_patient_by_code(conn, snapshot.renamed_patient_code)

    if organ_id is None:
        raise RuntimeError(
            "Внутренняя ошибка отката: отсутствует organ_id для восстановления пациента."
        )

    return _restore_patient_row(conn, snapshot.patient, organ_id)


def _restore_patient_row(conn, patient: PatientSnapshot, organ_id: int) -> int:
    return insert_patient(
        conn=conn,
        patient_code=patient.patient_code,
        last_name=patient.last_name,
        new_last_name=patient.new_last_name,
        first_name=patient.first_name,
        middle_name=patient.middle_name,
        birth_date=patient.birth_date,
        sex=patient.sex,
        recipient_code=patient.recipient_code,
        organ_id=organ_id,
    )


# --- Публичное восстановление snapshot и его очистка после использования ---
def restore_study_state_snapshot(snapshot: StudyStateSnapshot) -> None:
    _restore_patient_dir_name_if_needed(snapshot)

    restored_full_test_dir = _restore_full_test_dir_if_needed(snapshot)

    if not restored_full_test_dir:
        _restore_file_group(
            touched_paths=snapshot.touched_study_files,
            backups=snapshot.study_file_backups,
        )
        _remove_empty_test_dir_if_needed(snapshot)

    _restore_file_group(
        touched_paths=_source_paths_to_restore(snapshot),
        backups=snapshot.source_file_backups,
    )

    with get_engine().begin() as conn:
        for patient_code in _patient_codes_for_cleanup(snapshot):
            for hla_class in snapshot.classes:
                delete_test_by_patient_code(
                    conn,
                    organ=snapshot.organ,
                    patient_code=patient_code,
                    test_date=snapshot.test_date,
                    hla_class=hla_class,
                )

        organ_id: int | None = None
        if snapshot.patient is not None:
            organ_id = insert_organ(conn, snapshot.organ)

        patient_id = _restore_patient_identity(conn, snapshot, organ_id)

        if snapshot.tests:
            if patient_id is None:
                raise RuntimeError(
                    "Внутренняя ошибка отката: не удалось восстановить пациента перед восстановлением тестов."
                )

            for test_snapshot in snapshot.tests:
                test_id = insert_test(
                    conn=conn,
                    patient_id=patient_id,
                    test_date=snapshot.test_date,
                    hla_class=test_snapshot.hla_class,
                    pra=test_snapshot.pra,
                )
                insert_antibodies(
                    conn,
                    test_id=test_id,
                    antibodies=test_snapshot.antibodies,
                )


def cleanup_study_state_snapshot(snapshot: StudyStateSnapshot) -> None:
    try:
        if snapshot.backup_root.exists():
            shutil.rmtree(snapshot.backup_root, ignore_errors=True)
    except Exception:
        pass
