"""Бизнес-логика динамики антител поверх read-only данных PostgreSQL.

Сервис решает три задачи:
- разрешение пациента для окна динамики;
- объединение raw-данных из основной и дополнительной БД;
- подготовка payload’а для графиков с учётом resolution, порога и фильтров.
Если нужно понять, почему в окне отображается именно такой набор серий,
начинать стоит с этого модуля.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean

from hla_app.db.antibody_dynamics_repo import (
    find_primary_patient_candidates,
    load_primary_dynamics_rows,
    load_primary_patient_header,
    load_secondary_dynamics_rows,
)
from hla_app.services.antibody_dynamics_models import (
    ClassPlotPayload,
    DynamicsRawPayload,
    DynamicsViewOptions,
    PatientCandidate,
    PatientDynamicsView,
    PatientLookupInput,
    PlotSeries,
    PraBarPoint,
    SeriesPoint,
)
from hla_app.services.app_prefs import load_effective_app_preferences
from hla_app.services.import_service import (
    load_patient_code_by_recipient_code,
    load_patient_codes_by_recipient_code,
)
from hla_app.utils.antibody_specificity import (
    build_low_resolution_label,
    build_series_label,
    passes_ab_drb1_filter,
    stable_color_key,
)
from hla_app.utils.validators import (
    format_ddmmyyyy,
    normalize_for_compare,
    normalize_for_match,
)


# --- Разрешение пациента и объединение raw-данных ---
def _db_signature(
    *,
    db_user,
    db_password,
    db_host,
    db_port,
    db_name,
) -> tuple[str, str, str, int, str]:
    return (
        str(db_user or "").strip(),
        str(db_password or "").strip(),
        str(db_host or "").strip().lower(),
        int(db_port),
        str(db_name or "").strip(),
    )


def _secondary_points_to_primary_db(secondary_config) -> bool:
    primary = load_effective_app_preferences()

    try:
        primary_signature = _db_signature(
            db_user=primary.db_user,
            db_password=primary.db_password,
            db_host=primary.db_host,
            db_port=primary.db_port,
            db_name=primary.db_name,
        )
        secondary_signature = _db_signature(
            db_user=secondary_config.db_user,
            db_password=secondary_config.db_password,
            db_host=secondary_config.db_host,
            db_port=secondary_config.db_port,
            db_name=secondary_config.db_name,
        )
    except Exception:
        return False

    return primary_signature == secondary_signature


def _pick_last_names(lookup_input: PatientLookupInput) -> set[str]:
    values = set()
    if lookup_input.last_name:
        values.add(normalize_for_match(lookup_input.last_name, strict_first_char=True))
    if lookup_input.new_last_name:
        values.add(
            normalize_for_match(lookup_input.new_last_name, strict_first_char=True)
        )
    return values


def _build_candidate_last_names(row: dict) -> set[str]:
    values = {
        normalize_for_match(row["last_name"], strict_first_char=True),
    }
    if row["new_last_name"]:
        values.add(normalize_for_match(row["new_last_name"], strict_first_char=True))
    return values


def _matches_optional_name(wanted: str | None, candidate: str | None) -> bool:
    if not wanted:
        return True

    return normalize_for_compare(candidate or "") == normalize_for_compare(wanted)


def _build_patient_candidate_from_row(row: dict) -> PatientCandidate:
    return PatientCandidate(
        patient_code=row["patient_code"],
        full_name=" ".join(
            part
            for part in (
                row["last_name"],
                row["first_name"],
                row["middle_name"],
            )
            if part
        ),
        birth_date=row["birth_date"],
        recipient_code=row["recipient_code"],
        organ_title=row["organ_title"],
    )


def _filter_demographic_candidates(
    rows: list[dict],
    *,
    lookup_input: PatientLookupInput,
    wanted_last_names: set[str],
) -> list[PatientCandidate]:
    filtered = []
    for row in rows:
        candidate_last_names = _build_candidate_last_names(row)
        if wanted_last_names.isdisjoint(candidate_last_names):
            continue

        if not _matches_optional_name(lookup_input.first_name, row["first_name"]):
            continue

        if not _matches_optional_name(lookup_input.middle_name, row["middle_name"]):
            continue

        filtered.append(_build_patient_candidate_from_row(row))

    return filtered


def resolve_primary_patient_for_dynamics(
    lookup_input: PatientLookupInput,
) -> list[PatientCandidate]:
    patient_code = (lookup_input.patient_code or "").strip()
    recipient_code = lookup_input.recipient_code
    organ_title = (lookup_input.organ_title or "").strip()

    # Если форма уже привязана к конкретной папке пациента, то patient_code
    # однозначно известен и должен иметь наивысший приоритет. Это особенно
    # важно для старых папок, где в названии может не быть полной демографии.
    if patient_code:
        header = load_primary_patient_header(patient_code)
        if header is None:
            return []

        return [
            PatientCandidate(
                patient_code=header.patient_code,
                full_name=header.full_name,
                birth_date=header.birth_date,
                recipient_code=header.recipient_code,
                organ_title=header.organ_title,
            )
        ]

    # 1. Если введён recipient_code, работаем только через него.
    if recipient_code is not None:
        if organ_title:
            patient_code = load_patient_code_by_recipient_code(
                recipient_code,
                organ_title,
            )
            if not patient_code:
                return []

            header = load_primary_patient_header(patient_code)
            if header is None:
                return []

            return [
                PatientCandidate(
                    patient_code=header.patient_code,
                    full_name=header.full_name,
                    birth_date=header.birth_date,
                    recipient_code=header.recipient_code,
                    organ_title=header.organ_title,
                )
            ]

        patient_codes = load_patient_codes_by_recipient_code(recipient_code)

        if len(patient_codes) == 1:
            header = load_primary_patient_header(patient_codes[0])
            if header is None:
                return []

            return [
                PatientCandidate(
                    patient_code=header.patient_code,
                    full_name=header.full_name,
                    birth_date=header.birth_date,
                    recipient_code=header.recipient_code,
                    organ_title=header.organ_title,
                )
            ]

        if len(patient_codes) > 1:
            result = []
            for patient_code in patient_codes:
                header = load_primary_patient_header(patient_code)
                if header is None:
                    continue

                result.append(
                    PatientCandidate(
                        patient_code=header.patient_code,
                        full_name=header.full_name,
                        birth_date=header.birth_date,
                        recipient_code=header.recipient_code,
                        organ_title=header.organ_title,
                    )
                )
            return result

        return []

    # 2. Demographic search разрешён только если recipient_code не введён.
    if not organ_title:
        raise ValueError(
            "Выберите орган и выполните поиск по фамилии или коду реципиента."
        )

    wanted_last_names = _pick_last_names(lookup_input)
    if not wanted_last_names:
        raise ValueError("Для поиска пациента укажите фамилию.")

    if lookup_input.birth_date is None:
        raise ValueError("Для поиска пациента укажите дату рождения.")

    rows = find_primary_patient_candidates(
        organ_title=organ_title,
        first_name=lookup_input.first_name,
        birth_date=lookup_input.birth_date,
        middle_name=lookup_input.middle_name,
        sex=lookup_input.sex,
    )
    filtered = _filter_demographic_candidates(
        rows,
        lookup_input=lookup_input,
        wanted_last_names=wanted_last_names,
    )

    if filtered:
        return filtered

    if lookup_input.first_name or lookup_input.middle_name:
        # Повторяем read-only поиск без SQL-фильтра по имени/отчеству, чтобы
        # не потерять кандидата только из-за различий вроде Е/Ё. Точное
        # сравнение имени и отчества всё равно делаем ниже на сервисном слое.
        relaxed_rows = find_primary_patient_candidates(
            organ_title=organ_title,
            first_name=None,
            birth_date=lookup_input.birth_date,
            middle_name=None,
            sex=lookup_input.sex,
        )
        return _filter_demographic_candidates(
            relaxed_rows,
            lookup_input=lookup_input,
            wanted_last_names=wanted_last_names,
        )

    return []


def load_patient_dynamics_raw(
    patient_code: str,
    secondary_config,
) -> DynamicsRawPayload:
    header = load_primary_patient_header(patient_code)
    if header is None:
        raise RuntimeError(
            "Для пациента нет данных в основной базе PostgreSQL "
            "для построения динамики антител."
        )

    primary_rows = load_primary_dynamics_rows(patient_code)
    rows = list(primary_rows)
    primary_dates = sorted({row.test_date for row in primary_rows})
    secondary_dates = []

    if header.recipient_code is not None and not _secondary_points_to_primary_db(
        secondary_config
    ):
        # Защита от ошибочной конфигурации, когда secondary указывает на ту же БД,
        # что и primary: иначе одна и та же история загрузится дважды.
        # Ошибка дополнительной БД не должна ломать окно целиком:
        # primary source остаётся основным и обязательным.
        try:
            secondary_rows = load_secondary_dynamics_rows(
                db_user=secondary_config.db_user,
                db_password=secondary_config.db_password,
                db_host=secondary_config.db_host,
                db_port=secondary_config.db_port,
                db_name=secondary_config.db_name,
                recipient_code=header.recipient_code,
                organ_title=header.organ_title or "",
            )
            rows.extend(secondary_rows)
            secondary_dates = sorted({row.test_date for row in secondary_rows})
        except Exception:
            pass

    return DynamicsRawPayload(
        header=header,
        rows=rows,
        primary_test_dates=primary_dates,
        secondary_test_dates=secondary_dates,
    )


def list_available_antibody_labels(
    raw_payload: DynamicsRawPayload,
    *,
    resolution: str,
    selected_from_date=None,
    selected_to_date=None,
) -> list[str]:
    labels = set()

    for row in raw_payload.rows:
        if selected_from_date is not None and row.test_date < selected_from_date:
            continue

        if selected_to_date is not None and row.test_date > selected_to_date:
            continue

        if row.raw_value is None:
            continue

        label = build_series_label(
            gene=row.gene,
            allele_group=row.allele_group,
            allele=row.allele,
            resolution=resolution,
        )
        if label:
            labels.add(label)

    return sorted(labels)


# --- Подготовка payload’а для одного HLA-класса ---
def _build_class_payload(
    *,
    rows,
    hla_class: int,
    x_index_by_date: dict,
    all_dates: list,
    options: DynamicsViewOptions,
) -> ClassPlotPayload:
    class_rows = [row for row in rows if row.hla_class == hla_class]
    has_tests = any(row.hla_class == hla_class for row in rows)
    class_test_x_indices = sorted(
        {x_index_by_date[row.test_date] for row in class_rows}
    )

    pra_map = {}
    for row in class_rows:
        test_key = (row.source, row.test_id)
        pra_map.setdefault(test_key, (row.test_date, row.pra, row.source))

    pra_points = [
        PraBarPoint(
            x_index=x_index_by_date[test_date],
            value=int(pra_value or 0),
            test_date=test_date,
            source=source,
        )
        for test_date, pra_value, source in pra_map.values()
    ]

    grouped_values = defaultdict(list)
    selected_antibodies = set(options.selected_antibodies)

    for row in class_rows:
        if not passes_ab_drb1_filter(
            hla_class=hla_class,
            gene=row.gene,
            enabled=options.only_ab_drb1,
        ):
            continue

        raw_value = row.raw_value
        if raw_value is None or raw_value < options.min_titer:
            continue

        label = build_series_label(
            gene=row.gene,
            allele_group=row.allele_group,
            allele=row.allele,
            resolution=options.resolution,
        )

        if not label:
            continue

        if selected_antibodies and label not in selected_antibodies:
            continue

        test_key = (row.source, row.test_id)
        grouped_values[(test_key, label)].append(row)

    # Все серии живут на общей временной оси. Пропуски храним как NaN, чтобы
    # сохранить соответствие индексам всех дат; сам график строится только по
    # фактическим точкам серии и не проваливается к нулю на пропусках.
    series_map = defaultdict(lambda: [math.nan] * len(all_dates))
    point_map = defaultdict(list)
    color_group_by_label: dict[str, str] = {}

    for (_test_key, label), items in grouped_values.items():
        sample = items[0]
        x_index = x_index_by_date[sample.test_date]
        low_resolution_group = build_low_resolution_label(
            sample.gene,
            sample.allele_group,
        )
        color_group = low_resolution_group or label
        color_group_by_label.setdefault(label, color_group)

        if options.resolution == "high":
            value = max(int(item.raw_value or 0) for item in items)
        else:
            value = round(mean(int(item.raw_value or 0) for item in items))

        series_map[label][x_index] = float(value)
        point_map[label].append(
            SeriesPoint(
                x_index=x_index,
                y_value=float(value),
                test_date=sample.test_date,
                source=sample.source,
                pra=sample.pra,
            )
        )

    series = [
        PlotSeries(
            label=label,
            color_group=color_group_by_label.get(label, label),
            color_key=stable_color_key(color_group_by_label.get(label, label)),
            y_values=y_values,
            points=sorted(point_map[label], key=lambda item: item.x_index),
        )
        for label, y_values in sorted(series_map.items(), key=lambda item: item[0])
    ]

    tooltip_rows_by_index: dict[int, list[tuple[str, float]]] = defaultdict(list)
    for series_item in series:
        for point in series_item.points:
            tooltip_rows_by_index[point.x_index].append(
                (series_item.label, point.y_value)
            )

    # В tooltip порядок должен совпадать с визуальным восприятием графика:
    # сначала самые высокие значения на текущую дату, а не сортировка по label.
    tooltip_rows_by_index = {
        index: sorted(
            rows_for_index,
            key=lambda item: (-item[1], item[0]),
        )
        for index, rows_for_index in tooltip_rows_by_index.items()
    }

    empty_message = "Нет антител по текущему фильтру." if has_tests else "Нет данных."

    return ClassPlotPayload(
        title="Class I" if hla_class == 1 else "Class II",
        series=series,
        pra_points=sorted(pra_points, key=lambda item: item.x_index),
        tooltip_rows_by_index=tooltip_rows_by_index,
        test_x_indices=class_test_x_indices,
        has_tests=has_tests,
        empty_message=empty_message,
    )


def build_patient_dynamics_view(
    raw_payload: DynamicsRawPayload,
    options: DynamicsViewOptions,
) -> PatientDynamicsView:
    available_dates = sorted({row.test_date for row in raw_payload.rows})
    filtered_rows = [
        row
        for row in raw_payload.rows
        if (
            options.selected_from_date is None
            or row.test_date >= options.selected_from_date
        )
        and (
            options.selected_to_date is None
            or row.test_date <= options.selected_to_date
        )
    ]

    # Общая chronology строится до фильтрации серий, чтобы обе панели класса
    # оставались синхронными по X даже при пустом результате фильтра.
    all_dates = sorted({row.test_date for row in filtered_rows})
    x_index_by_date = {value: index for index, value in enumerate(all_dates)}
    x_labels = [format_ddmmyyyy(item) for item in all_dates]

    class1 = _build_class_payload(
        rows=filtered_rows,
        hla_class=1,
        x_index_by_date=x_index_by_date,
        all_dates=all_dates,
        options=options,
    )
    class2 = _build_class_payload(
        rows=filtered_rows,
        hla_class=2,
        x_index_by_date=x_index_by_date,
        all_dates=all_dates,
        options=options,
    )

    boundary_x = None
    primary_dates = [
        value
        for value in raw_payload.primary_test_dates
        if (options.selected_from_date is None or value >= options.selected_from_date)
        and (options.selected_to_date is None or value <= options.selected_to_date)
    ]
    secondary_dates = [
        value
        for value in raw_payload.secondary_test_dates
        if (options.selected_from_date is None or value >= options.selected_from_date)
        and (options.selected_to_date is None or value <= options.selected_to_date)
    ]

    if primary_dates and secondary_dates and x_index_by_date:
        primary_first = min(primary_dates)
        primary_last = max(primary_dates)
        secondary_first = min(secondary_dates)
        secondary_last = max(secondary_dates)

        left_date = None
        right_date = None

        if primary_last < secondary_first:
            left_date = primary_last
            right_date = secondary_first
        elif secondary_last < primary_first:
            left_date = secondary_last
            right_date = primary_first

        if left_date is not None and right_date is not None:
            left = x_index_by_date[left_date]
            right = x_index_by_date[right_date]
            boundary_x = (left + right) / 2

    return PatientDynamicsView(
        header=raw_payload.header,
        available_dates=available_dates,
        x_labels=x_labels,
        threshold_value=options.min_titer,
        class1=class1,
        class2=class2,
        boundary_x=boundary_x,
    )
