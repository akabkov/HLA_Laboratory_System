from __future__ import annotations

import os
from multiprocessing import Process, Queue
from pathlib import Path
from queue import Empty


def _probe_root_dir_worker(root_dir_text: str, result_queue: Queue) -> None:
    try:
        root_dir = Path(root_dir_text)

        if not root_dir.exists():
            result_queue.put(("missing", ""))
            return

        if not root_dir.is_dir():
            result_queue.put(("not_dir", ""))
            return

        # Важно не только проверить exists(), но и реально зайти в каталог.
        with os.scandir(root_dir) as entries:
            next(entries, None)

        result_queue.put(("ok", ""))
    except Exception as exc:
        result_queue.put(("error", str(exc)))


def probe_root_dir_settings(
    *,
    root_dir: Path | str | None,
    timeout_sec: int = 3,
) -> None:
    """
    Проверяет доступность файловой базы с таймаутом.
    Если путь недоступен/зависает, выбрасывает RuntimeError.
    """
    if root_dir is None:
        raise RuntimeError("Не задан путь к файловой базе.")

    root_dir = Path(root_dir)

    result_queue: Queue = Queue()
    proc = Process(
        target=_probe_root_dir_worker,
        args=(str(root_dir), result_queue),
        daemon=True,
    )

    try:
        proc.start()
        proc.join(timeout_sec)

        if proc.is_alive():
            proc.terminate()
            proc.join(1)
            raise RuntimeError(
                "Не удалось подключиться к файловой базе.\n\n"
                f"Путь: {root_dir}\n"
                f"Превышено время ожидания ({timeout_sec} сек.)."
            )

        try:
            status, detail = result_queue.get(timeout=0.5)
        except Empty:
            raise RuntimeError(
                "Не удалось получить результат проверки файловой базы.\n\n"
                f"Путь: {root_dir}"
            )

        if status == "ok":
            return

        if status == "missing":
            raise RuntimeError(f"Файловая база не найдена: {root_dir}")

        if status == "not_dir":
            raise RuntimeError(f"Путь к файловой базе не является папкой: {root_dir}")

        raise RuntimeError(
            "Не удалось подключиться к файловой базе.\n\n"
            f"Путь: {root_dir}\n\n"
            f"{detail or 'Неизвестная ошибка.'}"
        )

    finally:
        try:
            result_queue.close()
            result_queue.join_thread()
        except Exception:
            pass

        try:
            if proc.exitcode is not None:
                proc.close()
        except Exception:
            pass
