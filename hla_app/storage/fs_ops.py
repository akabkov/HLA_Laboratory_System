"""Низкоуровневые файловые операции проекта.

Здесь находятся retry-обертки для Windows/сетевых ошибок, удаление, копирование,
rename и вспомогательные функции работы с папками пациентов, исследованиями и
`source_files`. Если сбой связан именно с файловой системой, блокировками или
переименованием, вероятнее всего он локализован в этом модуле.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from hla_app.config.managed_files import (
    CONCLUSION_FILE_NAMES,
    MANAGED_STUDY_FILE_NAMES,
    class_result_file_name,
    conclusion_file_name,
)
from hla_app.utils.validators import (
    format_ddmmyyyy,
    normalize_for_match,
    parse_ddmmyyyy,
    patient_dir_name_full,
)

# Суффикс даты исследования: ..._дд.мм.гггг или ..._дд.мм.гггг__2
BIRTH_SUFFIX_RE = re.compile(r"_(\d{2}\.\d{2}\.\d{4})(?:_(ж|м|f|m))?(?:__\d+)?$")
NEWLN_RE = re.compile(r"^(?P<main>[^()]+)(\((?P<new>[^()]+)\))?$")


# --- Retry-обертки для операций Windows/SMB и базовые CRUD-функции FS ---
def _is_fs_locked_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True

    if isinstance(exc, OSError):
        return getattr(exc, "winerror", None) in {5, 32}

    return False


def _raise_fs_locked_runtime_error(action: str, target, exc: Exception) -> None:
    raise RuntimeError(
        f"Не удалось {action}: {target}\n\n"
        "В данный момент папка и/или её содержимое открыты или используются "
        "другим приложением либо другим пользователем на сетевом ресурсе.\n\n"
        "Закройте эту папку в Проводнике Windows, а также связанные файлы "
        "(например, Excel, просмотр JPEG-изображений, PDF-файлов или "
        "Word-документов), и повторите операцию."
    ) from exc


def _run_fs_with_retry(
    *,
    action: str,
    target,
    op,
    attempts: int = 5,
    delay_sec: float = 0.5,
):
    last_exc: Exception | None = None

    for attempt in range(attempts):
        try:
            return op()
        except Exception as exc:
            last_exc = exc

            if _is_fs_locked_error(exc) and attempt < attempts - 1:
                time.sleep(delay_sec)
                continue

            if _is_fs_locked_error(exc):
                _raise_fs_locked_runtime_error(action, target, exc)

            raise

    if last_exc is not None:
        raise last_exc


def _unlink_with_retry(path: Path) -> None:
    _run_fs_with_retry(
        action="удалить файл",
        target=path,
        op=lambda: path.unlink(),
    )


def _rmdir_with_retry(path: Path) -> None:
    _run_fs_with_retry(
        action="удалить папку",
        target=path,
        op=lambda: path.rmdir(),
    )


def _rmtree_with_retry(path: Path) -> None:
    _run_fs_with_retry(
        action="удалить папку исследования",
        target=path,
        op=lambda: shutil.rmtree(path),
    )


def _rename_path_with_retry(src: Path, dst: Path):
    return _run_fs_with_retry(
        action="переименовать объект",
        target=f"{src} -> {dst}",
        op=lambda: src.rename(dst),
    )


def _replace_path_with_retry(src: Path, dst: Path):
    return _run_fs_with_retry(
        action="заменить файл",
        target=f"{src} -> {dst}",
        op=lambda: src.replace(dst),
    )


def delete_file(path: Path) -> None:
    _unlink_with_retry(path)


def delete_empty_dir(path: Path) -> None:
    _rmdir_with_retry(path)


def delete_dir_tree(path: Path) -> None:
    _rmtree_with_retry(path)


def create_dir(path: Path, *, parents: bool = False, exist_ok: bool = False) -> Path:
    _run_fs_with_retry(
        action="создать папку",
        target=path,
        op=lambda: path.mkdir(parents=parents, exist_ok=exist_ok),
    )
    return path


def copy_file(src: Path, dst: Path) -> Path:
    create_dir(dst.parent, parents=True, exist_ok=True)
    _run_fs_with_retry(
        action="скопировать файл",
        target=f"{src} -> {dst}",
        op=lambda: shutil.copy2(src, dst),
    )
    return dst


def copy_dir_tree(src: Path, dst: Path) -> Path:
    create_dir(dst.parent, parents=True, exist_ok=True)
    _run_fs_with_retry(
        action="скопировать папку",
        target=f"{src} -> {dst}",
        op=lambda: shutil.copytree(src, dst),
    )
    return dst


def rename_path(src: Path, dst: Path) -> Path:
    if src == dst:
        return dst
    return _rename_path_with_retry(src, dst)


def replace_file(src: Path, dst: Path) -> Path:
    return _replace_path_with_retry(src, dst)


def list_existing_study_files(test_dir: Path) -> list[Path]:
    if not test_dir.exists() or not test_dir.is_dir():
        return []

    return [
        test_dir / name
        for name in MANAGED_STUDY_FILE_NAMES
        if (test_dir / name).is_file()
    ]


# --- Разбор имени папки пациента и helpers для поиска совпадений ---
def split_patient_folder_name(
    folder_name: str,
) -> tuple[str, str, str | None, str | None]:
    """
    Возвращает:
      base_name: имя без суффикса _дд.мм.гггг[_ж|_м] (и без __N, если есть)
      last_block: строка фамилии с возможными (Новая)
      birth_suffix: 'дд.мм.гггг' или None
      sex_suffix: 'ж' / 'м' / 'f' / 'm' / None
    """
    birth_suffix = None
    sex_suffix = None

    m = BIRTH_SUFFIX_RE.search(folder_name)
    if m:
        birth_suffix = m.group(1)
        sex_suffix = m.group(2)
        base_name = folder_name[: m.start()]
    else:
        base_name = folder_name

    last_block = base_name.split("_", 1)[0]
    return base_name, last_block, birth_suffix, sex_suffix


def extract_lastnames(
    last_block: str,
    *,
    strict_first_char: bool = False,
) -> set[str]:
    out: set[str] = set()
    s = (last_block or "").strip()
    if not s:
        return out

    m = NEWLN_RE.match(s)
    if not m:
        out.add(normalize_for_match(s, strict_first_char=strict_first_char))
        return out

    main = (m.group("main") or "").strip()
    new = (m.group("new") or "").strip()

    if main:
        out.add(normalize_for_match(main, strict_first_char=strict_first_char))
    if new:
        out.add(normalize_for_match(new, strict_first_char=strict_first_char))

    return out


def _normalize_fullname_base_for_match(base_name: str) -> str:
    """
    Нормализует base_name вида:

      Фамилия(Новая)_Имя.Отчество
      Фамилия_Имя.Отчество

    Особенность:
    - у основной фамилии первая буква строгая;
    - у новой фамилии в скобках первая буква тоже строгая;
    - имя/отчество нормализуются обычной фаззи-логикой.
    """
    s = (base_name or "").strip()
    if not s:
        return ""

    parts = s.split("_", 1)
    if len(parts) != 2:
        return normalize_for_match(s, strict_first_char=True)

    last_block, name_block = parts[0].strip(), parts[1].strip()

    m = NEWLN_RE.match(last_block)
    if m:
        main = (m.group("main") or "").strip()
        new = (m.group("new") or "").strip()

        main_norm = normalize_for_match(main, strict_first_char=True) if main else ""
        new_norm = normalize_for_match(new, strict_first_char=True) if new else ""

        last_norm = f"{main_norm}({new_norm})" if new_norm else main_norm
    else:
        last_norm = normalize_for_match(last_block, strict_first_char=True)

    name_norm = normalize_for_match(name_block)

    return f"{last_norm}_{name_norm}"


def extract_initials_from_folder(folder_name: str) -> str:
    """
    Из 'Фамилия(Новая)_Имя.Отчество_дд.мм.гггг' -> 'И.О.'
    Из 'Фамилия_Имя_дд.мм.гггг' -> 'И.'
    """
    base_name, _last_block, _birth, _sex = split_patient_folder_name(folder_name)

    parts = base_name.split("_", 1)
    if len(parts) < 2:
        return ""

    name_part = parts[1]
    if "." in name_part:
        fn, mn = name_part.split(".", 1)
        fn = (fn or "").strip()
        mn = (mn or "").strip()
        i1 = (fn[:1] or "").upper()
        i2 = (mn[:1] or "").upper()
        return f"{i1}.{i2}." if i2 else f"{i1}."
    else:
        fn = name_part.strip()
        i1 = (fn[:1] or "").upper()
        return f"{i1}."


def _first_initial_only(initials_value: str) -> str:
    """
    'И.О.' -> 'И.'
    'И.'   -> 'И.'
    'И'    -> 'И.'
    ''     -> ''
    """
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", initials_value or "")
    return f"{letters[0].upper()}." if letters else ""


# --- Модели найденных совпадений и распарсенной папки пациента ---
@dataclass
class MatchCandidate:
    folder: Path
    display: str


@dataclass(frozen=True)
class ParsedPatientFolder:
    folder: Path
    folder_name: str
    last_name: str
    new_last_name: str
    first_name: str
    middle_name: str
    birth_date: date | None
    sex: str | None
    is_new_format: bool


# --- Поиск папок пациентов и работа с корневой структурой органов ---
def parse_patient_folder_record(folder: Path) -> ParsedPatientFolder | None:
    """
    Разбирает имя папки пациента на части.

    Примеры:
      Иванов(Петров)_Мария.Сергеевна_01.02.1990_ж
      Иванов(Петров)_Мария.Сергеевна_01.02.1990_м
      Иванов_А.Е
      Иванов_А
    """
    if not folder or not folder.name:
        return None

    folder_name = folder.name
    base_name, last_block, birth_suffix, sex_suffix = split_patient_folder_name(
        folder_name
    )

    m = NEWLN_RE.match((last_block or "").strip())
    if m:
        last_name = (m.group("main") or "").strip()
        new_last_name = (m.group("new") or "").strip()
    else:
        last_name = (last_block or "").strip()
        new_last_name = ""

    name_part = ""
    parts = base_name.split("_", 1)
    if len(parts) == 2:
        name_part = parts[1].strip()

    first_name = ""
    middle_name = ""

    if name_part:
        if "." in name_part:
            first_name, middle_name = name_part.split(".", 1)
        else:
            first_name = name_part

    first_name = first_name.strip().strip(".")
    middle_name = middle_name.strip().strip(".")

    birth_date = parse_ddmmyyyy(birth_suffix) if birth_suffix else None

    if sex_suffix == "ж":
        sex = "f"
    elif sex_suffix == "м":
        sex = "m"
    elif sex_suffix in ("f", "m"):
        sex = sex_suffix
    else:
        sex = None

    return ParsedPatientFolder(
        folder=folder,
        folder_name=folder_name,
        last_name=last_name,
        new_last_name=new_last_name,
        first_name=first_name,
        middle_name=middle_name,
        birth_date=birth_date,
        sex=sex,
        is_new_format=birth_date is not None,
    )


def find_patient_folders_by_lastname_prefix(
    organ_dir: Path,
    query: str,
    *,
    limit: int = 12,
) -> list[ParsedPatientFolder]:
    """
    Автоподсказки по фамилии:
    - используем ту же нормализацию, что и для поиска совпадений;
    - ищем только по первой части имени папки (до первого '_');
    - в выдаче показываем полное имя папки.
    """
    want = normalize_for_match(query, strict_first_char=True)
    if not want:
        return []

    scored: list[tuple[int, int, str, ParsedPatientFolder]] = []

    for p in list_patient_folders(organ_dir):
        _, last_block, _birth, _sex = split_patient_folder_name(p.name)
        folder_lastnames = extract_lastnames(last_block, strict_first_char=True)

        best_score: int | None = None

        for ln in folder_lastnames:
            if ln == want:
                score = 0
            elif ln.startswith(want):
                score = 1
            elif want in ln:
                score = 2
            else:
                continue

            if best_score is None or score < best_score:
                best_score = score

        if best_score is None:
            continue

        parsed = parse_patient_folder_record(p)
        if parsed is None:
            continue

        scored.append(
            (
                best_score,
                0 if parsed.birth_date is not None else 1,  # новый формат выше
                parsed.folder_name.lower(),
                parsed,
            )
        )

    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [item[3] for item in scored[:limit]]


def get_base_tree_paths(root: Path, organ: str) -> tuple[Path, Path]:
    """
    Только вычисляет пути базового дерева файловой базы
    и ничего не создаёт на диске.
    """
    organ_dir = root / organ
    src_dir = organ_dir / "source_files"
    return organ_dir, src_dir


def ensure_base_tree(root: Path, organ: str) -> tuple[Path, Path]:
    """
    Гарантирует наличие базового дерева файловой базы.
    Использовать только перед реальной записью/заменой файлов.
    """
    organ_dir, src_dir = get_base_tree_paths(root, organ)
    src_dir.mkdir(parents=True, exist_ok=True)
    return organ_dir, src_dir


def list_patient_folders(organ_dir: Path) -> list[Path]:
    if not organ_dir.exists():
        return []
    return [p for p in organ_dir.iterdir() if p.is_dir() and p.name != "source_files"]


def find_matches_by_lastnames_initials(
    organ_dir: Path,
    target_lastnames: list[str],
    target_initials: str,
    *,
    initials_mode: str = "full",
) -> list[MatchCandidate]:
    """
    Совпадение: (любая из фамилий) + инициалы.
    initials_mode:
      - "full"       -> сравнение по обоим инициалам (новый формат)
      - "first_only" -> сравнение только по первому инициалу (старый формат)

    Фамилии учитывают скобки: Фамилия(Новая)
    Нормализация фамилий ФАЗЗИ.
    """
    want_ln = {
        normalize_for_match(x, strict_first_char=True) for x in target_lastnames if x
    }
    if not want_ln:
        return []

    want_init_full = (target_initials or "").upper()
    want_init_first = _first_initial_only(want_init_full)

    out: list[MatchCandidate] = []
    for p in list_patient_folders(organ_dir):
        _base, last_block, _birth, _sex = split_patient_folder_name(p.name)
        folder_lastnames = extract_lastnames(last_block, strict_first_char=True)
        if not (folder_lastnames & want_ln):
            continue

        folder_init = extract_initials_from_folder(p.name).upper()

        if initials_mode == "first_only":
            if want_init_first and _first_initial_only(folder_init) != want_init_first:
                continue
        else:
            if want_init_full and folder_init != want_init_full:
                continue

        out.append(MatchCandidate(folder=p, display=p.name))

    return out


def find_matches_by_fullname_base(
    organ_dir: Path,
    target_base_name: str,
) -> list[MatchCandidate]:
    """
    Совпадение по ФИО-части (до _дд.мм.гггг), т.е. base_name.
    Ищем только папки НОВОГО формата (где есть birth_suffix).

    Для основной фамилии и новой фамилии в скобках
    первая буква учитывается строго.
    """
    want = _normalize_fullname_base_for_match(target_base_name)
    if not want:
        return []

    out: list[MatchCandidate] = []
    for p in list_patient_folders(organ_dir):
        base_name, _last_block, birth, _sex = split_patient_folder_name(p.name)
        if not birth:
            continue

        if _normalize_fullname_base_for_match(base_name) == want:
            out.append(MatchCandidate(folder=p, display=p.name))

    out.sort(key=lambda x: x.display)
    return out


def ensure_patient_folder_full(
    organ_dir: Path,
    last_name: str,
    new_last_name: str,
    first_name: str,
    middle_name: str,
    birth_date: date,
    sex: str | None = None,
) -> Path:
    base_name = patient_dir_name_full(
        last_name,
        new_last_name,
        first_name,
        middle_name,
        birth_date,
        sex=sex,
    )
    patient_dir = organ_dir / base_name

    if not patient_dir.exists():
        patient_dir.mkdir(parents=True, exist_ok=False)
        return patient_dir

    k = 2
    while True:
        candidate = organ_dir / f"{base_name}__{k}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        k += 1


def ensure_test_date_folder(patient_dir: Path, test_date: date) -> Path:
    dname = format_ddmmyyyy(test_date)
    tdir = patient_dir / dname
    tdir.mkdir(parents=True, exist_ok=True)
    return tdir


# --- Работа с source_files и файлами конкретного исследования ---
def build_source_csv_path(
    src_dir: Path,
    organ: str,
    patient_folder_name: str,
    analysis_date: date,
    hla_class: int,
) -> Path:
    dst_name = (
        f"{organ}__{patient_folder_name}__"
        f"{format_ddmmyyyy(analysis_date)}__{hla_class}_класс.csv"
    )
    return src_dir / dst_name


def list_source_class_files(
    src_dir: Path,
    organ: str,
    patient_folder_name: str,
    analysis_date: date,
    hla_class: int,
) -> list[Path]:
    """
    Возвращает все source_files для конкретного класса исследования.

    Поддерживает гибридную базу:
      - новые файлы вида ...__1_класс.csv
      - legacy-файлы вида ...__1_класс.jpg
      - любые другие исторические расширения по тому же шаблону имени
    """
    if not src_dir.exists() or not src_dir.is_dir():
        return []

    pattern = (
        f"{organ}__{patient_folder_name}__"
        f"{format_ddmmyyyy(analysis_date)}__{hla_class}_класс.*"
    )
    result = [path for path in src_dir.glob(pattern) if path.is_file()]
    result.sort(key=lambda path: path.name.lower())
    return result


def list_source_files_for_patient(
    src_dir: Path,
    organ: str,
    patient_folder_name: str,
) -> list[Path]:
    """
    Возвращает все файлы пациента в source_files:
      ОРГАН__ИМЯ_ПАПКИ__...
    """
    if not src_dir.exists() or not src_dir.is_dir():
        return []

    prefix = f"{organ}__{patient_folder_name}__"

    result = [p for p in src_dir.iterdir() if p.is_file() and p.name.startswith(prefix)]
    result.sort(key=lambda p: p.name.lower())
    return result


def save_source_csv(
    src_dir: Path,
    organ: str,
    patient_folder_name: str,
    analysis_date: date,
    hla_class: int,
    csv_path: Path,
) -> Path:
    dst = build_source_csv_path(
        src_dir=src_dir,
        organ=organ,
        patient_folder_name=patient_folder_name,
        analysis_date=analysis_date,
        hla_class=hla_class,
    )

    src_dir.mkdir(parents=True, exist_ok=True)

    tmp_dst = src_dir / f".tmp__{dst.name}"
    if tmp_dst.exists():
        delete_file(tmp_dst)

    copy_file(csv_path, tmp_dst)

    try:
        replace_file(tmp_dst, dst)
    except Exception:
        try:
            if tmp_dst.exists():
                delete_file(tmp_dst)
        except Exception:
            pass
        raise

    return dst


# --- Переименование пациента и удаление существующих результатов ---
def rename_patient_folder(old_dir: Path, new_name: str) -> Path:
    new_dir = old_dir.parent / new_name
    if new_dir.exists():
        raise FileExistsError(f"Папка {new_dir} уже существует")
    return _rename_path_with_retry(old_dir, new_dir)


def delete_source_class_files(
    src_dir: Path,
    organ: str,
    patient_folder_name: str,
    analysis_date: date,
    hla_class: int,
) -> list[Path]:
    deleted: list[Path] = []

    for path in list_source_class_files(
        src_dir=src_dir,
        organ=organ,
        patient_folder_name=patient_folder_name,
        analysis_date=analysis_date,
        hla_class=hla_class,
    ):
        _unlink_with_retry(path)
        deleted.append(path)

    return deleted


def delete_patient_test_files(
    patient_dir: Path,
    test_date: date,
    classes: list[int],
) -> tuple[list[Path], bool]:
    """
    Удаляет только выбранные class-xlsx из папки исследования.

    Возвращает:
      - список удалённых путей
      - bool: была ли удалена папка даты целиком
    """
    deleted: list[Path] = []
    test_dir = patient_dir / format_ddmmyyyy(test_date)

    if not test_dir.exists() or not test_dir.is_dir():
        return deleted, False

    classes = sorted(set(classes))

    for hla_class in classes:
        xlsx_path = test_dir / class_result_file_name(hla_class)
        if xlsx_path.exists() and xlsx_path.is_file():
            _unlink_with_retry(xlsx_path)
            deleted.append(xlsx_path)

    try:
        remaining_managed = list_existing_study_files(test_dir)
    except FileNotFoundError:
        return deleted, True

    if remaining_managed:
        return deleted, False

    try:
        if not any(test_dir.iterdir()):
            _rmdir_with_retry(test_dir)
            deleted.append(test_dir)
            return deleted, True
    except FileNotFoundError:
        return deleted, True

    return deleted, False


def delete_patient_test_dir_tree(
    patient_dir: Path,
    test_date: date,
) -> tuple[list[Path], bool]:
    """
    Полностью удаляет папку исследования за дату со всем содержимым.

    Возвращает:
      - список путей, которые находились внутри папки (для отчётности)
      - bool: была ли удалена папка даты
    """
    deleted: list[Path] = []
    test_dir = patient_dir / format_ddmmyyyy(test_date)

    if not test_dir.exists() or not test_dir.is_dir():
        return deleted, False

    for p in sorted(
        test_dir.rglob("*"),
        key=lambda x: (len(x.relative_to(test_dir).parts), str(x).lower()),
        reverse=True,
    ):
        deleted.append(p)

    _rmtree_with_retry(test_dir)
    deleted.append(test_dir)
    return deleted, True


def rename_source_files_for_patient(
    src_dir: Path,
    organ: str,
    old_patient_folder_name: str,
    new_patient_folder_name: str,
) -> list[Path]:
    """
    Переименовывает все файлы пациента в source_files:
      ОРГАН__СТАРАЯ_ПАПКА__...
      -> ОРГАН__НОВАЯ_ПАПКА__...

    Хвост имени (дата исследования, класс, расширение и т.д.) сохраняется как есть.
    Возвращает список новых путей.
    """
    if old_patient_folder_name == new_patient_folder_name:
        return []

    if not src_dir.exists():
        return []

    old_prefix = f"{organ}__{old_patient_folder_name}__"
    new_prefix = f"{organ}__{new_patient_folder_name}__"

    # old_path, tmp_path, new_path
    matches: list[tuple[Path, Path, Path]] = []

    for p in src_dir.iterdir():
        if not p.is_file():
            continue
        if not p.name.startswith(old_prefix):
            continue

        tail = p.name[len(old_prefix) :]
        new_path = src_dir / f"{new_prefix}{tail}"
        tmp_path = src_dir / f".rename_tmp__{uuid4().hex}__{p.name}"
        matches.append((p, tmp_path, new_path))

    if not matches:
        return []

    # Этап 1: уводим исходные файлы во временные имена.
    for old_path, tmp_path, _new_path in matches:
        _rename_path_with_retry(old_path, tmp_path)

    renamed_to_final: list[tuple[Path, Path]] = []
    try:
        # Этап 2: переносим файлы на финальные имена.
        for _old_path, tmp_path, new_path in matches:
            if new_path.exists():
                raise FileExistsError(
                    f"Файл уже существует и мешает переименованию: {new_path.name}"
                )
            _rename_path_with_retry(tmp_path, new_path)
            renamed_to_final.append((new_path, tmp_path))

    except Exception:
        # Откат этапа 2: возвращаем финальные имена во временные.
        for new_path, tmp_path in reversed(renamed_to_final):
            if new_path.exists():
                _rename_path_with_retry(new_path, tmp_path)

        # Откат этапа 1: возвращаем временные имена в исходные.
        for old_path, tmp_path, _new_path in reversed(matches):
            if tmp_path.exists():
                _rename_path_with_retry(tmp_path, old_path)

        raise

    return [new_path for _old_path, _tmp_path, new_path in matches]


# --- Сохранение и удаление файлов заключения ---
def save_conclusion_file(
    patient_dir: Path,
    test_date: date,
    source_path: Path,
    *,
    negative_screening: bool,
) -> Path:
    test_dir = ensure_test_date_folder(patient_dir, test_date)
    dst_name = conclusion_file_name(
        negative_screening=negative_screening,
        suffix=source_path.suffix,
    )
    dst = test_dir / dst_name

    tmp_dst = test_dir / f".tmp__{dst_name}"
    if tmp_dst.exists():
        delete_file(tmp_dst)

    copy_file(source_path, tmp_dst)

    try:
        replace_file(tmp_dst, dst)
    except Exception:
        try:
            if tmp_dst.exists():
                delete_file(tmp_dst)
        except Exception:
            pass
        raise

    return dst


def delete_conclusion_files(
    patient_dir: Path,
    test_date: date,
) -> tuple[list[Path], bool]:
    deleted: list[Path] = []
    test_dir = patient_dir / format_ddmmyyyy(test_date)

    if not test_dir.exists() or not test_dir.is_dir():
        return deleted, False

    for target_name in CONCLUSION_FILE_NAMES:
        target = test_dir / target_name
        if target.exists() and target.is_file():
            _unlink_with_retry(target)
            deleted.append(target)

    try:
        remaining_managed = list_existing_study_files(test_dir)
    except FileNotFoundError:
        return deleted, True

    if remaining_managed:
        return deleted, False

    try:
        if not any(test_dir.iterdir()):
            _rmdir_with_retry(test_dir)
            deleted.append(test_dir)
            return deleted, True
    except FileNotFoundError:
        return deleted, True

    return deleted, False
