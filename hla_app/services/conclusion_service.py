from __future__ import annotations

import re
from pathlib import Path

from hla_app.data.luminex_parser import parse_fixed_luminex_csv
from hla_app.utils.validators import cap_hyphenated_lastname


def normalize_staff_name(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""

    s = re.sub(r"[^А-Яа-яЁё .-]+", "", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"\.{2,}", ".", s)

    parts = s.split()
    if not parts:
        return ""

    surname_raw = re.sub(r"[^А-Яа-яЁё-]+", "", parts[0]).strip("-")
    surname_raw = re.sub(r"-{2,}", "-", surname_raw)

    if len(re.sub(r"[^А-Яа-яЁё]+", "", surname_raw)) < 2:
        return ""

    surname = cap_hyphenated_lastname(surname_raw)

    rest = "".join(parts[1:])
    letters = re.findall(r"[А-Яа-яЁё]", rest)[:2]
    initials = "".join(f"{ch.upper()}." for ch in letters)

    return f"{surname} {initials}" if initials else surname


def build_conclusion_class_dict(csv_path: Path | None) -> dict | None:
    if not csv_path:
        return None

    hla_class, _batch_date, _patient, pra, antibodies = parse_fixed_luminex_csv(
        csv_path
    )
    return {
        "hla_class": hla_class,
        "pra": pra,
        "antibodies": antibodies,
    }


def suggest_conclusion_filename(
    num_register: int,
    last_name: str,
    first_name: str,
    middle_name: str,
) -> str:
    init = f"{first_name[0]}."
    if middle_name:
        init = f"{first_name[0]}.{middle_name[0]}."

    safe_last = re.sub(r'[\\/:*?"<>|]', "_", (last_name or "Пациент").strip())
    return f"{num_register}_Ab_{safe_last}_{init}.docx"
