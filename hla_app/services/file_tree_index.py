"""Локальный индекс файлового дерева для бокового проводника.

Здесь описаны структура хитов поиска и сервис `FileTreeIndexService`, который
строит, хранит и обновляет SQLite-индекс файловой базы. Если проблемы касаются
индексного поиска, частичного индекса или скорости поиска в проводнике, этот
модуль является основной точкой диагностики.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from hla_app.utils.validators import normalize_for_match

# --- Структура результата поиска по локальному индексу ---


@dataclass(frozen=True)
class FileTreeIndexHit:
    path: Path
    is_dir: bool


# --- Сервис построения и чтения локального SQLite-индекса ---


class FileTreeIndexService:
    """
    Локальный индекс файлового дерева для быстрого поиска.

    Индекс хранится в SQLite-файле на локальном диске.
    Построение выполняется во временную БД с последующей атомарной заменой.
    Это важно, чтобы во время перестроения не испортить рабочий индекс.
    """

    SCHEMA_VERSION = "2"

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

        if not self.db_path.exists():
            with closing(self._connect(self.db_path)) as conn:
                self._init_schema(conn)

    def _connect(self, db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        return conn

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("""--sql
            CREATE TABLE IF NOT EXISTS
                entries (
                    PATH TEXT PRIMARY KEY,
                    parent_path TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    NAME TEXT NOT NULL,
                    name_folded TEXT NOT NULL,
                    name_match TEXT NOT NULL,
                    is_dir INTEGER NOT NULL
                )
        """)
        conn.execute("""--sql
            CREATE INDEX IF NOT EXISTS ix_entries_parent_path ON entries (parent_path)
            """)
        conn.execute("""--sql
            CREATE INDEX IF NOT EXISTS ix_entries_name_folded ON entries (name_folded)
            """)
        conn.execute("""--sql
            CREATE INDEX IF NOT EXISTS ix_entries_name_match ON entries (name_match)
            """)
        conn.execute("""--sql
            CREATE TABLE IF NOT EXISTS
                meta (KEY TEXT PRIMARY KEY, VALUE TEXT NOT NULL)
        """)
        conn.commit()

    def _normalize_path(self, path: Path | str) -> Path:
        return Path(path).resolve(strict=False)

    def _read_meta(self, conn: sqlite3.Connection) -> dict[str, str]:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def _write_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """--sql
            INSERT INTO
                meta (KEY, VALUE)
            VALUES
                (?, ?) ON CONFLICT (KEY)
            DO
            UPDATE
            SET
                VALUE = excluded.value
            """,
            (key, value),
        )

    def _escape_like(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def _is_source_files_rel_parts(self, rel_parts: tuple[str, ...]) -> bool:
        return len(rel_parts) >= 2 and rel_parts[1] == "source_files"

    def _is_source_files_path(
        self,
        *,
        root_dir: Path,
        path: Path | str,
    ) -> bool:
        try:
            rel_parts = self._normalize_path(path).relative_to(root_dir).parts
        except Exception:
            return False

        return self._is_source_files_rel_parts(rel_parts)

    def is_ready_for(self, root_dir: Path | str) -> bool:
        root_dir = self._normalize_path(root_dir)

        if not self.db_path.exists():
            return False

        with self._lock:
            try:
                with closing(self._connect(self.db_path)) as conn:
                    meta = self._read_meta(conn)
            except Exception:
                return False

        return (
            meta.get("schema_version") == self.SCHEMA_VERSION
            and meta.get("root_path") == str(root_dir)
            and bool(meta.get("build_finished_at_utc"))
        )

    def rebuild(self, root_dir: Path | str) -> int:
        """
        Полностью перестраивает индекс для root_dir.
        Возвращает число проиндексированных записей.
        """
        root_dir = self._normalize_path(root_dir)

        if not root_dir.exists():
            raise RuntimeError(f"Путь не существует: {root_dir}")
        if not root_dir.is_dir():
            raise RuntimeError(f"Путь не является папкой: {root_dir}")

        tmp_db_path = self.db_path.with_name(self.db_path.name + ".building")

        try:
            if tmp_db_path.exists():
                tmp_db_path.unlink()
        except Exception:
            pass

        entry_count = 0
        skipped_dir_count = 0
        batch: list[tuple[str, str, str, str, str, str, int]] = []

        def flush_batch(conn: sqlite3.Connection) -> None:
            nonlocal batch
            if not batch:
                return

            conn.executemany(
                """--sql
                INSERT INTO
                    entries (
                        PATH,
                        parent_path,
                        rel_path,
                        NAME,
                        name_folded,
                        name_match,
                        is_dir
                    )
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            batch = []

        with closing(self._connect(tmp_db_path)) as conn:
            self._init_schema(conn)
            conn.execute("DELETE FROM entries")
            conn.execute("DELETE FROM meta")

            started_at = datetime.now(UTC).isoformat()

            root_name = root_dir.name or str(root_dir)
            batch.append(
                (
                    str(root_dir),
                    "",
                    "",
                    root_name,
                    root_name.casefold(),
                    normalize_for_match(root_name, strict_first_char=True),
                    1,
                )
            )
            entry_count += 1

            stack = [root_dir]

            while stack:
                current_dir = stack.pop()

                try:
                    with os.scandir(current_dir) as it:
                        for entry in it:
                            entry_path = Path(entry.path).resolve(strict=False)
                            is_dir = entry.is_dir(follow_symlinks=False)

                            if self._is_source_files_path(
                                root_dir=root_dir,
                                path=entry_path,
                            ):
                                continue

                            try:
                                rel_path = str(entry_path.relative_to(root_dir))
                            except Exception:
                                rel_path = entry_path.name

                            batch.append(
                                (
                                    str(entry_path),
                                    str(current_dir),
                                    rel_path,
                                    entry.name,
                                    entry.name.casefold(),
                                    normalize_for_match(
                                        entry.name,
                                        strict_first_char=True,
                                    ),
                                    1 if is_dir else 0,
                                )
                            )
                            entry_count += 1

                            if is_dir:
                                stack.append(entry_path)

                            if len(batch) >= 2000:
                                flush_batch(conn)

                except Exception:
                    # Не валим весь индекс из-за одной проблемной папки,
                    # но фиксируем, что этот каталог не удалось прочитать.
                    skipped_dir_count += 1
                    continue

            flush_batch(conn)

            self._write_meta(conn, "schema_version", self.SCHEMA_VERSION)
            self._write_meta(conn, "root_path", str(root_dir))
            self._write_meta(conn, "entry_count", str(entry_count))
            self._write_meta(conn, "skipped_dir_count", str(skipped_dir_count))
            self._write_meta(conn, "build_started_at_utc", started_at)
            self._write_meta(
                conn,
                "build_finished_at_utc",
                datetime.now(UTC).isoformat(),
            )
            conn.commit()

        with self._lock:
            tmp_db_path.replace(self.db_path)

        return entry_count

    def search(
        self,
        *,
        root_dir: Path | str,
        base_dir: Path | str,
        text: str,
        limit: int = 100,
    ) -> list[FileTreeIndexHit]:
        """
        Поиск по имени файла/папки внутри base_dir (включая вложенные).
        """
        query = (text or "").strip()
        if not query:
            return []

        root_dir = self._normalize_path(root_dir)
        base_dir = self._normalize_path(base_dir)

        if not self.is_ready_for(root_dir):
            return []

        try:
            base_dir.relative_to(root_dir)
        except Exception:
            base_dir = root_dir

        if self._is_source_files_path(root_dir=root_dir, path=base_dir):
            return []

        base_dir_text = str(base_dir)
        base_dir_prefix = base_dir_text + os.sep
        query_folded = query.casefold()
        query_match = normalize_for_match(query, strict_first_char=True)
        query_like = "%" + self._escape_like(query_folded) + "%"
        query_match_like = (
            "%" + self._escape_like(query_match) + "%" if query_match else None
        )
        name_match_clause = "name_folded LIKE ? ESCAPE '\\'"
        params: list[object] = [query_like]
        if query_match_like is not None:
            name_match_clause = (
                "(name_folded LIKE ? ESCAPE '\\' OR name_match LIKE ? ESCAPE '\\')"
            )
            params.append(query_match_like)

        params.extend([base_dir_text, base_dir_prefix + "%", int(limit)])
        sql = f"""--sql
            SELECT
                PATH,
                is_dir
            FROM
                entries
            WHERE
                {name_match_clause}
                AND (
                    PATH = ?
                    OR PATH LIKE ?
                )
            ORDER BY
                is_dir DESC,
                NAME COLLATE NOCASE ASC,
                rel_path COLLATE NOCASE ASC
            LIMIT
                ?
        """

        with self._lock:
            with closing(self._connect(self.db_path)) as conn:
                rows = conn.execute(sql, params).fetchall()

        return [
            FileTreeIndexHit(path=Path(row["path"]), is_dir=bool(row["is_dir"]))
            for row in rows
            if not self._is_source_files_path(
                root_dir=root_dir,
                path=row["path"],
            )
        ]

    def entry_count(self) -> int:
        if not self.db_path.exists():
            return 0

        with self._lock:
            try:
                with closing(self._connect(self.db_path)) as conn:
                    meta = self._read_meta(conn)
            except Exception:
                return 0

        try:
            return int(meta.get("entry_count", "0"))
        except Exception:
            return 0

    def skipped_dir_count(self) -> int:
        if not self.db_path.exists():
            return 0

        with self._lock:
            try:
                with closing(self._connect(self.db_path)) as conn:
                    meta = self._read_meta(conn)
            except Exception:
                return 0

        try:
            return int(meta.get("skipped_dir_count", "0"))
        except Exception:
            return 0
