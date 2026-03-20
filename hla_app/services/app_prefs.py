from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings

from hla_app.__about__ import __author__, __title__
from hla_app.config.settings import (
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    DEFAULT_CONCLUSION_PLACE,
    DEFAULT_CONCLUSION_SAVE_DIR,
    DEFAULT_DIALOG_DIR,
    DEFAULT_SUM_SAVE_DIR,
    LIMIT_ROOT_EXPLORER_TO_ORGANS,
    ROOT_DIR,
)

_ORG_NAME = __author__
_APP_NAME = __title__

_KEY_ROOT_DIR = "paths/root_dir"
_KEY_DIALOG_DIR = "paths/default_dialog_dir"
_KEY_JPG_DIALOG_DIR = "paths/default_jpg_dialog_dir"
_KEY_CONCLUSION_SAVE_DIR = "paths/default_conclusion_save_dir"
_KEY_SUM_SAVE_DIR = "paths/default_sum_save_dir"
_KEY_CONCLUSION_PLACE = "conclusion/default_place"
_KEY_EXPLORER_VISIBLE = "ui/explorer_visible"
_KEY_LIMIT_ROOT_EXPLORER_TO_ORGANS = "ui/limit_root_explorer_to_organs"
_KEY_DB_USER = "db/user"
_KEY_DB_PASSWORD = "db/password"
_KEY_DB_HOST = "db/host"
_KEY_DB_PORT = "db/port"
_KEY_DB_NAME = "db/name"

_ALLOWED_CONCLUSION_PLACES = {"mnpc", "rnpc"}


@dataclass(frozen=True)
class PathPreferences:
    dialog_dir: Path | None
    conclusion_save_dir: Path | None
    sum_save_dir: Path | None


@dataclass(frozen=True)
class AppPreferences:
    root_dir: Path | None
    dialog_dir: Path | None
    jpg_dialog_dir: Path | None
    conclusion_save_dir: Path | None
    sum_save_dir: Path | None
    conclusion_place: str | None
    explorer_visible: bool
    limit_root_explorer_to_organs: bool | None
    db_user: str | None
    db_password: str | None
    db_host: str | None
    db_port: int | None
    db_name: str | None


def _settings() -> QSettings:
    return QSettings(_ORG_NAME, _APP_NAME)


def _read_bool(settings: QSettings, key: str, default: bool = False) -> bool:
    raw = settings.value(key, defaultValue=default)
    if isinstance(raw, bool):
        return raw

    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _read_optional_bool(settings: QSettings, key: str) -> bool | None:
    raw = settings.value(key, None)

    if raw is None:
        return None

    if isinstance(raw, bool):
        return raw

    text = str(raw).strip().lower()
    if not text:
        return None

    if text in {"1", "true", "yes", "on"}:
        return True

    if text in {"0", "false", "no", "off"}:
        return False

    return None


def _read_optional_path(settings: QSettings, key: str) -> Path | None:
    raw = settings.value(key, "", type=str)
    text = (raw or "").strip()
    return Path(text) if text else None


def _read_optional_str(settings: QSettings, key: str) -> str | None:
    raw = settings.value(key, "", type=str)
    text = (raw or "").strip()
    return text or None


def _read_optional_int(settings: QSettings, key: str) -> int | None:
    raw = settings.value(key, "")
    text = str(raw).strip()
    if not text:
        return None

    try:
        return int(text)
    except Exception:
        return None


def _read_optional_conclusion_place(settings: QSettings, key: str) -> str | None:
    raw = settings.value(key, "", type=str)
    text = (raw or "").strip()
    if not text or text not in _ALLOWED_CONCLUSION_PLACES:
        return None
    return text


def _write_optional_path(
    settings: QSettings,
    key: str,
    value: Path | str | None,
) -> None:
    if value is None:
        settings.remove(key)
        return

    text = str(value).strip()
    if not text:
        settings.remove(key)
        return

    settings.setValue(key, text)


def _write_optional_str(
    settings: QSettings,
    key: str,
    value: str | None,
) -> None:
    text = (value or "").strip()
    if not text:
        settings.remove(key)
        return

    settings.setValue(key, text)


def _write_optional_int(
    settings: QSettings,
    key: str,
    value: int | str | None,
) -> None:
    if value is None:
        settings.remove(key)
        return

    text = str(value).strip()
    if not text:
        settings.remove(key)
        return

    settings.setValue(key, int(text))


def _write_optional_bool(
    settings: QSettings,
    key: str,
    value: bool | None,
) -> None:
    if value is None:
        settings.remove(key)
        return

    settings.setValue(key, bool(value))


def _write_optional_conclusion_place(
    settings: QSettings,
    key: str,
    value: str | None,
) -> None:
    text = (value or "").strip()
    if not text:
        settings.remove(key)
        return

    if text not in _ALLOWED_CONCLUSION_PLACES:
        raise ValueError(
            "conclusion_place должен быть 'mnpc', 'rnpc' или пустым значением."
        )

    settings.setValue(key, text)


def load_user_app_preferences() -> AppPreferences:
    settings = _settings()
    return AppPreferences(
        root_dir=_read_optional_path(settings, _KEY_ROOT_DIR),
        dialog_dir=_read_optional_path(settings, _KEY_DIALOG_DIR),
        jpg_dialog_dir=_read_optional_path(settings, _KEY_JPG_DIALOG_DIR),
        conclusion_save_dir=_read_optional_path(settings, _KEY_CONCLUSION_SAVE_DIR),
        sum_save_dir=_read_optional_path(settings, _KEY_SUM_SAVE_DIR),
        conclusion_place=_read_optional_conclusion_place(
            settings,
            _KEY_CONCLUSION_PLACE,
        ),
        explorer_visible=_read_bool(settings, _KEY_EXPLORER_VISIBLE, False),
        limit_root_explorer_to_organs=_read_optional_bool(
            settings,
            _KEY_LIMIT_ROOT_EXPLORER_TO_ORGANS,
        ),
        db_user=_read_optional_str(settings, _KEY_DB_USER),
        db_password=_read_optional_str(settings, _KEY_DB_PASSWORD),
        db_host=_read_optional_str(settings, _KEY_DB_HOST),
        db_port=_read_optional_int(settings, _KEY_DB_PORT),
        db_name=_read_optional_str(settings, _KEY_DB_NAME),
    )


def load_effective_app_preferences() -> AppPreferences:
    user = load_user_app_preferences()
    return AppPreferences(
        root_dir=user.root_dir if user.root_dir is not None else ROOT_DIR,
        dialog_dir=user.dialog_dir
        if user.dialog_dir is not None
        else DEFAULT_DIALOG_DIR,
        jpg_dialog_dir=user.jpg_dialog_dir
        if user.jpg_dialog_dir is not None
        else DEFAULT_DIALOG_DIR,
        conclusion_save_dir=(
            user.conclusion_save_dir
            if user.conclusion_save_dir is not None
            else DEFAULT_CONCLUSION_SAVE_DIR
        ),
        sum_save_dir=user.sum_save_dir
        if user.sum_save_dir is not None
        else DEFAULT_SUM_SAVE_DIR,
        conclusion_place=(
            user.conclusion_place
            if user.conclusion_place is not None
            else DEFAULT_CONCLUSION_PLACE
        ),
        explorer_visible=user.explorer_visible,
        limit_root_explorer_to_organs=(
            user.limit_root_explorer_to_organs
            if user.limit_root_explorer_to_organs is not None
            else LIMIT_ROOT_EXPLORER_TO_ORGANS
        ),
        db_user=user.db_user if user.db_user is not None else DB_USER,
        db_password=user.db_password if user.db_password is not None else DB_PASSWORD,
        db_host=user.db_host if user.db_host is not None else DB_HOST,
        db_port=user.db_port if user.db_port is not None else DB_PORT,
        db_name=user.db_name if user.db_name is not None else DB_NAME,
    )


def save_user_app_preferences(
    *,
    root_dir: Path | str | None,
    dialog_dir: Path | str | None,
    jpg_dialog_dir: Path | str | None,
    conclusion_save_dir: Path | str | None,
    sum_save_dir: Path | str | None,
    conclusion_place: str | None,
    limit_root_explorer_to_organs: bool | None,
    db_user: str | None,
    db_password: str | None,
    db_host: str | None,
    db_port: int | str | None,
    db_name: str | None,
    explorer_visible: bool | None = None,
) -> None:
    settings = _settings()

    _write_optional_path(settings, _KEY_ROOT_DIR, root_dir)
    _write_optional_path(settings, _KEY_DIALOG_DIR, dialog_dir)
    _write_optional_path(settings, _KEY_JPG_DIALOG_DIR, jpg_dialog_dir)
    _write_optional_path(settings, _KEY_CONCLUSION_SAVE_DIR, conclusion_save_dir)
    _write_optional_path(settings, _KEY_SUM_SAVE_DIR, sum_save_dir)
    _write_optional_conclusion_place(
        settings,
        _KEY_CONCLUSION_PLACE,
        conclusion_place,
    )
    _write_optional_bool(
        settings,
        _KEY_LIMIT_ROOT_EXPLORER_TO_ORGANS,
        limit_root_explorer_to_organs,
    )

    _write_optional_str(settings, _KEY_DB_USER, db_user)
    _write_optional_str(settings, _KEY_DB_PASSWORD, db_password)
    _write_optional_str(settings, _KEY_DB_HOST, db_host)
    _write_optional_int(settings, _KEY_DB_PORT, db_port)
    _write_optional_str(settings, _KEY_DB_NAME, db_name)

    if explorer_visible is not None:
        settings.setValue(_KEY_EXPLORER_VISIBLE, bool(explorer_visible))

    settings.sync()


def save_explorer_visibility_preference(visible: bool) -> None:
    """Сохраняет только состояние панели проводника."""
    settings = _settings()
    settings.setValue(_KEY_EXPLORER_VISIBLE, bool(visible))
    settings.sync()


def load_effective_path_preferences() -> PathPreferences:
    prefs = load_effective_app_preferences()
    return PathPreferences(
        dialog_dir=prefs.dialog_dir,
        conclusion_save_dir=prefs.conclusion_save_dir,
        sum_save_dir=prefs.sum_save_dir,
    )
