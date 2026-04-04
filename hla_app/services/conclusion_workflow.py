"""Высокоуровневый сценарий формирования заключения.

Файл связывает UI и генератор DOCX: собирает `ConclusionPayload`, определяет
название клиники, выбирает путь сохранения и вызывает итоговую запись файла.
Если нужен единый поток создания заключения "от формы до документа", он
собран именно здесь.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hla_app.config.settings import FIRST_CLINIC, SECOND_CLINIC
from hla_app.reports.docx_conclusion import create_hla_conclusion_docx
from hla_app.services.conclusion_service import (
    build_conclusion_class_dict,
    normalize_staff_name,
    suggest_conclusion_filename,
)
from hla_app.utils.validators import is_positive_int_text_without_leading_zero

# --- Названия учреждений для шапки заключения ---

_FIRST_CLINIC_FULL_NAME = "ГУ «Минский НПЦ хирургии, трансплантологии и гематологии»"
_SECOND_CLINIC_FULL_NAME = "ГУ «РНПЦ трансфузиологии и медицинских биотехнологий»"
_FIRST_CLINIC_LABORATORY_NAME = "Лаборатория HLA-типирования"
_SECOND_CLINIC_LABORATORY_NAME = "Лаборатория HLA-типирования органов и тканей"
_FIRST_CLINIC_RESULTS_TITLE = "Результаты определения анти-HLA-антител"
_SECOND_CLINIC_RESULTS_TITLE = "Результаты определения HLA-фенотипа и анти-HLA-антител"
_SECOND_CLINIC_PHENOTYPE_LINE = "HLA-фенотип:"
_SECOND_CLINIC_DESTINATION_INSTITUTION_LABEL = "Учреждение:"


# --- Полезная нагрузка сценария формирования заключения ---


@dataclass(frozen=True)
class ConclusionPayload:
    class1: dict | None
    class2: dict | None
    num_register: int
    head_of: str
    acting: bool
    biologist1: str
    doctor: bool
    biologist2: str
    last_name: str
    first_name: str
    middle_name: str
    screening: bool
    clinic_name: str
    laboratory_name: str
    results_title: str
    destination_institution_label: str | None
    phenotype_line_text: str | None

    @property
    def suggested_filename(self) -> str:
        return suggest_conclusion_filename(
            self.num_register,
            self.last_name,
            self.first_name,
            self.middle_name,
        )


# --- Подготовка реквизитов учреждения и данных для документа ---


@dataclass(frozen=True)
class ConclusionClinicPresentation:
    clinic_name: str
    laboratory_name: str
    results_title: str
    destination_institution_label: str | None = None
    phenotype_line_text: str | None = None


_CLINIC_PRESENTATIONS = {
    "f_clinic": ConclusionClinicPresentation(
        clinic_name=_FIRST_CLINIC_FULL_NAME,
        laboratory_name=_FIRST_CLINIC_LABORATORY_NAME,
        results_title=_FIRST_CLINIC_RESULTS_TITLE,
    ),
    "s_clinic": ConclusionClinicPresentation(
        clinic_name=_SECOND_CLINIC_FULL_NAME,
        laboratory_name=_SECOND_CLINIC_LABORATORY_NAME,
        results_title=_SECOND_CLINIC_RESULTS_TITLE,
        destination_institution_label=_SECOND_CLINIC_DESTINATION_INSTITUTION_LABEL,
        phenotype_line_text=_SECOND_CLINIC_PHENOTYPE_LINE,
    ),
}


def resolve_clinic_presentation(clinic: str) -> ConclusionClinicPresentation:
    presentation = _CLINIC_PRESENTATIONS.get(clinic)
    if presentation is not None:
        return presentation

    raise ValueError(
        f"Для заключения выберите учреждение: {FIRST_CLINIC} или {SECOND_CLINIC}."
    )


def build_conclusion_payload(
    *,
    class1_csv: Path | None,
    class2_csv: Path | None,
    ask_negative_without_csv,
    num_register_text: str,
    clinic: str | None,
    head_of_text: str,
    biologist1_text: str,
    biologist2_text: str,
    acting: bool,
    doctor: bool,
    screening: bool,
    last_name: str,
    new_last_name: str,
    first_name: str,
    middle_name: str,
) -> ConclusionPayload | None:
    base_last = last_name or new_last_name
    normalized_middle_name = middle_name or ""

    if not base_last:
        raise ValueError(
            "Для заключения заполните Фамилию или включите и заполните Новую фамилию."
        )
    if not first_name:
        raise ValueError("Для заключения заполните Имя.")

    num_register_text = (num_register_text or "").strip()
    if not is_positive_int_text_without_leading_zero(num_register_text):
        raise ValueError(
            "Поле «№ по журналу» должно быть заполнено "
            "(только цифры, без начального 0)."
        )
    num_register = int(num_register_text)

    if clinic is None:
        raise ValueError(
            f"Для заключения выберите учреждение: {FIRST_CLINIC} или {SECOND_CLINIC}."
        )

    clinic_code = clinic
    presentation = resolve_clinic_presentation(clinic_code)

    head_of = normalize_staff_name(head_of_text)
    biologist1 = normalize_staff_name(biologist1_text)
    biologist2 = normalize_staff_name(biologist2_text)

    filled_staff_count = sum(1 for value in (head_of, biologist1, biologist2) if value)
    required_staff_count = 1
    if filled_staff_count < required_staff_count:
        raise ValueError(
            "Заполните хотя бы одно из трёх полей: "
            "«Заведующий», первое поле «Биолог/Врач», второе поле «Биолог»."
        )

    if class1_csv is None and class2_csv is None:
        if not ask_negative_without_csv():
            return None

        class1 = None
        class2 = None
    else:
        class1 = build_conclusion_class_dict(class1_csv)
        class2 = build_conclusion_class_dict(class2_csv)

    return ConclusionPayload(
        class1=class1,
        class2=class2,
        num_register=num_register,
        head_of=head_of,
        acting=acting,
        biologist1=biologist1,
        doctor=doctor,
        biologist2=biologist2,
        last_name=base_last,
        first_name=first_name,
        middle_name=normalized_middle_name,
        screening=screening,
        clinic_name=presentation.clinic_name,
        laboratory_name=presentation.laboratory_name,
        results_title=presentation.results_title,
        destination_institution_label=presentation.destination_institution_label,
        phenotype_line_text=presentation.phenotype_line_text,
    )


# --- Публичное сохранение DOCX-заключения на диск ---


def save_conclusion_docx(
    *,
    payload: ConclusionPayload,
    output_path: Path,
    overwrite: bool = False,
) -> Path:
    result = create_hla_conclusion_docx(
        class1=payload.class1,
        class2=payload.class2,
        num_register=payload.num_register,
        head_of=payload.head_of,
        acting=payload.acting,
        biologist1=payload.biologist1,
        doctor=payload.doctor,
        biologist2=payload.biologist2,
        last_name=payload.last_name,
        first_name=payload.first_name,
        middle_name=payload.middle_name,
        screening=payload.screening,
        clinic_name=payload.clinic_name,
        laboratory_name=payload.laboratory_name,
        results_title=payload.results_title,
        destination_institution_label=payload.destination_institution_label,
        phenotype_line_text=payload.phenotype_line_text,
        output_path=output_path,
        overwrite=overwrite,
    )
    return Path(result)
