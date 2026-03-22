"""Основной сервис импорта исследования.

Здесь сосредоточена самая критичная логика проекта: подготовка входных данных,
копирование CSV и файла заключения в файловую базу, генерация Excel, обновление PostgreSQL,
backup/restore существующих файлов и rollback при ошибках. Если есть проблема
с заменой, удалением, повторным импортом или рассинхронизацией FS и БД, почти
всегда ключевой код будет в этом модуле.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from hla_app.config.managed_files import CONCLUSION_FILE_NAMES
from hla_app.data.luminex_parser import parse_luminex_csv
from hla_app.db.engine import get_engine
from hla_app.db.repo import (
    delete_orphan_patient_by_code,
    delete_test_by_patient_code,
    get_patient_code_by_recipient_code,
    get_patient_codes_by_recipient_code,
    get_patient_id_by_code,
    get_patient_recipient_code_by_code,
    insert_antibodies,
    insert_organ,
    insert_patient,
    insert_test,
    update_existing_patient_by_code,
    update_patient_recipient_code_by_code,
)
from hla_app.reports.excel_sorted import create_sorted_excel_result
from hla_app.storage.fs_ops import (
    build_source_csv_path,
    delete_conclusion_files,
    delete_file,
    delete_patient_test_dir_tree,
    delete_patient_test_files,
    delete_source_class_files,
    ensure_base_tree,
    ensure_test_date_folder,
    replace_file,
    save_conclusion_file,
    save_source_csv,
)


# --- Входные и выходные модели основного сценария импорта ---
@dataclass(frozen=True)
class PatientData:
    organ: str
    patient_code: str
    last_name: str
    new_last_name: str | None
    first_name: str
    middle_name: str | None
    birth_date: date
    sex: str | None
    recipient_code: int | None


@dataclass(frozen=True)
class ImportInput:
    patient_dir: Path
    test_date: date
    class1_csv: Path | None
    class2_csv: Path | None
    overwrite_existing: bool
    conclusion_file_path: Path | None = None
    preserve_existing_patient_in_db: bool = False


@dataclass(frozen=True)
class ImportResult:
    test_dir: Path


# --- Преобразование значений CSV и подготовка строк для PostgreSQL ---
def _to_int(value) -> int | None:
    text_value = str(value).strip()

    if not text_value:
        return None

    numeric_value = Decimal(text_value)
    rounded_value = numeric_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(rounded_value)


def _upsert_patient(conn, patient: PatientData, inp: ImportInput, organ_id: int) -> int:
    if inp.preserve_existing_patient_in_db:
        patient_id = get_patient_id_by_code(conn, patient.patient_code)
        if patient_id is not None:
            update_patient_recipient_code_by_code(
                conn,
                patient_code=patient.patient_code,
                recipient_code=patient.recipient_code,
            )
            return patient_id

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


def load_existing_patient_recipient_code(
    patient_code: str,
) -> tuple[bool, int | None]:
    with get_engine().begin() as conn:
        return get_patient_recipient_code_by_code(conn, patient_code)


def load_patient_code_by_recipient_code(recipient_code: int, organ: str) -> str | None:
    with get_engine().begin() as conn:
        return get_patient_code_by_recipient_code(conn, recipient_code, organ)


def load_patient_codes_by_recipient_code(recipient_code: int) -> list[str]:
    with get_engine().begin() as conn:
        return get_patient_codes_by_recipient_code(conn, recipient_code)


def _build_antibody_rows(antibodies: list[dict]) -> list[dict]:
    rows: list[dict] = []

    for antibody in antibodies:
        specificity = (antibody.get("Specificity") or "").strip()
        gene = ""
        allele_group = ""
        allele = ""

        if "*" in specificity and ":" in specificity:
            gene, rest = specificity.split("*", 1)
            allele_group, allele = rest.split(":", 1)
        elif "*" in specificity:
            gene, allele_group = specificity.split("*", 1)
        else:
            gene = specificity

        rows.append(
            {
                "gene": gene,
                "allele_group": allele_group,
                "allele": allele,
                "pct_positive": _to_int(antibody.get("% Positive")),
                "raw_value": _to_int(antibody.get("Raw Value")),
                "mfi_lra": _to_int(antibody.get("MFI/LRA")),
            }
        )

    return rows


# --- Backup и восстановление файлов на время критичных операций ---
def _backup_existing_file(path: Path) -> Path | None:
    if not path.exists() or not path.is_file():
        return None

    backup_path = path.with_name(f".bak__{uuid4().hex}__{path.name}")
    replace_file(path, backup_path)
    return backup_path


def _restore_backup_file(path: Path, backup_path: Path | None) -> None:
    if backup_path is None or not backup_path.exists():
        return

    replace_file(backup_path, path)


def _drop_backup_file(backup_path: Path | None) -> None:
    if backup_path is None:
        return

    if backup_path.exists() and backup_path.is_file():
        delete_file(backup_path)


def _drop_all_backups(backups: list[tuple[Path, Path | None]]) -> None:
    for _path, backup_path in backups:
        try:
            _drop_backup_file(backup_path)
        except Exception:
            pass


def _restore_all_backups(backups: list[tuple[Path, Path | None]]) -> None:
    for path, backup_path in reversed(backups):
        try:
            _restore_backup_file(path, backup_path)
        except Exception:
            pass


# --- Синхронизация существующей записи пациента при переименовании ---
def rename_existing_patient_record(
    *,
    current_patient_code: str,
    new_patient_code: str,
    last_name: str,
    new_last_name: str | None,
    first_name: str,
    middle_name: str | None,
    birth_date: date,
    sex: str | None,
    recipient_code: int | None,
) -> int | None:
    """
    Обновляет patient_code и анкетные данные пациента в PostgreSQL.

    Если запись по старому коду не найдена, новая запись НЕ создаётся.
    Это допустимо для пациентов, у которых отсутствуют положительные результаты
    и, следовательно, нет строк в patients/tests/antibodies.

    Возвращает:
    - id обновлённой записи, если строка была найдена и обновлена;
    - None, если в БД не было записи по current_patient_code.
    """
    with get_engine().begin() as conn:
        return update_existing_patient_by_code(
            conn=conn,
            current_patient_code=current_patient_code,
            new_patient_code=new_patient_code,
            last_name=last_name,
            new_last_name=new_last_name,
            first_name=first_name,
            middle_name=middle_name,
            birth_date=birth_date,
            sex=sex,
            recipient_code=recipient_code,
        )


# --- Основной сценарий импорта исследования в FS и PostgreSQL ---
def do_import(
    *, root_dir: Path, patient: PatientData, inp: ImportInput
) -> ImportResult:
    selected_csvs = {
        hla_class: csv_path
        for hla_class, csv_path in ((1, inp.class1_csv), (2, inp.class2_csv))
        if csv_path is not None
    }

    parsed_selected: dict[int, tuple[str, list[dict]]] = {}

    for hla_class, csv_path in selected_csvs.items():
        parsed = parse_luminex_csv(csv_path)
        pack = (parsed.pra, [item.as_report_dict() for item in parsed.antibodies])
        parsed_selected[hla_class] = pack

    if not parsed_selected:
        return ImportResult(
            test_dir=inp.patient_dir / inp.test_date.strftime("%d.%m.%Y")
        )

    has_non_empty_tests = any(
        bool(antibodies) for _pra, antibodies in parsed_selected.values()
    )

    _, source_dir = ensure_base_tree(root_dir, patient.organ)
    test_dir = ensure_test_date_folder(inp.patient_dir, inp.test_date)

    managed_outputs: list[tuple[Path, Path | None]] = []

    def prepare_output_path(path: Path) -> None:
        backup_path = _backup_existing_file(path)
        managed_outputs.append((path, backup_path))

    try:
        for hla_class, csv_path in selected_csvs.items():
            target_excel = test_dir / f"{hla_class}_класс.xlsx"
            prepare_output_path(target_excel)
            create_sorted_excel_result(csv_path, target_excel)

            target_source_csv = build_source_csv_path(
                src_dir=source_dir,
                organ=patient.organ,
                patient_folder_name=inp.patient_dir.name,
                analysis_date=inp.test_date,
                hla_class=hla_class,
            )
            prepare_output_path(target_source_csv)

            save_source_csv(
                source_dir,
                patient.organ,
                inp.patient_dir.name,
                inp.test_date,
                hla_class,
                csv_path,
            )

        if inp.conclusion_file_path is not None:
            for file_name in CONCLUSION_FILE_NAMES:
                prepare_output_path(test_dir / file_name)

            delete_conclusion_files(
                patient_dir=inp.patient_dir,
                test_date=inp.test_date,
            )

            save_conclusion_file(
                patient_dir=inp.patient_dir,
                test_date=inp.test_date,
                source_path=inp.conclusion_file_path,
                negative_screening=False,
            )

        with get_engine().begin() as conn:
            organ_id: int | None = None
            patient_id: int | None = None

            if has_non_empty_tests:
                organ_id = insert_organ(conn, patient.organ)
                patient_id = _upsert_patient(conn, patient, inp, organ_id)

            for hla_class, pack in parsed_selected.items():
                pra, antibodies = pack

                if not antibodies:
                    delete_test_by_patient_code(
                        conn,
                        organ=patient.organ,
                        patient_code=patient.patient_code,
                        test_date=inp.test_date,
                        hla_class=hla_class,
                    )
                    continue

                assert organ_id is not None
                assert patient_id is not None

                test_id = insert_test(
                    conn=conn,
                    patient_id=patient_id,
                    test_date=inp.test_date,
                    hla_class=hla_class,
                    pra=_to_int(pra),
                )

                if inp.overwrite_existing:
                    conn.execute(
                        text("DELETE FROM antibodies WHERE test_id = :tid"),
                        {"tid": test_id},
                    )

                insert_antibodies(
                    conn,
                    test_id=test_id,
                    antibodies=_build_antibody_rows(antibodies),
                )

            delete_orphan_patient_by_code(conn, patient.patient_code)

        _drop_all_backups(managed_outputs)

        return ImportResult(test_dir=test_dir)

    except Exception:
        for path, _backup_path in reversed(managed_outputs):
            try:
                if path.exists() and path.is_file():
                    delete_file(path)
            except Exception:
                pass

        _restore_all_backups(managed_outputs)

        try:
            if test_dir.exists() and not any(test_dir.iterdir()):
                test_dir.rmdir()
        except Exception:
            pass

        raise


# --- Удаление существующих результатов исследования и полной папки даты ---
def delete_existing_results(
    *,
    root_dir: Path,
    organ: str,
    patient_code: str,
    patient_dir: Path,
    test_date: date,
    classes: list[int],
) -> None:
    classes = sorted(set(classes))
    _, source_dir = ensure_base_tree(root_dir, organ)

    with get_engine().begin() as conn:
        for hla_class in classes:
            delete_test_by_patient_code(
                conn,
                organ=organ,
                patient_code=patient_code,
                test_date=test_date,
                hla_class=hla_class,
            )

        delete_orphan_patient_by_code(conn, patient_code)

    for hla_class in classes:
        delete_source_class_files(
            src_dir=source_dir,
            organ=organ,
            patient_folder_name=patient_dir.name,
            analysis_date=test_date,
            hla_class=hla_class,
        )

    delete_patient_test_files(
        patient_dir=patient_dir,
        test_date=test_date,
        classes=classes,
    )


def delete_entire_study(
    *,
    root_dir: Path,
    organ: str,
    patient_code: str,
    patient_dir: Path,
    test_date: date,
) -> None:
    """
    Полностью удаляет исследование за дату.

    Удаляются:
        - записи tests / antibodies для Class I и II;
        - соответствующие файлы в source_files;
        - JPEG/PDF заключения;
        - вся папка исследования со всем содержимым.
    """
    delete_existing_results(
        root_dir=root_dir,
        organ=organ,
        patient_code=patient_code,
        patient_dir=patient_dir,
        test_date=test_date,
        classes=[1, 2],
    )

    delete_conclusion_files(
        patient_dir=patient_dir,
        test_date=test_date,
    )

    delete_patient_test_dir_tree(
        patient_dir=patient_dir,
        test_date=test_date,
    )
