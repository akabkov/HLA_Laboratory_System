from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

_CLASS_ROW_INDEX = 4
_BATCH_DATE_ROW_INDEX = 5
_PATIENT_ROW_INDEX = 6
_PATIENT_COLUMN_INDEX = 9
_PRA_ROW_INDEX = 12
_PRA_COLUMN_INDEX = 44
_ANTIBODIES_START_ROW_INDEX = 24
_NONE_COLUMN_INDEX = 1
_PERCENT_POSITIVE_COLUMN_INDEX = 13
_RAW_VALUE_COLUMN_INDEX = 21
_MFI_LRA_COLUMN_INDEX = 32
_STOP_MARKER = "Reviewer Comment"


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


def parse_luminex_csv(csv_path: Path) -> ParsedLuminexCsv:
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))

    class_row = _joined_row(rows, _CLASS_ROW_INDEX)
    class_match = re.search(r"\b(II|I)\b", class_row)
    hla_class = class_match.group(1) if class_match else ""

    batch_date_row = _joined_row(rows, _BATCH_DATE_ROW_INDEX)
    batch_date_match = re.search(r"(\d{4}-\d{2}-\d{2})", batch_date_row)
    batch_date = batch_date_match.group(1) if batch_date_match else ""

    patient = _cell(rows, _PATIENT_ROW_INDEX, _PATIENT_COLUMN_INDEX)
    pra = _cell(rows, _PRA_ROW_INDEX, _PRA_COLUMN_INDEX)

    antibodies: list[LuminexAntibodyRow] = []
    row_index = _ANTIBODIES_START_ROW_INDEX

    if _cell(rows, row_index, _NONE_COLUMN_INDEX).lower() == "none":
        return ParsedLuminexCsv(
            hla_class=hla_class,
            batch_date=batch_date,
            patient=patient,
            pra=pra,
            antibodies=[],
        )

    while row_index < len(rows):
        if _STOP_MARKER in _joined_row(rows, row_index):
            break

        specificity = _cell(rows, row_index, _NONE_COLUMN_INDEX)
        pct_positive = _cell(rows, row_index, _PERCENT_POSITIVE_COLUMN_INDEX)
        raw_value = _cell(rows, row_index, _RAW_VALUE_COLUMN_INDEX)
        mfi_lra = _cell(rows, row_index, _MFI_LRA_COLUMN_INDEX)

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
