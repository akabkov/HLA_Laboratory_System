"""Сервис пакетного построения Excel по средним значениям антител.

Модуль принимает набор CSV, определяет их класс, фильтрует пустые результаты,
группирует пары Class I / Class II и решает, строить один общий Excel или
отдельные файлы. Если расчет среднего или логика skipped-файлов ведет себя
не так, как ожидается, нужный код находится здесь.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hla_app.data.luminex_parser import ParsedLuminexCsv, parse_luminex_csv
from hla_app.reports.excel_avg_titer import (
    build_titer_excel_filename,
    build_titer_group_key,
    create_avg_titer_excel_from_parsed,
    create_avg_titer_excel_from_parsed_group,
    has_titer_rows_from_parsed,
    has_titer_rows_from_parsed_group,
)
from hla_app.services.app_prefs import load_effective_path_preferences

# Значение по умолчанию для нижнего порога чувствительности
# в блоке «Титр антител к A, B и DRB1».
DEFAULT_AVG_MIN_TITER = 3000


# --- Результаты пакетной обработки CSV и служебные модели группировки ---
@dataclass(frozen=True)
class SumSkippedFile:
    csv_path: Path
    hla_class: str
    reason: str


@dataclass(frozen=True)
class SumBuildResult:
    outputs: list[Path]
    skipped_empty: list[SumSkippedFile]


@dataclass(frozen=True)
class PreparedAvgCsv:
    csv_path: Path
    parsed: ParsedLuminexCsv
    hla_class: str
    has_rows: bool


# --- Вспомогательные функции выбора имени и пути итогового Excel ---
def _build_target_output_path(
    *,
    output_dir: Path,
    base_name: str,
    filename_counts: dict[tuple[str, str], int],
) -> Path:
    key = (str(output_dir), base_name)
    filename_counts[key] = filename_counts.get(key, 0) + 1
    file_index = filename_counts[key]

    if file_index == 1:
        return output_dir / base_name

    base_output = output_dir / base_name
    return output_dir / f"{base_output.stem}__{file_index}{base_output.suffix}"


# --- Основной сценарий пакетного построения Excel по средним значениям ---
def build_avg_excels(
    csv_paths: list[Path],
    min_titer: int = DEFAULT_AVG_MIN_TITER,
    num_register_text: str | None = None,
    *,
    test_date: date | None = None,
    patient_dir_name_text: str | None = None,
    dry_run: bool = False,
) -> SumBuildResult:

    outputs: list[Path] = []
    skipped_empty: list[SumSkippedFile] = []
    filename_counts: dict[tuple[str, str], int] = {}
    prepared_items: list[PreparedAvgCsv] = []

    min_titer = max(0, int(min_titer))
    configured_output_dir = load_effective_path_preferences().sum_save_dir

    if configured_output_dir is not None and not dry_run:
        configured_output_dir.mkdir(parents=True, exist_ok=True)

    for csv_path in csv_paths:
        if not csv_path.exists():
            continue

        parsed_csv = parse_luminex_csv(csv_path)
        hla_class, has_rows = has_titer_rows_from_parsed(
            parsed_csv,
            min_titer=min_titer,
        )
        prepared_items.append(
            PreparedAvgCsv(
                csv_path=csv_path,
                parsed=parsed_csv,
                hla_class=hla_class,
                has_rows=has_rows,
            )
        )

    grouped: dict[tuple[str, str], list[PreparedAvgCsv]] = {}
    for item in prepared_items:
        grouped.setdefault(build_titer_group_key(item.parsed), []).append(item)

    consumed_ids: set[int] = set()

    for group_items in grouped.values():
        class_i_items = [item for item in group_items if item.hla_class == "I"]
        class_ii_items = [item for item in group_items if item.hla_class == "II"]

        # Объединяем только чистую пару: один Class I и один Class II
        if len(class_i_items) != 1 or len(class_ii_items) != 1:
            continue

        pair_items = [class_i_items[0], class_ii_items[0]]

        # Объединяем только если оба класса реально содержат данные.
        if not all(item.has_rows for item in pair_items):
            continue

        pair_parsed = [item.parsed for item in pair_items]

        if not has_titer_rows_from_parsed_group(pair_parsed, min_titer=min_titer):
            continue

        output_dir = (
            configured_output_dir
            if configured_output_dir is not None
            else pair_items[0].csv_path.parent
        )

        base_name = build_titer_excel_filename(
            pair_items[0].parsed.patient,
            "I_II",
            pair_items[0].parsed.batch_date,
            num_register_text,
            patient_dir_name_text=patient_dir_name_text,
        )
        target_output = _build_target_output_path(
            output_dir=output_dir,
            base_name=base_name,
            filename_counts=filename_counts,
        )

        if dry_run:
            outputs.append(target_output)
        else:
            outputs.append(
                create_avg_titer_excel_from_parsed_group(
                    pair_parsed,
                    target_output,
                    min_titer=min_titer,
                    test_date=test_date,
                    num_register_text=num_register_text,
                )
            )

        for item in pair_items:
            consumed_ids.add(id(item))

    for item in prepared_items:
        if id(item) in consumed_ids:
            continue

        if not item.has_rows:
            skipped_empty.append(
                SumSkippedFile(
                    csv_path=item.csv_path,
                    hla_class=item.hla_class,
                    reason=(
                        "после фильтрации по специфичности и нижнему порогу "
                        "не осталось данных для расчёта"
                    ),
                )
            )
            continue

        output_dir = (
            configured_output_dir
            if configured_output_dir is not None
            else item.csv_path.parent
        )

        base_name = build_titer_excel_filename(
            item.parsed.patient,
            item.hla_class,
            item.parsed.batch_date,
            num_register_text,
            patient_dir_name_text=patient_dir_name_text,
        )
        target_output = _build_target_output_path(
            output_dir=output_dir,
            base_name=base_name,
            filename_counts=filename_counts,
        )

        if dry_run:
            outputs.append(target_output)
        else:
            outputs.append(
                create_avg_titer_excel_from_parsed(
                    item.parsed,
                    target_output,
                    min_titer=min_titer,
                    test_date=test_date,
                    num_register_text=num_register_text,
                )
            )

    return SumBuildResult(
        outputs=outputs,
        skipped_empty=skipped_empty,
    )
