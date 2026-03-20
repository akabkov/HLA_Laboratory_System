from __future__ import annotations

from sqlalchemy import text


def insert_organ(conn, organ: str) -> int:
    result = conn.execute(
        text(
            """--sql
            INSERT INTO organs (title)
            VALUES (:title)
            ON CONFLICT (title) DO UPDATE SET title = organs.title
            RETURNING id
            """
        ),
        {"title": organ},
    )
    return result.fetchone()[0]


def insert_patient(
    conn,
    patient_code: str,
    last_name: str,
    new_last_name: str | None,
    first_name: str,
    middle_name: str | None,
    birth_date,
    sex: str | None,
) -> int:
    result = conn.execute(
        text(
            """--sql
            INSERT INTO patients (
                patient_code,
                last_name,
                new_last_name,
                first_name,
                middle_name,
                birth_date,
                sex
            )
            VALUES (
                :patient_code,
                :last_name,
                :new_last_name,
                :first_name,
                :middle_name,
                :birth_date,
                :sex
            )
            ON CONFLICT (patient_code) DO UPDATE SET
                last_name = EXCLUDED.last_name,
                new_last_name = EXCLUDED.new_last_name,
                first_name = EXCLUDED.first_name,
                middle_name = EXCLUDED.middle_name,
                birth_date = EXCLUDED.birth_date,
                sex = EXCLUDED.sex
            RETURNING id
            """
        ),
        {
            "patient_code": patient_code,
            "last_name": last_name,
            "new_last_name": new_last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "birth_date": birth_date,
            "sex": sex,
        },
    )
    return result.fetchone()[0]


def insert_test(
    conn,
    organ_id: int,
    patient_id: int,
    test_date,
    hla_class: int,
    pra: int,
) -> int:
    result = conn.execute(
        text(
            """--sql
            INSERT INTO tests (organ_id, patient_id, test_date, hla_class, pra)
            VALUES (:organ_id, :patient_id, :test_date, :hla_class, :pra)
            ON CONFLICT (patient_id, organ_id, test_date, hla_class) DO UPDATE SET
                pra = EXCLUDED.pra
            RETURNING id
            """
        ),
        {
            "organ_id": organ_id,
            "patient_id": patient_id,
            "test_date": test_date,
            "hla_class": hla_class,
            "pra": pra,
        },
    )
    row = result.fetchone()
    if row is None:
        raise RuntimeError("Не удалось получить id записи tests после UPSERT.")
    return row[0]


def insert_antibodies(conn, test_id: int, antibodies: list[dict]) -> None:
    if not antibodies:
        return

    rows = [
        {
            "test_id": test_id,
            "gene": antibody["gene"],
            "allele_group": antibody["allele_group"],
            "allele": antibody["allele"],
            "pct_positive": antibody["pct_positive"],
            "raw_value": antibody["raw_value"],
            "mfi_lra": antibody["mfi_lra"],
        }
        for antibody in antibodies
    ]

    conn.execute(
        text(
            """--sql
            INSERT INTO antibodies (
                test_id,
                gene,
                allele_group,
                allele,
                pct_positive,
                raw_value,
                mfi_lra
            )
            VALUES (
                :test_id,
                :gene,
                :allele_group,
                :allele,
                :pct_positive,
                :raw_value,
                :mfi_lra
            )
            ON CONFLICT DO NOTHING
            """
        ),
        rows,
    )


def get_patient_id_by_code(conn, patient_code: str) -> int | None:
    result = conn.execute(
        text(
            """--sql
            SELECT id
            FROM patients
            WHERE patient_code = :patient_code
            """
        ),
        {"patient_code": patient_code},
    )
    row = result.fetchone()
    return row[0] if row else None


def update_existing_patient_by_code(
    conn,
    current_patient_code: str,
    new_patient_code: str,
    last_name: str,
    new_last_name: str | None,
    first_name: str,
    middle_name: str | None,
    birth_date,
    sex: str | None,
) -> int | None:
    result = conn.execute(
        text(
            """--sql
            UPDATE patients
            SET
                patient_code = :new_patient_code,
                last_name = :last_name,
                new_last_name = :new_last_name,
                first_name = :first_name,
                middle_name = :middle_name,
                birth_date = :birth_date,
                sex = :sex
            WHERE patient_code = :current_patient_code
            RETURNING id
            """
        ),
        {
            "current_patient_code": current_patient_code,
            "new_patient_code": new_patient_code,
            "last_name": last_name,
            "new_last_name": new_last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "birth_date": birth_date,
            "sex": sex,
        },
    )
    row = result.fetchone()
    return row[0] if row else None


def delete_test_by_patient_code(
    conn,
    *,
    organ: str,
    patient_code: str,
    test_date,
    hla_class: int,
) -> int:
    result = conn.execute(
        text(
            """--sql
            DELETE FROM tests t
            USING patients AS p, organs AS o
            WHERE t.patient_id = p.id
              AND t.organ_id = o.id
              AND p.patient_code = :patient_code
              AND o.title = :organ
              AND t.test_date = :test_date
              AND t.hla_class = :hla_class
            RETURNING t.id
            """
        ),
        {
            "organ": organ,
            "patient_code": patient_code,
            "test_date": test_date,
            "hla_class": hla_class,
        },
    )
    return len(result.fetchall())


def delete_orphan_patient_by_code(conn, patient_code: str) -> int:
    result = conn.execute(
        text(
            """--sql
            DELETE FROM patients p
            WHERE
                p.patient_code = :patient_code
                AND NOT EXISTS (
                    SELECT
                        1
                    FROM
                        tests t
                    WHERE
                        t.patient_id = p.id
                )
            RETURNING
                p.id
            """
        ),
        {"patient_code": patient_code},
    )
    return len(result.fetchall())
