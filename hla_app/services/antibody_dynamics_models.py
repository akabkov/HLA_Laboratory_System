"""Dataclass-модели для окна динамики антител.

Модуль описывает три слоя данных:
- входные данные поиска пациента;
- сырые read-only данные, загруженные из PostgreSQL;
- уже подготовленные payload’ы для отрисовки графиков.
Это позволяет держать SQL, бизнес-логику агрегации и UI-рендеринг раздельно.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


# --- Входные данные поиска пациента и базовая идентификация ---
@dataclass(frozen=True)
class PatientLookupInput:
    patient_code: str | None
    organ_title: str | None
    recipient_code: int | None
    last_name: str | None
    new_last_name: str | None
    first_name: str | None
    middle_name: str | None
    birth_date: date | None
    sex: str | None


@dataclass(frozen=True)
class PatientCandidate:
    patient_code: str
    full_name: str
    birth_date: date | None
    recipient_code: int | None
    organ_title: str | None


@dataclass(frozen=True)
class DynamicsPatientHeader:
    patient_code: str
    full_name: str
    birth_date: date | None
    sex: str | None
    recipient_code: int | None
    organ_title: str | None


# --- Сырые read-only данные из PostgreSQL до агрегации под графики ---
@dataclass(frozen=True)
class DynamicsRawRow:
    source: str
    test_id: int
    test_date: date
    hla_class: int
    pra: int | None
    gene: str | None
    allele_group: str | None
    allele: str | None
    raw_value: int | None


@dataclass(frozen=True)
class DynamicsRawPayload:
    header: DynamicsPatientHeader
    rows: list[DynamicsRawRow]
    primary_test_dates: list[date]
    secondary_test_dates: list[date]


# --- Пользовательские настройки отображения и payload для графиков ---
@dataclass(frozen=True)
class DynamicsViewOptions:
    resolution: str
    only_ab_drb1: bool
    min_titer: int
    selected_antibodies: tuple[str, ...] = ()
    selected_from_date: date | None = None
    selected_to_date: date | None = None


@dataclass(frozen=True)
class SeriesPoint:
    x_index: int
    y_value: float
    test_date: date
    source: str
    pra: int | None


@dataclass(frozen=True)
class PlotSeries:
    label: str
    color_group: str
    color_key: int
    y_values: list[float]
    points: list[SeriesPoint]


@dataclass(frozen=True)
class PraBarPoint:
    x_index: int
    value: int
    test_date: date
    source: str


@dataclass(frozen=True)
class ClassPlotPayload:
    title: str
    series: list[PlotSeries]
    pra_points: list[PraBarPoint]
    tooltip_rows_by_index: dict[int, list[tuple[str, float]]]
    test_x_indices: list[int]
    has_tests: bool
    empty_message: str


@dataclass(frozen=True)
class PatientDynamicsView:
    header: DynamicsPatientHeader
    available_dates: list[date]
    x_labels: list[str]
    threshold_value: int
    class1: ClassPlotPayload
    class2: ClassPlotPayload
    boundary_x: float | None
