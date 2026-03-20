from __future__ import annotations

CLASS_RESULT_FILE_NAMES: dict[int, str] = {
    1: "1_класс.xlsx",
    2: "2_класс.xlsx",
}

NEGATIVE_CONCLUSION_FILE_NAME = "скрининг.jpg"
POSITIVE_CONCLUSION_FILE_NAME = "идентификация.jpg"

CONCLUSION_FILE_NAMES: tuple[str, str] = (
    NEGATIVE_CONCLUSION_FILE_NAME,
    POSITIVE_CONCLUSION_FILE_NAME,
)

MANAGED_STUDY_FILE_NAMES: tuple[str, ...] = (
    CLASS_RESULT_FILE_NAMES[1],
    CLASS_RESULT_FILE_NAMES[2],
    NEGATIVE_CONCLUSION_FILE_NAME,
    POSITIVE_CONCLUSION_FILE_NAME,
)


def class_result_file_name(hla_class: int) -> str:
    try:
        return CLASS_RESULT_FILE_NAMES[int(hla_class)]
    except Exception as exc:
        raise ValueError(f"Неизвестный HLA class: {hla_class}") from exc


def conclusion_file_name(*, negative_screening: bool) -> str:
    return (
        NEGATIVE_CONCLUSION_FILE_NAME
        if negative_screening
        else POSITIVE_CONCLUSION_FILE_NAME
    )
