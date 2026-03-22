"""Базовые настройки приложения и общие ограничения по датам.

Файл хранит корневой путь файловой базы по умолчанию, параметры PostgreSQL,
а также функции расчета минимально допустимых дат для анализов и рождения.
Если нужно понять стартовую конфигурацию системы, начинать удобнее отсюда.
"""

from __future__ import annotations

import os
from calendar import monthrange
from datetime import date
from pathlib import Path

# --- Учреждение по умолчанию ---

FIRST_CLINIC = "ГУ «МНПЦ ХТиГ»"
SECOND_CLINIC = "ГУ «РНПЦ ТиМБ»"
# Допустимые значения:
#   - "f_clinic" — ГУ «МНПЦ ХТиГ»;
#   - "s_clinic" — ГУ «РНПЦ ТиМБ».
DEFAULT_CLINIC = "f_clinic"


# --- Пути и каталоги по умолчанию ---

# Корень файловой базы.
ROOT_DIR = Path(r"D:\HLA_Laboratory_System")

# Папка по умолчанию для открытия файловых диалогов.
# Если None, используется Рабочий стол.
DEFAULT_DIALOG_DIR: Path | None = None

# Папка по умолчанию для сохранения заключения.
# Если None, используется Рабочий стол.
DEFAULT_CONCLUSION_SAVE_DIR: Path | None = None

# Папка по умолчанию для сохранения результатов блока
# «Титр антител к A, B и DRB1».
# Если None, файлы сохраняются рядом с исходными CSV.
DEFAULT_SUM_SAVE_DIR: Path | None = None


# --- Параметры подключения и служебные секреты приложения ---

# Таймаут одной попытки проверки доступности корня файловой базы.
ROOT_DIR_PROBE_TIMEOUT_SECONDS = 7
# Общее число попыток проверки.
# 2 = первая попытка + один повтор.
ROOT_DIR_PROBE_MAX_ATTEMPTS = 2
# Небольшая пауза между попытками.
ROOT_DIR_PROBE_RETRY_DELAY_SECONDS = 1


# --- Параметры подключения к PostgreSQL ---

DB_USER = os.getenv("HLA_APP_DB_USER", "postgres")
DB_PASSWORD = os.getenv("HLA_APP_DB_PASSWORD", "0")
DB_HOST = os.getenv("HLA_APP_DB_HOST", "localhost")
DB_PORT = int(os.getenv("HLA_APP_DB_PORT", "5432"))

_FIRST_CLINIC_DB_NAME = "hla_db_after"
_SECOND_CLINIC_DB_NAME = "hla_db_before"


def get_default_db_name_for_clinic(clinic: str | None) -> str:
    env_db_name = (os.getenv("HLA_APP_DB_NAME") or "").strip()
    if env_db_name:
        return env_db_name

    clinic_code = clinic or DEFAULT_CLINIC

    if clinic_code == "f_clinic":
        return _FIRST_CLINIC_DB_NAME

    if clinic_code == "s_clinic":
        return _SECOND_CLINIC_DB_NAME

    raise ValueError(
        "Неизвестное учреждение для имени БД по умолчанию: "
        f"{clinic_code!r}. Ожидалось 'f_clinic' или 's_clinic'."
    )


DB_NAME = get_default_db_name_for_clinic(DEFAULT_CLINIC)


# Код подтверждения для защищённых действий в приложении.
# При необходимости может быть переопределён через переменную окружения.
APP_PASSWORD = os.getenv("HLA_APP_PASSWORD", "0")


# --- UI-константы и значения по умолчанию для формы ---

# Перечень органов для выпадающего списка.
ORGANS = ["ЛЁГКОЕ", "ПЕЧЕНЬ", "ПОЧКА", "СЕРДЦЕ"]

# Ограничение боковой панели проводника:
# True  -> в корне проводника показывать только папки из ORGANS
# False -> показывать всё содержимое ROOT_DIR
#
# Это значение используется как значение ПО УМОЛЧАНИЮ.
# Пользователь может быстро переопределить его в окне «Настройки».
LIMIT_ROOT_EXPLORER_TO_ORGANS = True

# Выполнять ли полную перестройку локального индекса боковой панели
# автоматически при старте приложения.
# Удобно менять перед сборкой, если стартовая индексация не нужна.
REBUILD_FILE_TREE_INDEX_ON_STARTUP = True


# --- Вспомогательные функции расчета граничных дат ---


def _shift_months(value: date, months: int) -> date:
    total_months = value.year * 12 + (value.month - 1) + months
    year = total_months // 12
    month = total_months % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _shift_years(value: date, years: int) -> date:
    year = value.year + years
    day = min(value.day, monthrange(year, value.month)[1])
    return date(year, value.month, day)


def get_date_min_test() -> date:
    """Возвращает минимальную дату исследования: три месяца назад."""
    return _shift_months(date.today(), -3)


def get_date_min_birth() -> date:
    """Возвращает минимальную дату рождения: сто лет назад."""
    return _shift_years(date.today(), -100)
