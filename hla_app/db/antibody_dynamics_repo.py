"""Read-only SQL-запросы для динамики антител.

Этот модуль ничего не знает о Qt и не содержит UI-логики. Его задача —
получить карточку пациента из основной БД приложения и загрузить строки
исследований/антител из основной и, при необходимости, дополнительной БД.
Все функции здесь должны оставаться только read-only.
"""

from __future__ import annotations

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text

from hla_app.db.engine import build_db_url, get_engine
from hla_app.services.antibody_dynamics_models import (
    DynamicsPatientHeader,
    DynamicsRawRow,
)


# --- Вспомогательное преобразование строк PostgreSQL в UI-friendly header ---
def _build_full_name(
    last_name: str | None,
    first_name: str | None,
    middle_name: str | None,
) -> str:
    # Для старых пациентов часть полей в основной БД может быть не заполнена.
    # В header окна динамики показываем только реально имеющиеся части ФИО.
    parts = [
        (last_name or "").strip(),
        (first_name or "").strip(),
    ]
    middle = (middle_name or "").strip()
    if middle:
        parts.append(middle)
    return " ".join(part for part in parts if part)


# --- Запросы к основной БД приложения ---
def load_primary_patient_header(patient_code: str) -> DynamicsPatientHeader | None:
    with get_engine().begin() as conn:
        row = (
            conn.execute(
                text("""--sql
                    SELECT
                        p.patient_code,
                        p.last_name,
                        p.first_name,
                        p.middle_name,
                        p.birth_date,
                        p.sex,
                        p.recipient_code,
                        o.title AS organ_title
                    FROM
                        patients p
                        JOIN organs o ON o.id = p.organ_id
                    WHERE
                        p.patient_code = :patient_code
                """),
                {"patient_code": patient_code},
            )
            .mappings()
            .first()
        )

    if row is None:
        return None

    return DynamicsPatientHeader(
        patient_code=row["patient_code"],
        full_name=_build_full_name(
            row["last_name"],
            row["first_name"],
            row["middle_name"],
        ),
        birth_date=row["birth_date"],
        sex=row["sex"],
        recipient_code=row["recipient_code"],
        organ_title=row["organ_title"],
    )


def find_primary_patient_candidates(
    *,
    organ_title: str,
    first_name: str | None,
    birth_date,
    middle_name: str | None,
    sex: str | None,
) -> list[dict]:
    with get_engine().begin() as conn:
        rows = (
            conn.execute(
                text("""--sql
                    SELECT
                        p.patient_code,
                        p.last_name,
                        p.new_last_name,
                        p.first_name,
                        p.middle_name,
                        p.birth_date,
                        p.recipient_code,
                        o.title AS organ_title
                    FROM
                        patients p
                        JOIN organs o ON o.id = p.organ_id
                    WHERE
                        o.title = :organ_title
                        AND p.birth_date = :birth_date
                        AND (
                            :first_name IS NULL
                            OR LOWER(COALESCE(p.first_name, '')) = LOWER(:first_name)
                        )
                        AND (
                            :middle_name IS NULL
                            OR LOWER(COALESCE(p.middle_name, '')) = LOWER(:middle_name)
                        )
                        AND (
                            :sex IS NULL
                            OR p.sex = :sex
                        )
                    ORDER BY
                        p.last_name,
                        p.first_name,
                        p.middle_name,
                        p.patient_code
                """),
                {
                    "organ_title": organ_title,
                    "first_name": first_name,
                    "birth_date": birth_date,
                    "middle_name": middle_name,
                    "sex": sex,
                },
            )
            .mappings()
            .all()
        )

    return [dict(row) for row in rows]


def load_primary_dynamics_rows(patient_code: str) -> list[DynamicsRawRow]:
    with get_engine().begin() as conn:
        # LEFT JOIN нужен, чтобы не потерять сам тест даже если в нём не окажется
        # строки антител после фильтров или особенностей данных.
        rows = (
            conn.execute(
                text("""--sql
                    SELECT
                        t.id AS test_id,
                        t.test_date,
                        t.hla_class,
                        t.pra,
                        a.gene,
                        a.allele_group,
                        a.allele,
                        a.raw_value
                    FROM
                        patients p
                        JOIN tests t ON t.patient_id = p.id
                        LEFT JOIN antibodies a ON a.test_id = t.id
                    WHERE
                        p.patient_code = :patient_code
                    ORDER BY
                        t.test_date,
                        t.hla_class,
                        a.gene,
                        a.allele_group,
                        a.allele,
                        a.raw_value
                """),
                {"patient_code": patient_code},
            )
            .mappings()
            .all()
        )

    return [
        DynamicsRawRow(
            source="primary",
            test_id=row["test_id"],
            test_date=row["test_date"],
            hla_class=row["hla_class"],
            pra=row["pra"],
            gene=row["gene"],
            allele_group=row["allele_group"],
            allele=row["allele"],
            raw_value=row["raw_value"],
        )
        for row in rows
    ]


# --- Запросы к дополнительной БД для объединения chronology ---
def load_secondary_dynamics_rows(
    *,
    db_user: str,
    db_password: str,
    db_host: str,
    db_port: int,
    db_name: str,
    recipient_code: int,
    organ_title: str,
) -> list[DynamicsRawRow]:
    engine = sa_create_engine(
        build_db_url(
            db_user=db_user,
            db_password=db_password,
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
        ),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.begin() as conn:
            # Во второй БД нельзя полагаться на числовой organ_id из primary:
            # сначала сопоставляем орган по title, затем ищем пациента по
            # (recipient_code, secondary organ_id).
            organ_row = (
                conn.execute(
                    text("""--sql
                        SELECT
                            id
                        FROM
                            organs
                        WHERE
                            title = :organ_title
                    """),
                    {"organ_title": organ_title},
                )
                .mappings()
                .first()
            )
            if organ_row is None:
                return []

            patient_row = (
                conn.execute(
                    text("""--sql
                        SELECT
                            id
                        FROM
                            patients
                        WHERE
                            recipient_code = :recipient_code
                            AND organ_id = :organ_id
                    """),
                    {
                        "recipient_code": recipient_code,
                        "organ_id": organ_row["id"],
                    },
                )
                .mappings()
                .first()
            )
            if patient_row is None:
                return []

            rows = (
                conn.execute(
                    text("""--sql
                        SELECT
                            t.id AS test_id,
                            t.test_date,
                            t.hla_class,
                            t.pra,
                            a.gene,
                            a.allele_group,
                            a.allele,
                            a.raw_value
                        FROM
                            tests t
                            LEFT JOIN antibodies a ON a.test_id = t.id
                        WHERE
                            t.patient_id = :patient_id
                        ORDER BY
                            t.test_date,
                            t.hla_class,
                            a.gene,
                            a.allele_group,
                            a.allele,
                            a.raw_value
                    """),
                    {"patient_id": patient_row["id"]},
                )
                .mappings()
                .all()
            )

        return [
            DynamicsRawRow(
                source="secondary",
                test_id=row["test_id"],
                test_date=row["test_date"],
                hla_class=row["hla_class"],
                pra=row["pra"],
                gene=row["gene"],
                allele_group=row["allele_group"],
                allele=row["allele"],
                raw_value=row["raw_value"],
            )
            for row in rows
        ]
    finally:
        engine.dispose()
