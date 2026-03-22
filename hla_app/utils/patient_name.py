"""Форматирование краткого имени пациента.

В модуле находится логика преобразования полного имени в короткий формат для
Excel, заключений и других отображаемых мест. Если итоговое представление ФИО
в отчетах выглядит не так, как ожидается, проверять нужно этот небольшой файл.
"""

from __future__ import annotations

import re

from hla_app.utils.validators import cap_hyphenated_fio_part


# --- Публичное форматирование краткого имени пациента для вывода ---
def normalize_patient_name(patient: str) -> str:
    patient = (patient or "").strip()
    return re.sub(r"^[^A-Za-zА-Яа-яЁё]+", "", patient)


def format_patient_short_name(patient: str) -> str:
    patient = normalize_patient_name(patient)

    if not patient:
        return ""

    parts = [p for p in patient.split() if p]
    if not parts:
        return ""

    # Фамилия
    surname_raw = re.sub(r"[^A-Za-zА-Яа-яЁё-]+", "", parts[0]).strip("-")

    if not surname_raw:
        surname = "Пациент"
    else:
        surname = cap_hyphenated_fio_part(surname_raw)

    if len(parts) == 1:
        return surname

    initials: list[str] = []

    # Проверяем полный формат
    for part in parts[1:]:
        letters = re.findall(r"[A-Za-zА-Яа-яЁё]", part)
        if letters:
            initials.append(letters[0].upper())
        if len(initials) >= 2:
            break

    # Потом формат с инициалами
    if len(initials) < 2:
        rest = "".join(parts[1:])
        letters = re.findall(r"[A-Za-zА-Яа-яЁё]", rest)

        if letters:
            initials = [letters[0].upper()]
            if len(letters) > 1:
                initials.append(letters[1].upper())

    if not initials:
        return surname

    if len(initials) == 1:
        return f"{surname} {initials[0]}."
    else:
        return f"{surname} {initials[0]}.{initials[1]}."
