from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hla_app.data.luminex_parser import parse_luminex_csv
from hla_app.reports.excel_ab_titer import (
    build_titer_excel_filename,
    create_titer_a_b_drb1_excel_from_parsed,
    has_titer_rows_from_parsed,
)
from hla_app.services.app_prefs import load_effective_path_preferences


@dataclass(frozen=True)
class SumSkippedFile:
    csv_path: Path
    hla_class: str
    reason: str


@dataclass(frozen=True)
class SumBuildResult:
    outputs: list[Path]
    skipped_empty: list[SumSkippedFile]


def build_avg_excels(csv_paths: list[Path], min_titer: int = 0) -> SumBuildResult:
    outputs: list[Path] = []
    skipped_empty: list[SumSkippedFile] = []
    filename_counts: dict[tuple[str, str], int] = {}

    min_titer = max(0, int(min_titer))
    configured_output_dir = load_effective_path_preferences().sum_save_dir

    if configured_output_dir is not None:
        configured_output_dir.mkdir(parents=True, exist_ok=True)

    for csv_path in csv_paths:
        if not csv_path.exists():
            continue

        parsed = parse_luminex_csv(csv_path)

        hla_class, has_rows = has_titer_rows_from_parsed(
            parsed,
            min_titer=min_titer,
        )
        if not has_rows:
            skipped_empty.append(
                SumSkippedFile(
                    csv_path=csv_path,
                    hla_class=hla_class,
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
            else csv_path.parent
        )

        base_name = build_titer_excel_filename(parsed.patient, parsed.hla_class)
        key = (str(output_dir), base_name)
        filename_counts[key] = filename_counts.get(key, 0) + 1
        file_index = filename_counts[key]

        if file_index == 1:
            target_output = output_dir / base_name
        else:
            base_output = output_dir / base_name
            target_output = (
                output_dir / f"{base_output.stem}__{file_index}{base_output.suffix}"
            )

        outputs.append(
            create_titer_a_b_drb1_excel_from_parsed(
                parsed,
                target_output,
                min_titer=min_titer,
            )
        )

    return SumBuildResult(
        outputs=outputs,
        skipped_empty=skipped_empty,
    )
