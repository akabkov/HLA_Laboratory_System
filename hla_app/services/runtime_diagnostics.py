"""Рантайм-диагностика GUI-приложения и журнал аварийных завершений.

Модуль нужен для двух задач:
1. сохранять подробный лог текущей сессии приложения;
2. делать silent-crash диагностируемым: на следующем запуске сообщать
   пользователю о предыдущем нештатном завершении и указывать путь к логу.

Отдельно включается `faulthandler`, чтобы при фатальных ошибках уровня
access violation / abort Python по возможности успел записать стек в файл.
"""

from __future__ import annotations

import atexit
import faulthandler
import json
import os
import sys
import threading
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from types import TracebackType

_APP_DIR_NAME = "HLA_Laboratory_System"
_RUNTIME_DIR_NAME = "runtime"
_LOGS_DIR_NAME = "logs"
_ACTIVE_SESSION_FILE_NAME = "active_session.json"

_ACTIVE_DIAGNOSTICS: "RuntimeDiagnostics | NullRuntimeDiagnostics | None" = None


@dataclass(frozen=True)
class PreviousUnexpectedShutdown:
    pid: int | None
    started_at_utc: str | None
    log_path: Path | None


class NullRuntimeDiagnostics:
    """Fail-safe no-op реализация на случай, если журнал не удалось включить."""

    def __init__(self, reason: str):
        self.reason = reason
        self.runtime_root: Path | None = None
        self.log_path: Path | None = None
        self.previous_unexpected_shutdown: PreviousUnexpectedShutdown | None = None

    def write_event(self, level: str, message: str) -> None:
        return None

    def write_exception(
        self,
        context: str,
        exc_type,
        exc_value,
        exc_tb: TracebackType | None,
    ) -> None:
        return None

    def install_python_hooks(self) -> None:
        return None

    def install_qt_message_handler(self) -> None:
        return None

    def build_previous_unexpected_shutdown_message(self) -> str | None:
        return None

    def mark_clean_shutdown(self) -> None:
        return None


def _runtime_root_dir() -> Path:
    base_dir = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base_dir:
        return Path(base_dir) / _APP_DIR_NAME / _RUNTIME_DIR_NAME
    return Path.home() / f".{_APP_DIR_NAME.lower()}" / _RUNTIME_DIR_NAME


def _format_now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


class RuntimeDiagnostics:
    def __init__(
        self,
        *,
        runtime_root: Path,
        log_path: Path,
        stream,
        active_session_path: Path,
        previous_unexpected_shutdown: PreviousUnexpectedShutdown | None,
    ):
        self.runtime_root = runtime_root
        self.log_path = log_path
        self._stream = stream
        self._active_session_path = active_session_path
        self.previous_unexpected_shutdown = previous_unexpected_shutdown

        self._lock = RLock()
        self._closed = False
        self._qt_message_handler_installed = False
        self._previous_sys_excepthook = sys.excepthook
        self._previous_threading_excepthook = getattr(threading, "excepthook", None)
        self._previous_unraisablehook = getattr(sys, "unraisablehook", None)
        self._previous_qt_message_handler = None

    def _write_raw(self, text: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._stream.write(text.rstrip() + "\n")
            self._stream.flush()

    def write_event(self, level: str, message: str) -> None:
        self._write_raw(f"[{_format_now_utc()}] [{level.upper()}] {message}")

    def write_exception(
        self,
        context: str,
        exc_type,
        exc_value,
        exc_tb: TracebackType | None,
    ) -> None:
        lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        self.write_event("error", context)
        self._write_raw("".join(lines).rstrip())

    def install_python_hooks(self) -> None:
        def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
            self.write_exception(
                "Неперехваченное исключение верхнего уровня.",
                exc_type,
                exc_value,
                exc_tb,
            )
            self._show_gui_crash_message(
                title="Неперехваченное исключение",
                text=(
                    "В приложении возникло неперехваченное исключение.\n\n"
                    f"{exc_value}\n\n"
                    f"Подробный лог:\n{self.log_path}"
                ),
            )
            if self._previous_sys_excepthook not in (None, sys.__excepthook__):
                self._previous_sys_excepthook(exc_type, exc_value, exc_tb)

        def _threading_excepthook(args) -> None:
            self.write_exception(
                f"Неперехваченное исключение в потоке {args.thread.name!r}.",
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
            )
            previous = self._previous_threading_excepthook
            if previous is not None and previous is not threading.__excepthook__:
                previous(args)

        def _unraisablehook(unraisable) -> None:
            exc_value = getattr(unraisable, "exc_value", None)
            exc_type = type(exc_value) if exc_value is not None else RuntimeError
            self.write_exception(
                f"Неподнимаемое исключение: {getattr(unraisable, 'err_msg', '') or 'без описания'}",
                exc_type,
                exc_value
                if exc_value is not None
                else RuntimeError("Неизвестная ошибка"),
                getattr(unraisable, "exc_traceback", None),
            )
            previous = self._previous_unraisablehook
            if previous is not None and previous is not sys.__unraisablehook__:
                previous(unraisable)

        sys.excepthook = _sys_excepthook
        if hasattr(threading, "excepthook"):
            threading.excepthook = _threading_excepthook
        if hasattr(sys, "unraisablehook"):
            sys.unraisablehook = _unraisablehook

    def install_qt_message_handler(self) -> None:
        if self._qt_message_handler_installed:
            return

        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        except Exception as exc:
            self.write_event(
                "warn",
                f"Не удалось подключить Qt message handler: {exc}",
            )
            return

        level_by_type = {
            QtMsgType.QtDebugMsg: "debug",
            QtMsgType.QtInfoMsg: "info",
            QtMsgType.QtWarningMsg: "warn",
            QtMsgType.QtCriticalMsg: "error",
            QtMsgType.QtFatalMsg: "fatal",
        }

        def _qt_message_handler(message_type, context, message) -> None:
            context_text = ""
            try:
                file_name = getattr(context, "file", "") or ""
                line_no = getattr(context, "line", 0) or 0
                function_name = getattr(context, "function", "") or ""
                parts = [part for part in (file_name, function_name) if part]
                if line_no:
                    parts.append(f"line={line_no}")
                if parts:
                    context_text = " [" + " | ".join(parts) + "]"
            except Exception:
                context_text = ""

            self.write_event(
                level_by_type.get(message_type, "info"),
                f"Qt: {message}{context_text}",
            )

            previous = self._previous_qt_message_handler
            if previous is not None:
                try:
                    previous(message_type, context, message)
                except Exception:
                    pass

        self._previous_qt_message_handler = qInstallMessageHandler(_qt_message_handler)
        self._qt_message_handler_installed = True

    def build_previous_unexpected_shutdown_message(self) -> str | None:
        info = self.previous_unexpected_shutdown
        if info is None:
            return None

        parts = [
            "Предыдущее завершение приложения выглядело нештатным.",
        ]
        if info.started_at_utc:
            parts.append(f"Начало прошлой сессии: {info.started_at_utc}")
        if info.pid is not None:
            parts.append(f"PID прошлой сессии: {info.pid}")
        if info.log_path is not None:
            parts.append(f"Диагностический лог: {info.log_path}")
        parts.append(
            "Если проблема повторится, сохраните этот лог: он поможет понять причину silent crash."
        )
        return "\n\n".join(parts)

    def _show_gui_crash_message(self, *, title: str, text: str) -> None:
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
        except Exception:
            return

        app = QApplication.instance()
        if app is None:
            return

        try:
            QMessageBox.critical(None, title, text)
        except Exception:
            return

    def mark_clean_shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return

            try:
                faulthandler.disable()
            except Exception:
                pass

            try:
                if self._active_session_path.exists():
                    self._active_session_path.unlink()
            except Exception:
                pass

            try:
                self._stream.flush()
            except Exception:
                pass

            try:
                self._stream.close()
            except Exception:
                pass

            self._closed = True


def _make_null_diagnostics(reason: str) -> NullRuntimeDiagnostics:
    global _ACTIVE_DIAGNOSTICS

    diagnostics = NullRuntimeDiagnostics(reason)
    _ACTIVE_DIAGNOSTICS = diagnostics

    try:
        sys.stderr.write("[runtime-diagnostics disabled] " + reason.rstrip() + "\n")
    except Exception:
        pass

    return diagnostics


def initialize_runtime_diagnostics() -> RuntimeDiagnostics | NullRuntimeDiagnostics:
    global _ACTIVE_DIAGNOSTICS

    if _ACTIVE_DIAGNOSTICS is not None:
        return _ACTIVE_DIAGNOSTICS

    try:
        runtime_root = _runtime_root_dir()
        logs_dir = runtime_root / _LOGS_DIR_NAME
        logs_dir.mkdir(parents=True, exist_ok=True)

        active_session_path = runtime_root / _ACTIVE_SESSION_FILE_NAME

        previous_unexpected_shutdown: PreviousUnexpectedShutdown | None = None
        if active_session_path.exists():
            try:
                payload = json.loads(active_session_path.read_text(encoding="utf-8"))
                previous_unexpected_shutdown = PreviousUnexpectedShutdown(
                    pid=int(payload["pid"]) if payload.get("pid") is not None else None,
                    started_at_utc=payload.get("started_at_utc"),
                    log_path=(
                        Path(payload["log_path"]) if payload.get("log_path") else None
                    ),
                )
            except Exception:
                previous_unexpected_shutdown = PreviousUnexpectedShutdown(
                    pid=None,
                    started_at_utc=None,
                    log_path=None,
                )

        started_at_utc = _format_now_utc()
        log_path = logs_dir / (
            f"session_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_pid{os.getpid()}.log"
        )
        stream = log_path.open("a", encoding="utf-8", buffering=1)
    except Exception as exc:
        return _make_null_diagnostics(
            f"Не удалось инициализировать рантайм-диагностику: {exc}"
        )

    diagnostics = RuntimeDiagnostics(
        runtime_root=runtime_root,
        log_path=log_path,
        stream=stream,
        active_session_path=active_session_path,
        previous_unexpected_shutdown=previous_unexpected_shutdown,
    )

    diagnostics.write_event("info", "Инициализация рантайм-диагностики.")
    diagnostics.write_event("info", f"Python: {sys.version}")
    diagnostics.write_event("info", f"Исполняемый файл: {sys.executable}")
    diagnostics.write_event("info", f"Текущий PID: {os.getpid()}")

    try:
        faulthandler.enable(file=stream, all_threads=True)
        diagnostics.write_event("info", "faulthandler включен.")
    except Exception as exc:
        diagnostics.write_event(
            "warn",
            f"Не удалось включить faulthandler: {exc}",
        )

    active_session_payload = {
        "pid": os.getpid(),
        "started_at_utc": started_at_utc,
        "log_path": str(log_path),
    }
    try:
        active_session_path.write_text(
            json.dumps(active_session_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        diagnostics.mark_clean_shutdown()
        return _make_null_diagnostics(
            f"Не удалось создать маркер активной сессии: {exc}"
        )

    diagnostics.install_python_hooks()
    atexit.register(diagnostics.mark_clean_shutdown)

    _ACTIVE_DIAGNOSTICS = diagnostics
    return diagnostics


def log_runtime_event(level: str, message: str) -> None:
    if _ACTIVE_DIAGNOSTICS is None:
        return
    _ACTIVE_DIAGNOSTICS.write_event(level, message)


def log_runtime_exception(context: str, exc: BaseException) -> None:
    if _ACTIVE_DIAGNOSTICS is None:
        return
    _ACTIVE_DIAGNOSTICS.write_exception(
        context,
        type(exc),
        exc,
        exc.__traceback__,
    )


def get_runtime_diagnostics() -> RuntimeDiagnostics | NullRuntimeDiagnostics | None:
    return _ACTIVE_DIAGNOSTICS
