"""Разбор CSV-файлов анализатора Luminex.

В модуле описаны dataclass-модели `LuminexAntibodyRow` и `ParsedLuminexCsv`,
а также функции `parse_luminex_csv()` и `parse_fixed_luminex_csv()`. Здесь
сосредоточена логика чтения фиксированной структуры CSV и преобразования ее
в данные для импорта, Excel-отчетов и заключений.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from hla_app.services.app_prefs import load_effective_app_preferences

# --- Координаты фиксированной структуры CSV Luminex ---

_LAYOUT_V131 = {
    "class_row_index": 4,
    "batch_date_row_index": 5,
    "patient_row_index": 6,
    "patient_column_index": 9,
    "pra_row_index": 12,
    "pra_column_index": 44,
    "antibodies_start_row_index": 24,
    "none_column_index": 1,
    "percent_positive_column_index": 13,
    "raw_value_column_index": 21,
    "mfi_lra_column_index": 32,
    "stop_marker": "Reviewer Comment",
    "batch_date_format": "ymd",
}

_LAYOUT_V140 = {
    "class_row_index": 4,
    "batch_date_row_index": 5,
    "patient_row_index": 6,
    "patient_column_index": 10,
    "pra_row_index": 12,
    "pra_column_index": 45,
    "antibodies_start_row_index": 24,
    "none_column_index": 1,
    "percent_positive_column_index": 14,
    "raw_value_column_index": 22,
    "mfi_lra_column_index": 33,
    "stop_marker": "Reviewer Comment",
    "batch_date_format": "dmy",
}


# --- Структуры распарсенных данных CSV ---


@dataclass(frozen=True)
class LuminexAntibodyRow:
    specificity: str
    pct_positive: str
    raw_value: str
    mfi_lra: str

    def as_report_dict(self) -> dict[str, str]:
        return {
            "Specificity": self.specificity,
            "% Positive": self.pct_positive,
            "Raw Value": self.raw_value,
            "MFI/LRA": self.mfi_lra,
        }


@dataclass(frozen=True)
class ParsedLuminexCsv:
    hla_class: str
    batch_date: str
    patient: str
    pra: str
    antibodies: list[LuminexAntibodyRow]

    def as_legacy_tuple(self) -> tuple[str, str, str, str, list[dict[str, str]]]:
        return (
            self.hla_class,
            self.batch_date,
            self.patient,
            self.pra,
            [item.as_report_dict() for item in self.antibodies],
        )


# --- Низкоуровневые helpers чтения ячеек и строк ---


def _cell(rows: list[list[str]], row_index: int, column_index: int) -> str:
    if 0 <= row_index < len(rows):
        row = rows[row_index]
        if 0 <= column_index < len(row):
            return (row[column_index] or "").strip()
    return ""


def _joined_row(rows: list[list[str]], row_index: int) -> str:
    if 0 <= row_index < len(rows):
        return " ".join((cell or "").strip() for cell in rows[row_index])
    return ""


def _active_layout() -> dict[str, int | str]:
    clinic = load_effective_app_preferences().clinic

    if clinic == "f_clinic":
        return _LAYOUT_V131

    if clinic == "s_clinic":
        return _LAYOUT_V140

    raise ValueError(
        "Не удалось определить формат CSV: clinic должна быть 'f_clinic' или 's_clinic'."
    )


def _extract_batch_date(batch_date_row: str, *, batch_date_format: str) -> str:
    text = (batch_date_row or "").strip()

    if batch_date_format == "ymd":
        match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        if not match:
            return ""

        year, month, day = match.group(1).split("-")
        return f"{day}.{month}.{year}"

    if batch_date_format == "dmy":
        match = re.search(r"(\d{2}-\d{2}-\d{4})", text)
        if not match:
            return ""

        day, month, year = match.group(1).split("-")
        return f"{day}.{month}.{year}"

    return ""


# --- Публичный API разбора CSV для импорта и отчетов ---


def parse_luminex_csv(csv_path: Path) -> ParsedLuminexCsv:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))

    layout = _active_layout()

    class_row = _joined_row(rows, layout["class_row_index"])
    class_match = re.search(r"\b(II|I)\b", class_row)
    hla_class = class_match.group(1) if class_match else ""

    batch_date_row = _joined_row(rows, layout["batch_date_row_index"])
    batch_date = _extract_batch_date(
        batch_date_row,
        batch_date_format=str(layout["batch_date_format"]),
    )

    patient = _cell(
        rows,
        layout["patient_row_index"],
        layout["patient_column_index"],
    )
    pra = _cell(
        rows,
        layout["pra_row_index"],
        layout["pra_column_index"],
    )

    antibodies: list[LuminexAntibodyRow] = []
    row_index = layout["antibodies_start_row_index"]

    if _cell(rows, row_index, layout["none_column_index"]).lower() == "none":
        return ParsedLuminexCsv(
            hla_class=hla_class,
            batch_date=batch_date,
            patient=patient,
            pra=pra,
            antibodies=[],
        )

    while row_index < len(rows):
        if str(layout["stop_marker"]) in _joined_row(rows, row_index):
            break

        specificity = _cell(rows, row_index, layout["none_column_index"])
        pct_positive = _cell(
            rows,
            row_index,
            layout["percent_positive_column_index"],
        )
        raw_value = _cell(
            rows,
            row_index,
            layout["raw_value_column_index"],
        )
        mfi_lra = _cell(
            rows,
            row_index,
            layout["mfi_lra_column_index"],
        )

        if any((specificity, pct_positive, raw_value, mfi_lra)):
            antibodies.append(
                LuminexAntibodyRow(
                    specificity=specificity,
                    pct_positive=pct_positive,
                    raw_value=raw_value,
                    mfi_lra=mfi_lra,
                )
            )

        row_index += 1

    return ParsedLuminexCsv(
        hla_class=hla_class,
        batch_date=batch_date,
        patient=patient,
        pra=pra,
        antibodies=antibodies,
    )


def parse_fixed_luminex_csv(
    csv_path: Path,
) -> tuple[str, str, str, str, list[dict[str, str]]]:
    """
    Совместимый интерфейс для существующего кода проекта.

    Возвращает кортеж:
        (hla_class, batch_date, patient, pra, antibodies_as_dicts)
    """
    return parse_luminex_csv(csv_path).as_legacy_tuple()
