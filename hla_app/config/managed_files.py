"""Соглашения по именам управляемых файлов исследования.

Здесь собраны канонические имена Excel-результатов и хранимых файлов
заключения (`.jpg` / `.pdf`), а также функции
`class_result_file_name()` / `normalize_conclusion_storage_suffix()` /
`conclusion_file_name()`. Если ломается работа с именами файлов в папках
исследований или `source_files`, сначала проверять этот модуль.
"""

from __future__ import annotations

# --- Канонические имена файлов исследования ---

CLASS_RESULT_FILE_NAMES: dict[int, str] = {
    1: "1_класс.xlsx",
    2: "2_класс.xlsx",
}

NEGATIVE_CONCLUSION_JPG_FILE_NAME = "скрининг.jpg"
POSITIVE_CONCLUSION_JPG_FILE_NAME = "идентификация.jpg"

NEGATIVE_CONCLUSION_PDF_FILE_NAME = "скрининг.pdf"
POSITIVE_CONCLUSION_PDF_FILE_NAME = "идентификация.pdf"

CONCLUSION_FILE_NAMES: tuple[str, ...] = (
    NEGATIVE_CONCLUSION_JPG_FILE_NAME,
    POSITIVE_CONCLUSION_JPG_FILE_NAME,
    NEGATIVE_CONCLUSION_PDF_FILE_NAME,
    POSITIVE_CONCLUSION_PDF_FILE_NAME,
)

MANAGED_STUDY_FILE_NAMES: tuple[str, ...] = (
    CLASS_RESULT_FILE_NAMES[1],
    CLASS_RESULT_FILE_NAMES[2],
    *CONCLUSION_FILE_NAMES,
)


# --- Вспомогательные функции выбора имени файла по типу результата ---


def class_result_file_name(hla_class: int) -> str:
    try:
        return CLASS_RESULT_FILE_NAMES[int(hla_class)]
    except Exception as exc:
        raise ValueError(f"Неизвестный HLA class: {hla_class}") from exc


def normalize_conclusion_storage_suffix(suffix: str) -> str:
    suffix = suffix.lower()

    if suffix in {".jpg", ".jpeg"}:
        return ".jpg"

    if suffix == ".pdf":
        return ".pdf"

    raise ValueError(f"Неподдерживаемый формат заключения: {suffix}")


def conclusion_file_name(*, negative_screening: bool, suffix: str) -> str:
    normalized = normalize_conclusion_storage_suffix(suffix)
    stem = "скрининг" if negative_screening else "идентификация"
    return f"{stem}{normalized}"
