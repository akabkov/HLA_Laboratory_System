"""Стартовые проверки и инициализация окружения приложения.

Здесь собирается `StartupState`, подключаются Qt-переводчики, определяются
рабочие каталоги и выполняются ранние проверки доступности корня базы и БД.
Если проблема воспроизводится еще до открытия главного окна, искать ее стоит
в этом модуле.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, QLocale, QStandardPaths, QTranslator
from PySide6.QtWidgets import QApplication
from sqlalchemy import text

from hla_app.db.engine import get_engine
from hla_app.services.app_prefs import load_effective_app_preferences

# --- Снимок стартового состояния приложения ---


@dataclass(frozen=True)
class StartupState:
    root_dir: Path
    desktop_dir: Path
    db_host: str
    db_port: int
    db_name: str
    conclusion_save_dir: Path | None
    sum_save_dir: Path | None


# --- Вспомогательные функции определения окружения запуска ---


def desktop_dir() -> Path:
    desktop = QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)
    return Path(desktop) if desktop else Path.home()


def install_qt_translators(app: QApplication) -> None:
    qt_translator = QTranslator(app)
    base_translator = QTranslator(app)

    translations_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    locale = QLocale(QLocale.Language.Russian, QLocale.Country.Belarus)

    qt_translator.load(locale, "qtbase", "_", translations_path)
    base_translator.load(locale, "qt", "_", translations_path)

    app.installTranslator(qt_translator)
    app.installTranslator(base_translator)

    # Сохраняем ссылки на переводчики, чтобы они не были собраны GC.
    app._qt_translator = qt_translator
    app._base_translator = base_translator


# --- Сбор стартовых настроек и ранние проверки БД ---


def collect_startup_state() -> StartupState:
    prefs = load_effective_app_preferences()
    desktop = desktop_dir()

    root_dir = prefs.root_dir
    if root_dir is None:
        raise RuntimeError("Не задан путь к файловой базе.")

    return StartupState(
        root_dir=root_dir,
        desktop_dir=desktop,
        db_host=prefs.db_host or "",
        db_port=int(prefs.db_port or 0),
        db_name=prefs.db_name or "",
        conclusion_save_dir=prefs.conclusion_save_dir,
        sum_save_dir=prefs.sum_save_dir,
    )


def probe_database_connection() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
