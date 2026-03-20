from __future__ import annotations

import sys
from multiprocessing import freeze_support
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from hla_app.services.root_dir_service import probe_root_dir_settings
from hla_app.services.startup_service import (
    collect_startup_state,
    install_qt_translators,
    probe_database_connection,
)
from hla_app.ui.startup_splash import StartupSplash


def main() -> None:
    freeze_support()
    app = QApplication(sys.argv)

    icon_path = Path(__file__).resolve().parent / "assets" / "app.ico"
    background_path = Path(__file__).resolve().parent / "assets" / "startup.png"

    app.setWindowIcon(QIcon(str(icon_path)))

    splash = StartupSplash(
        background_path=background_path if background_path.exists() else None
    )
    splash.show()
    splash.raise_()
    splash.activateWindow()
    QApplication.processEvents()

    try:
        splash.set_step(
            5,
            "Инициализация приложения...",
            "QApplication создана успешно.",
            level="ok",
        )

        install_qt_translators(app)
        splash.set_step(
            15,
            "Загрузка локализации...",
            "Переводчики Qt загружены: Russian / Belarus.",
            level="ok",
        )

        state = collect_startup_state()
        splash.set_step(
            28,
            "Чтение настроек...",
            "Пользовательские настройки приложения загружены.",
            level="ok",
        )

        root_dir_error: str | None = None

        try:
            probe_root_dir_settings(root_dir=state.root_dir)
            splash.append_log(
                f"Подключение к файловой базе выполнено успешно: {state.root_dir}",
                level="ok",
            )
            splash.set_step(
                55,
                "Проверка файловой базы...",
                "Файловая база доступна.",
                level="ok",
            )
        except Exception as exc:
            root_dir_error = str(exc)
            splash.set_step(
                55,
                "Проверка файловой базы...",
                "Подключение не удалось. Приложение будет запущено с ограничениями.",
                level="warn",
            )
            splash.append_log(root_dir_error, level="error")

        if state.conclusion_save_dir is None:
            splash.append_log(
                f"Папка заключений по умолчанию: Рабочий стол ({state.desktop_dir})",
                level="info",
            )
        elif state.conclusion_save_dir.exists():
            splash.append_log(
                f"Папка сохранения заключений: {state.conclusion_save_dir}",
                level="ok",
            )
        else:
            splash.append_log(
                "Папка заключений будет создана при первом сохранении: "
                f"{state.conclusion_save_dir}",
                level="warn",
            )

        if state.sum_save_dir is None:
            splash.append_log(
                "Суммарный титр: сохранение рядом с исходным CSV",
                level="info",
            )
        elif state.sum_save_dir.exists():
            splash.append_log(
                f"Папка для суммарного титра: {state.sum_save_dir}",
                level="ok",
            )
        else:
            splash.append_log(
                "Папка для суммарного титра будет создана при первом сохранении: "
                f"{state.sum_save_dir}",
                level="warn",
            )

        if root_dir_error is None:
            splash.append_log("Пути и каталоги успешно обработаны.", level="ok")
        else:
            splash.append_log(
                "Пути и каталоги обработаны, но файловая база недоступна.",
                level="warn",
            )

        db_connection_error: str | None = None

        try:
            probe_database_connection()
            splash.set_step(
                75,
                "Подключение к PostgreSQL...",
                (
                    "Соединение установлено успешно: "
                    f"{state.db_host}:{state.db_port} / {state.db_name}"
                ),
                level="ok",
            )
        except Exception as exc:
            db_connection_error = str(exc)
            splash.set_step(
                75,
                "Подключение к PostgreSQL...",
                "Подключение не удалось. Приложение будет запущено с ограничениями.",
                level="warn",
            )
            splash.append_log(db_connection_error, level="error")

        splash.set_step(
            90,
            "Подготовка главного окна...",
            "Инициализация компонентов интерфейса...",
            level="info",
        )

        from hla_app.ui.main_window import MainWindow

        window = MainWindow(root_dir_available=root_dir_error is None)

        wanted = window.calculate_initial_size()
        screen = splash.screen()
        if screen is None:
            screen = app.primaryScreen()

        if screen is not None:
            available = screen.availableGeometry().size()

            if (
                wanted.width() <= available.width()
                and wanted.height() <= available.height()
            ):
                window.resize(wanted)
                window.show()
            else:
                window.showMaximized()
        else:
            window.resize(wanted)
            window.show()

        splash.set_step(
            100,
            "Запуск завершён",
            "Главное окно готово к работе.",
            level="ok",
        )

        splash.close()

        if root_dir_error:
            QMessageBox.warning(
                window,
                "Файловая база недоступна",
                "Приложение запущено без подключения к файловой базе.\n\n"
                "Импорт и проводник временно недоступны.\n"
                "Проверьте путь к файловой базе в окне «Настройки».\n\n"
                f"{root_dir_error}",
            )

        if db_connection_error:
            QMessageBox.warning(
                window,
                "PostgreSQL недоступен",
                "Приложение запущено без подключения к PostgreSQL.\n\n"
                "Проверьте настройки подключения в окне «Настройки».\n\n"
                f"{db_connection_error}",
            )

        sys.exit(app.exec())

    except Exception as exc:
        splash.set_step(
            100,
            "Ошибка запуска",
            str(exc),
            level="error",
        )
        QMessageBox.critical(
            None,
            "Ошибка запуска приложения",
            f"Приложение не удалось запустить:\n\n{exc}",
        )
        splash.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
