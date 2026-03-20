from __future__ import annotations

import re
from datetime import date, datetime

# Имя/отчество: только русские буквы/дефис, минимум 2 символа
RU_NAME_RE = re.compile(r"^[А-Яа-яЁё-]{2,}$")

# Фамилия: первые 2 символа ОБЯЗАТЕЛЬНО буквы
RU_LASTNAME_RE = re.compile(r"^[А-Яа-яЁё]{2}[А-Яа-яЁё-]*$")

# --------- нормализация для "поиска совпадений" ---------

# многосимвольные правила
_EQUIV_MULTI = (("сч", "щ"),)

# группы фонетически похожих букв
_EQUIV_GROUPS = [
    {"а", "о"},
    {"б", "п"},
    {"в", "ф"},
    {"г", "к"},
    {"д", "т"},
    {"ж", "ш", "щ"},
    {"з", "с"},
    {"е", "ё", "и", "э", "й", "ы"},
    {"у", "ю"},
]

# построение канонической карты символов
_EQUIV_CHAR: dict[str, str] = {}

for group in _EQUIV_GROUPS:
    canon = sorted(group)[0]
    for ch in group:
        _EQUIV_CHAR[ch] = canon

# удаляемые символы
_EQUIV_CHAR["ъ"] = ""
_EQUIV_CHAR["ь"] = ""

# латиница → кириллица
_EQUIV_CHAR.update(
    {
        "a": "а",
        "e": "е",
        "o": "о",
        "c": "с",
        "p": "р",
        "x": "х",
        "y": "у",
    }
)

_DOUBLE_RE = re.compile(r"(\w)\1+", flags=re.UNICODE)


def normalize_for_compare(s: str) -> str:
    """
    Строгое сравнение (без фаззи)
    """
    return (s or "").replace("Ё", "Е").replace("ё", "е").lower()


def normalize_for_match(s: str, *, strict_first_char: bool = False) -> str:
    """
    Фаззи-нормализация для поиска совпадений:
    - нижний регистр
    - сч -> щ
    - замены по таблице
    - удаление пробелов и дефисов
    - схлопывание повторов

    Если strict_first_char=True:
    - первая буква НЕ проходит через фаззи-замены;
    - то есть первая введённая буква считается именно собой.
    """
    s = (s or "").strip().lower()

    if not s:
        return ""

    # убрать разделители заранее
    s = s.replace("-", "").replace(" ", "")

    if not s:
        return ""

    if strict_first_char:
        first = s[:1]
        rest = s[1:]

        # многосимвольные правила применяем только к хвосту,
        # чтобы не "размазывать" первую букву
        for a, b in _EQUIV_MULTI:
            rest = rest.replace(a, b)

        rest = "".join(_EQUIV_CHAR.get(ch, ch) for ch in rest)

        result = first + rest
        result = first + _DOUBLE_RE.sub(r"\1", result[1:])

        return result

    # старая логика целиком
    for a, b in _EQUIV_MULTI:
        s = s.replace(a, b)

    s = "".join(_EQUIV_CHAR.get(ch, ch) for ch in s)
    s = _DOUBLE_RE.sub(r"\1", s)

    return s


def is_valid_ru_name(s: str) -> bool:
    return bool(RU_NAME_RE.match(s or ""))


def is_valid_ru_lastname(s: str) -> bool:
    s = (s or "").strip()

    if not s:
        return False

    if not RU_LASTNAME_RE.match(s):
        return False

    if s.endswith("-"):
        return False

    return True


def parse_ddmmyyyy(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except Exception:
        return None


def format_ddmmyyyy(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def check_date_in_range(d: date, min_d: date, max_d: date) -> bool:
    return (d >= min_d) and (d <= max_d)


def initials(first_name: str, middle_name: str | None) -> str:
    i1 = (first_name[:1] or "").upper()
    i2 = ((middle_name or "")[:1] or "").upper()

    return f"{i1}.{i2}." if i2 else f"{i1}."


def patient_dir_name(
    last_name: str,
    new_last_name: str,
    first_name: str,
    middle_name: str,
) -> str:
    if new_last_name:
        ln = f"{last_name}({new_last_name})"
    else:
        ln = last_name

    if middle_name:
        return f"{ln}_{first_name}.{middle_name}"

    return f"{ln}_{first_name}"


def patient_dir_name_full(
    last_name: str,
    new_last_name: str,
    first_name: str,
    middle_name: str,
    birth_date: date,
    sex: str | None = None,
) -> str:

    base = patient_dir_name(last_name, new_last_name, first_name, middle_name)

    result = f"{base}_{format_ddmmyyyy(birth_date)}"

    if sex == "f":
        result += "_ж"
    elif sex == "m":
        result += "_м"

    return result


def patient_code(organ: str, patient_dir: str) -> str:
    return f"{organ}__{patient_dir}"


def cap_ru(s: str) -> str:
    s = (s or "").strip()

    if not s:
        return ""

    return s[:1].upper() + s[1:].lower()


def cap_hyphenated_lastname(s: str) -> str:
    s = (s or "").strip()

    if not s:
        return ""

    parts = [cap_ru(p) for p in s.split("-") if p]

    return "-".join(parts)
