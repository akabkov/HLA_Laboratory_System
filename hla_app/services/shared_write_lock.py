"""Межклиентская advisory-блокировка записывающих операций через PostgreSQL.

Модуль используется только в write-контуре: импорт, замена и удаление
исследований. Read-only сценарии, такие как окно динамики антител, не должны
использовать этот lock.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from sqlalchemy import text

from hla_app.db.engine import get_engine

# --- Advisory lock constants, общие для всех клиентов приложения ---
_LOCK_KEY_1 = 42117
_LOCK_KEY_2 = 1


class SharedWriteLockBusyError(RuntimeError):
    pass


# --- Публичный context manager для операций, меняющих файловую базу и PostgreSQL ---
@contextmanager
def acquire_shared_write_lock(
    *,
    operation_name: str,
    timeout_sec: float = 15.0,
    poll_interval_sec: float = 0.25,
):
    conn = None
    locked = False

    try:
        try:
            conn = get_engine().connect()
        except Exception as exc:
            raise RuntimeError(
                "Не удалось подключиться к PostgreSQL для межклиентской блокировки записи.\n\n"
                "В сетевом режиме изменение файловой базы и БД разрешено только "
                "при доступном PostgreSQL."
            ) from exc

        deadline = time.monotonic() + max(0.1, float(timeout_sec))

        while True:
            # Повторяем pg_try_advisory_lock до таймаута, чтобы не зависать
            # навсегда при активной записи из другого клиента.
            locked = bool(
                conn.execute(
                    text("SELECT pg_try_advisory_lock(:key1, :key2)"),
                    {"key1": _LOCK_KEY_1, "key2": _LOCK_KEY_2},
                ).scalar()
            )
            if locked:
                break

            if time.monotonic() >= deadline:
                raise SharedWriteLockBusyError(
                    f"Операция «{operation_name}» временно недоступна.\n\n"
                    "Другой клиент уже выполняет изменение файловой базы или PostgreSQL.\n"
                    "Повторите попытку позже."
                )

            time.sleep(max(0.05, float(poll_interval_sec)))

        yield

    finally:
        if conn is not None:
            try:
                if locked:
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:key1, :key2)"),
                        {"key1": _LOCK_KEY_1, "key2": _LOCK_KEY_2},
                    )
            except Exception:
                pass

            try:
                conn.close()
            except Exception:
                pass
