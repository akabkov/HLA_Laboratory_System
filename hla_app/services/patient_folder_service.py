"""Поиск и создание папок пациентов в файловой базе.

Здесь описаны модели вариантов совпадения пациента, поиск существующих папок
в старом и новом формате, сортировка совпадений и создание новой папки вместе
с `patient_code` для БД. Если проблема связана с выбором пациента или именем
его каталога, начинать проверку удобно с этого файла.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hla_app.storage.fs_ops import (
    MatchCandidate,
    ensure_patient_folder_full,
    find_matches_by_fullname_base,
    find_matches_by_lastnames_initials,
    list_patient_folders,
    parse_patient_folder_record,
    split_patient_folder_name,
)
from hla_app.utils.validators import (
    initials,
    normalize_for_compare,
    normalize_for_match,
    patient_code,
    patient_dir_name,
    patient_dir_name_full,
)

_DUPLICATE_SUFFIX_RE = re.compile(r"^(?P<base>.*?)(?:__(?P<num>\d+))?$")


# --- Модели результата поиска и вариантов уже существующих папок ---
@dataclass(frozen=True)
class ExistingPatientOption:
    label: str
    folder_name: str
    is_new_format: bool


@dataclass(frozen=True)
class PatientFolderSearchResult:
    desired_patient_name: str
    exact_match_folder: Path | None
    options: list[ExistingPatientOption]


# --- Вспомогательные функции сортировки и поиска совпадений по имени ---
def _folder_name_sort_key(folder_name: str) -> tuple[str, int, int, str]:
    """
    Естественная сортировка имён папок пациента:

    Иванов_А.А_01.01.2000_ж
    Иванов_А.А_01.01.2000_ж__2
    Иванов_А.А_01.01.2000_ж__10

    Порядок:
      1) базовое имя без дубль-суффикса
      2) папка без __N идёт раньше дублей
      3) дубли сортируются по числу, а не строково
    """
    text = (folder_name or "").strip()
    match = _DUPLICATE_SUFFIX_RE.match(text)

    if match is None:
        return (text.lower(), 0, 0, text.lower())

    base = (match.group("base") or "").lower()
    num_text = match.group("num")

    has_duplicate_suffix = 1 if num_text else 0
    duplicate_index = int(num_text) if num_text else 0

    return (base, has_duplicate_suffix, duplicate_index, text.lower())


def find_new_format_matches_by_name_birth(
    *,
    organ_dir: Path,
    target_lastnames: list[str],
    target_first_name: str,
    target_birth_date: date,
    target_sex: str | None,
) -> list[MatchCandidate]:
    """
    Дополнительный поиск по папкам НОВОГО формата для случая,
    когда пользователь не ввёл отчество.

    Совпадением считаем:
      - фамилию или новую фамилию;
      - точное совпадение имени (с нормализацией регистра и Ё/Е);
      - точную дату рождения;
      - пол, если он указан в папке.

    Такой поиск не делает автопривязку, а только добавляет
    найденные папки в список вариантов для выбора пользователем.
    """
    want_lastnames = {
        normalize_for_match(value, strict_first_char=True)
        for value in target_lastnames
        if value
    }
    want_first_name = normalize_for_compare(target_first_name)

    if not want_lastnames or not want_first_name:
        return []

    result: list[MatchCandidate] = []

    for folder in list_patient_folders(organ_dir):
        parsed = parse_patient_folder_record(folder)
        if parsed is None:
            continue

        if not parsed.is_new_format or parsed.birth_date is None:
            continue

        if parsed.birth_date != target_birth_date:
            continue

        # Если пол указан в имени папки, требуем совпадение.
        # Если в папке пола нет, не отбрасываем такой вариант.
        if target_sex and parsed.sex and parsed.sex != target_sex:
            continue

        folder_lastnames = {
            normalize_for_match(value, strict_first_char=True)
            for value in (parsed.last_name, parsed.new_last_name)
            if value
        }
        if not (folder_lastnames & want_lastnames):
            continue

        if normalize_for_compare(parsed.first_name) != want_first_name:
            continue

        result.append(MatchCandidate(folder=folder, display=folder.name))

    return result


# --- Основной поиск совпадений пациента в старом и новом формате ---
def build_patient_folder_search(
    *,
    organ_dir: Path,
    last_name: str,
    new_last_name: str,
    first_name: str,
    middle_name: str,
    birth_date: date,
    sex: str | None,
) -> PatientFolderSearchResult:
    base_last = last_name if last_name else new_last_name
    bracket_last = new_last_name if last_name else ""

    desired_patient_name = patient_dir_name_full(
        base_last,
        bracket_last,
        first_name,
        middle_name,
        birth_date,
        sex=sex,
    )
    desired_base_name = patient_dir_name(
        base_last,
        bracket_last,
        first_name,
        middle_name,
    )

    exact_match_folder: Path | None = None
    duplicate_full_matches: list[Path] = []

    if organ_dir.exists():
        for p in organ_dir.iterdir():
            if not p.is_dir() or p.name == "source_files":
                continue

            if p.name == desired_patient_name:
                exact_match_folder = p
            else:
                match = _DUPLICATE_SUFFIX_RE.match(p.name)
                if (
                    match is not None
                    and (match.group("base") or "") == desired_patient_name
                    and match.group("num") is not None
                ):
                    duplicate_full_matches.append(p)

    # Автоматически выбираем ТОЛЬКО настоящую точную папку.
    # Если есть только дубли вида "__2", "__3", "__10",
    # обязательно отдаём их пользователю на явный выбор.
    if exact_match_folder is not None:
        return PatientFolderSearchResult(
            desired_patient_name=desired_patient_name,
            exact_match_folder=exact_match_folder,
            options=[],
        )

    duplicate_full_matches.sort(key=lambda p: _folder_name_sort_key(p.name))

    target_lastnames: list[str] = []
    if last_name:
        target_lastnames.append(last_name)
    if new_last_name:
        target_lastnames.append(new_last_name)

    target_init = initials(first_name, middle_name)

    full_base_matches = find_matches_by_fullname_base(
        organ_dir=organ_dir,
        target_base_name=desired_base_name,
    )

    # Дополнительный поиск по папкам НОВОГО формата,
    # когда отчество не заполнено:
    # фамилия(и) + имя + дата рождения (+ пол, если он указан).
    new_format_name_birth_matches: list[MatchCandidate] = []
    if not (middle_name or "").strip():
        new_format_name_birth_matches = find_new_format_matches_by_name_birth(
            organ_dir=organ_dir,
            target_lastnames=target_lastnames,
            target_first_name=first_name,
            target_birth_date=birth_date,
            target_sex=sex,
        )

    new_format_initial_matches = find_matches_by_lastnames_initials(
        organ_dir=organ_dir,
        target_lastnames=target_lastnames,
        target_initials=target_init,
        initials_mode="full",
    )
    new_format_matches = [
        m
        for m in new_format_initial_matches
        if split_patient_folder_name(m.display)[2] is not None
    ]

    legacy_initial_matches = find_matches_by_lastnames_initials(
        organ_dir=organ_dir,
        target_lastnames=target_lastnames,
        target_initials=target_init,
        initials_mode="first_only",
    )
    legacy_matches = [
        m
        for m in legacy_initial_matches
        if split_patient_folder_name(m.display)[2] is None
    ]

    full_base_matches = sorted(
        full_base_matches,
        key=lambda m: _folder_name_sort_key(m.display),
    )
    new_format_name_birth_matches = sorted(
        new_format_name_birth_matches,
        key=lambda m: _folder_name_sort_key(m.display),
    )
    new_format_matches = sorted(
        new_format_matches,
        key=lambda m: _folder_name_sort_key(m.display),
    )
    legacy_matches = sorted(
        legacy_matches,
        key=lambda m: _folder_name_sort_key(m.display),
    )

    options: list[ExistingPatientOption] = []
    seen_folder_names: set[str] = set()

    # Сначала показываем самые опасные случаи:
    # полные дубли точного имени с суффиксами __N.
    for p in duplicate_full_matches:
        if p.name in seen_folder_names:
            continue
        seen_folder_names.add(p.name)
        options.append(
            ExistingPatientOption(
                label=f"Новый формат (дубль): {p.name}",
                folder_name=p.name,
                is_new_format=True,
            )
        )

    # Самое сильное совпадение по новому формату:
    # полное совпадение ФИО-части (без даты рождения и пола).
    for m in full_base_matches:
        if m.display in seen_folder_names:
            continue
        seen_folder_names.add(m.display)
        options.append(
            ExistingPatientOption(
                label=f"Новый формат: {m.display}",
                folder_name=m.display,
                is_new_format=True,
            )
        )

    # Дополнительный сильный fallback для случая, когда отчество не введено:
    # фамилия(и) + имя + дата рождения (+ пол, если он указан).
    for m in new_format_name_birth_matches:
        if m.display in seen_folder_names:
            continue
        seen_folder_names.add(m.display)
        options.append(
            ExistingPatientOption(
                label=f"Новый формат (имя + ДР): {m.display}",
                folder_name=m.display,
                is_new_format=True,
            )
        )

    # Более широкий fallback по фамилии(ям) + полным инициалам.
    for m in new_format_matches:
        if m.display in seen_folder_names:
            continue
        seen_folder_names.add(m.display)
        options.append(
            ExistingPatientOption(
                label=f"Новый формат: {m.display}",
                folder_name=m.display,
                is_new_format=True,
            )
        )

    for m in legacy_matches:
        if m.display in seen_folder_names:
            continue
        seen_folder_names.add(m.display)
        options.append(
            ExistingPatientOption(
                label=f"Старый формат: {m.display}",
                folder_name=m.display,
                is_new_format=False,
            )
        )

    return PatientFolderSearchResult(
        desired_patient_name=desired_patient_name,
        exact_match_folder=None,
        options=options,
    )


# --- Создание новой папки пациента и сборка patient_code для БД ---
def create_new_patient_folder(
    *,
    organ_dir: Path,
    last_name: str,
    new_last_name: str,
    first_name: str,
    middle_name: str,
    birth_date: date,
    sex: str | None,
) -> Path:
    return ensure_patient_folder_full(
        organ_dir,
        last_name,
        new_last_name,
        first_name,
        middle_name,
        birth_date,
        sex=sex,
    )


def build_db_patient_code(organ: str, patient_dir_name: str) -> str:
    return patient_code(organ, patient_dir_name)
