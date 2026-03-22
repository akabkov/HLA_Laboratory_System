"""Преобразование Word-документов заключения в PDF.

Модуль изолирует интеграцию с Microsoft Word COM для сценария, когда
пользователь выбирает Word-документ заключения (`.doc` / `.docx`),
а в файловую базу должен быть сохранён итоговый PDF-файл (`.pdf`).
Если проблема связана именно с конвертацией Word -> PDF, проверять нужно
этот модуль.
"""

from __future__ import annotations

from pathlib import Path

# Код формата экспорта Microsoft Word в PDF
_WORD_PDF_FORMAT = 17


# --- Публичное преобразование Word-документа заключения в PDF ---
def convert_word_document_to_pdf(source_path: Path, pdf_path: Path) -> None:

    source = Path(source_path).resolve()
    target = Path(pdf_path).resolve()

    if source.suffix.lower() not in {".doc", ".docx"}:
        raise ValueError(f"Неподдерживаемый формат Word-документа: {source.suffix}")

    target.parent.mkdir(parents=True, exist_ok=True)

    import pythoncom
    from win32com.client import DispatchEx

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0

        document = word.Documents.Open(str(source), ReadOnly=True)
        document.ExportAsFixedFormat(str(target), _WORD_PDF_FORMAT)

    except Exception as exc:
        raise RuntimeError(
            "Не удалось преобразовать Word-документ в PDF.\n\n"
            "Наиболее частые причины:\n"
            "1. На компьютере не установлен Microsoft Word.\n"
            "2. Word не доверяет папке, из которой выбран файл.\n\n"
            "Если Word установлен, добавьте папку с заключениями в «Надёжные расположения»:\n"
            "Файл -> Параметры -> Центр управления безопасностью -> "
            "Надёжные расположения -> Добавить новое расположение.\n"
            "Укажите нужную папку.\n"
            "Если это сетевой ресурс, включите «Разрешить надёжные расположения в сети»."
        ) from exc

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

        pythoncom.CoUninitialize()
