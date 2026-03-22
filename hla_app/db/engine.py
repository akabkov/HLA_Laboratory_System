"""Подключение к PostgreSQL через SQLAlchemy.

Здесь собирается строка подключения, читаются актуальные параметры БД,
кэшируется `Engine` и выполняется быстрая проверка доступности БД. Если
проблема связана именно с соединением, URL или переключением настроек БД,
искать ее стоит в этом файле.
"""

from __future__ import annotations

from threading import RLock
from urllib.parse import quote_plus

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from hla_app.config.settings import (
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
)
from hla_app.services.app_prefs import load_effective_app_preferences

# --- Кэш движка и синхронизация его пересоздания ---

_ENGINE_LOCK = RLock()
_engine: Engine | None = None
_engine_signature: tuple[str, str, str, int, str] | None = None


# --- Подготовка URL и текущей конфигурации подключения ---


def build_db_url(
    *,
    db_user: str,
    db_password: str,
    db_host: str,
    db_port: int,
    db_name: str,
) -> str:
    return (
        "postgresql+psycopg2://"
        f"{db_user}:{quote_plus(db_password)}@{db_host}:{db_port}/{db_name}"
    )


def get_current_db_config() -> tuple[str, str, str, int, str]:
    prefs = load_effective_app_preferences()

    db_user = prefs.db_user or DB_USER
    db_password = prefs.db_password if prefs.db_password is not None else DB_PASSWORD
    db_host = prefs.db_host or DB_HOST
    db_port = int(prefs.db_port or DB_PORT)
    db_name = prefs.db_name or DB_NAME

    return db_user, db_password, db_host, db_port, db_name


# --- Публичный доступ к Engine и проверка соединения ---


def get_engine() -> Engine:
    global _engine, _engine_signature

    signature = get_current_db_config()

    with _ENGINE_LOCK:
        if _engine is None or _engine_signature != signature:
            if _engine is not None:
                _engine.dispose()

            db_user, db_password, db_host, db_port, db_name = signature
            _engine = sa_create_engine(
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
            _engine_signature = signature

        return _engine


def probe_db_settings(
    *,
    db_user: str,
    db_password: str,
    db_host: str,
    db_port: int,
    db_name: str,
    timeout_sec: int = 3,
) -> None:
    test_engine = sa_create_engine(
        build_db_url(
            db_user=db_user,
            db_password=db_password,
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
        ),
        pool_pre_ping=True,
        connect_args={"connect_timeout": timeout_sec},
    )
    try:
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    finally:
        test_engine.dispose()
