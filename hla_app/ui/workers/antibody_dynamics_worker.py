"""Фоновые QRunnable-задачи для окна динамики антител.

Здесь вынесены две асинхронные операции:
- загрузка динамики пациента;
- probe дополнительной БД из popup-настроек.
Сигналы несут request_id, чтобы UI мог игнорировать устаревшие результаты
после повторной загрузки или быстрого переключения пациента.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from hla_app.db.engine import probe_db_settings
from hla_app.services.antibody_dynamics_service import load_patient_dynamics_raw


# --- Фоновая загрузка динамики пациента и probe дополнительной БД ---
class AntibodyDynamicsLoadSignals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


class AntibodyDynamicsLoadTask(QRunnable):
    def __init__(self, *, request_id: int, patient_code: str, secondary_config):
        super().__init__()
        self.request_id = request_id
        self.patient_code = patient_code
        self.secondary_config = secondary_config
        self.signals = AntibodyDynamicsLoadSignals()

    def run(self) -> None:
        try:
            payload = load_patient_dynamics_raw(
                self.patient_code,
                self.secondary_config,
            )
            try:
                # request_id позволяет окну отбросить устаревший результат, если
                # пользователь уже успел открыть другого пациента.
                self.signals.finished.emit(self.request_id, payload)
            except RuntimeError:
                return
        except Exception as exc:
            try:
                self.signals.failed.emit(self.request_id, str(exc))
            except RuntimeError:
                return


class SecondaryDbProbeSignals(QObject):
    finished = Signal(int, bool)


class SecondaryDbProbeTask(QRunnable):
    def __init__(self, *, request_id: int, config):
        super().__init__()
        self.request_id = request_id
        self.config = config
        self.signals = SecondaryDbProbeSignals()

    def run(self) -> None:
        try:
            # Probe выполняется отдельно от сохранения: popup не должен
            # становиться "несохраняемым" только из-за недоступной optional DB.
            probe_db_settings(
                db_user=self.config.db_user,
                db_password=self.config.db_password,
                db_host=self.config.db_host,
                db_port=self.config.db_port,
                db_name=self.config.db_name,
            )
            try:
                self.signals.finished.emit(
                    self.request_id,
                    True,
                )
            except RuntimeError:
                return
        except Exception:
            try:
                self.signals.finished.emit(
                    self.request_id,
                    False,
                )
            except RuntimeError:
                return
