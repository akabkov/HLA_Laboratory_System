"""Преобразование Word-документов заключения в PDF.

Модуль изолирует интеграцию с Microsoft Word COM для сценария, когда
пользователь выбирает Word-документ заключения (`.doc` / `.docx`),
а в файловую базу должен быть сохранён итоговый PDF-файл (`.pdf`).
Если проблема связана именно с конвертацией Word -> PDF, проверять нужно
этот модуль.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pythoncom
from win32com.client import DispatchEx

# Код формата экспорта Microsoft Word в PDF
_WORD_PDF_FORMAT = 17
_WORD_TO_PDF_HELPER_ARG = "--word-to-pdf-helper"
_HELPER_TIMEOUT_SEC = 120
_HELPER_RESULT_PREFIX = "hla_word_conversion__"


def _conversion_error_message(*, details: str | None = None) -> str:
    message = (
        "Не удалось преобразовать Word-документ в PDF.\n\n"
        "Наиболее частые причины:\n"
        "1. На компьютере не установлен Microsoft Word.\n"
        "2. Word не доверяет папке, из которой выбран файл.\n\n"
        "Если Word установлен, добавьте папку с заключениями в «Надёжные расположения»:\n"
        "Файл -> Параметры -> Центр управления безопасностью -> "
        "Надёжные расположения -> Добавить новое расположение.\n"
        "Укажите нужную папку.\n"
        "Если это сетевой ресурс, включите «Разрешить надёжные расположения в сети»."
    )

    if details:
        message += f"\n\nТехническая информация:\n{details}"

    return message


def _write_helper_result(
    result_path: Path,
    *,
    ok: bool,
    error: str | None = None,
) -> None:
    payload = {
        "ok": bool(ok),
        "error": error,
    }
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_helper_result(result_path: Path) -> dict | None:
    try:
        return json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_helper_command(source: Path, target: Path, result_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [
            sys.executable,
            _WORD_TO_PDF_HELPER_ARG,
            str(source),
            str(target),
            str(result_path),
        ]

    main_py = Path(__file__).resolve().parents[2] / "main.py"
    return [
        sys.executable,
        str(main_py),
        _WORD_TO_PDF_HELPER_ARG,
        str(source),
        str(target),
        str(result_path),
    ]


def _run_helper_process(source: Path, target: Path) -> None:
    fd, result_name = tempfile.mkstemp(
        prefix=_HELPER_RESULT_PREFIX,
        suffix=".json",
    )
    os.close(fd)
    result_path = Path(result_name)

    try:
        try:
            completed = subprocess.run(
                _build_helper_command(source, target, result_path),
                timeout=_HELPER_TIMEOUT_SEC,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                _conversion_error_message(
                    details=(
                        "Изолированный процесс конвертации не завершился вовремя.\n"
                        f"Timeout: {_HELPER_TIMEOUT_SEC} сек."
                    )
                )
            ) from exc

        helper_result = _read_helper_result(result_path)

        if (
            completed.returncode == 0
            and helper_result is not None
            and helper_result.get("ok")
            and target.exists()
        ):
            return

        details_parts: list[str] = [
            f"Код завершения процесса конвертации: {completed.returncode}"
        ]

        if helper_result is None:
            details_parts.append(
                "Помощник конвертации не смог записать диагностический результат."
            )
        else:
            helper_error = (helper_result.get("error") or "").strip()
            if helper_error:
                details_parts.append(helper_error)

        raise RuntimeError(_conversion_error_message(details="\n".join(details_parts)))
    finally:
        try:
            result_path.unlink(missing_ok=True)
        except Exception:
            pass


def _convert_word_document_to_pdf_in_process(source_path: Path, pdf_path: Path) -> None:

    source = Path(source_path).resolve()
    target = Path(pdf_path).resolve()

    if source.suffix.lower() not in {".doc", ".docx"}:
        raise ValueError(f"Неподдерживаемый формат Word-документа: {source.suffix}")

    target.parent.mkdir(parents=True, exist_ok=True)

    pythoncom.CoInitialize()
    word = None
    document = None
    temp_source = None
    try:
        # Word может показывать системный prompt, если исходный DOCX уже открыт
        # кем-то на редактирование. Работа через временную копию убирает привязку
        # к lock-состоянию исходного файла и делает конвертацию стабильнее.
        temp_source = _build_temp_word_copy(source)

        word = DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0

        document = word.Documents.Open(
            str(temp_source),
            ReadOnly=True,
            ConfirmConversions=False,
            AddToRecentFiles=False,
            NoEncodingDialog=True,
        )
        document.ExportAsFixedFormat(str(target), _WORD_PDF_FORMAT)

    except Exception as exc:
        raise RuntimeError(_conversion_error_message()) from exc

    finally:
        if document is not None:
            try:
                document.Close(False)
            except Exception:
                pass

        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass

        if temp_source is not None:
            try:
                temp_source.unlink(missing_ok=True)
            except Exception:
                pass

        pythoncom.CoUninitialize()


def _build_temp_word_copy(source: Path) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix="hla_word_source__", suffix=source.suffix)
    os.close(fd)
    temp_source = Path(temp_name)
    shutil.copy2(source, temp_source)
    return temp_source


# --- Публичное преобразование Word-документа заключения в PDF ---
def convert_word_document_to_pdf(source_path: Path, pdf_path: Path) -> None:
    source = Path(source_path).resolve()
    target = Path(pdf_path).resolve()
    _run_helper_process(source, target)


def run_word_document_to_pdf_helper_from_argv(argv: list[str]) -> int | None:
    if len(argv) < 2 or argv[1] != _WORD_TO_PDF_HELPER_ARG:
        return None

    if len(argv) != 5:
        return 2

    source = Path(argv[2])
    target = Path(argv[3])
    result_path = Path(argv[4])
    try:
        _convert_word_document_to_pdf_in_process(source, target)
        _write_helper_result(result_path, ok=True)
        return 0
    except Exception as exc:
        try:
            _write_helper_result(
                result_path,
                ok=False,
                error=str(exc),
            )
        except Exception:
            pass
        return 1
