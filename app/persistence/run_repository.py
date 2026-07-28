from __future__ import annotations

"""Dated, point-in-time storage for deterministic market pipeline runs."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.models.run_models import AnalysisStage, StageResult
from app.persistence.atomic_writer import atomic_write_json, atomic_write_text
from app.scheduling.job_lock import JobLock, lock_filename


STAGE_JSON_FILENAMES: dict[AnalysisStage, str] = {
    AnalysisStage.UNIVERSE_VALIDATION: "universe_validation.json",
    AnalysisStage.PREMARKET: "premarket.json",
    AnalysisStage.OPENING_5M: "opening_5m.json",
    AnalysisStage.OPENING_15M: "opening_15m.json",
}

STAGE_MARKDOWN_FILENAMES: dict[AnalysisStage, str] = {
    AnalysisStage.PREMARKET: "premarket.md",
    AnalysisStage.OPENING_5M: "opening_5m.md",
    # The user-facing 15-minute document is the final report by definition.
    AnalysisStage.OPENING_15M: "final_report.md",
}

TERMINAL_FAILURE_STATUSES = {"ERROR", "FAILED", "IN_PROGRESS", "PENDING", "RUNNING"}


class RunRepositoryError(RuntimeError):
    """Base class for persistence contract failures."""


class RepositoryNotInitializedError(RunRepositoryError):
    """Raised when a stage is saved before its run configuration snapshot."""


class DuplicateRunError(RunRepositoryError):
    """Raised when a logical run key has already reached a terminal state."""


class ConfigurationSnapshotMismatchError(RunRepositoryError):
    """Raised rather than silently mixing configurations in a dated run."""


class InvalidRunDataError(RunRepositoryError, ValueError):
    """Raised for malformed dates, stages, or conflicting run metadata."""


class RunRepository:
    """Own the exact ``output/runs/YYYY-MM-DD`` persistence layout.

    ``strategy_version`` can be supplied once at construction or inferred from
    the configuration snapshot.  Logical idempotency is always evaluated with
    ``trade_date + stage + strategy_version``.
    """

    def __init__(self, root: str | Path, strategy_version: str | None = None) -> None:
        self.root = Path(root)
        self.strategy_version = strategy_version.strip() if strategy_version else None

    @staticmethod
    def logical_run_key(
        trade_date: str | date,
        stage: AnalysisStage | str,
        strategy_version: str,
    ) -> str:
        date_text = _date_text(trade_date)
        parsed_stage = _stage(stage)
        version = _required_text(strategy_version, "strategy_version")
        return f"{date_text}:{parsed_stage.value}:{version}"

    # Alias matching the model's terminology.
    run_key = logical_run_key

    def run_dir(self, trade_date: str | date) -> Path:
        return self.root / _date_text(trade_date)

    def manifest_path(self, trade_date: str | date) -> Path:
        return self.run_dir(trade_date) / "run_manifest.json"

    def config_snapshot_path(self, trade_date: str | date) -> Path:
        return self.run_dir(trade_date) / "config_snapshot.json"

    def stage_path(self, trade_date: str | date, stage: AnalysisStage | str) -> Path:
        return self.run_dir(trade_date) / STAGE_JSON_FILENAMES[_stage(stage)]

    def markdown_path(self, trade_date: str | date, stage: AnalysisStage | str) -> Path | None:
        filename = STAGE_MARKDOWN_FILENAMES.get(_stage(stage))
        return self.run_dir(trade_date) / filename if filename else None

    def final_report_path(self, trade_date: str | date, *, markdown: bool = False) -> Path:
        return self.run_dir(trade_date) / ("final_report.md" if markdown else "final_report.json")

    def lock_path(
        self,
        trade_date: str | date,
        stage: AnalysisStage | str,
        strategy_version: str | None = None,
    ) -> Path:
        version = self._resolve_version(strategy_version, trade_date=trade_date)
        key = self.logical_run_key(trade_date, stage, version)
        # Keep operational locks outside the dated artifact directory so the
        # requested YYYY-MM-DD file layout contains analysis artifacts only.
        return self.root / ".locks" / _date_text(trade_date) / lock_filename(key)

    def job_lock(
        self,
        trade_date: str | date,
        stage: AnalysisStage | str,
        strategy_version: str | None = None,
        *,
        blocking: bool = False,
    ) -> JobLock:
        return JobLock(self.lock_path(trade_date, stage, strategy_version), blocking=blocking)

    def initialize_run(
        self,
        trade_date: str | date,
        config: Mapping[str, Any],
        *,
        force: bool = False,
        initialized_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist the immutable point-in-time config and initialize a manifest.

        Repeated calls with the same configuration are harmless.  A changed
        snapshot requires ``force=True`` so a later stage cannot accidentally
        reinterpret earlier evidence under different rules.
        """

        date_text = _date_text(trade_date)
        config_value = deepcopy(dict(config))
        config_version = _required_text(config_value.get("strategy_version"), "config.strategy_version")
        if self.strategy_version is not None and config_version != self.strategy_version:
            raise ConfigurationSnapshotMismatchError(
                f"repository strategy version {self.strategy_version!r} does not match config {config_version!r}"
            )
        version = self.strategy_version or config_version
        timestamp = _timestamp(initialized_at)
        run_dir = self.run_dir(date_text)
        run_dir.mkdir(parents=True, exist_ok=True)

        with self._manifest_lock(date_text):
            config_path = self.config_snapshot_path(date_text)
            if config_path.exists():
                existing_config = _load_json_object(config_path)
                if existing_config != config_value and not force:
                    raise ConfigurationSnapshotMismatchError(
                        f"configuration snapshot already exists with different content for {date_text}"
                    )
            if force or not config_path.exists():
                atomic_write_json(config_path, config_value)

            manifest_path = self.manifest_path(date_text)
            if manifest_path.exists():
                manifest = _load_json_object(manifest_path)
                _validate_manifest_identity(manifest, date_text, version)
                manifest.setdefault("stages", {})
                if force:
                    manifest["config_snapshot_updated_at"] = timestamp
            else:
                manifest = {
                    "trade_date": date_text,
                    "strategy_version": version,
                    "config_snapshot": "config_snapshot.json",
                    "initialized_at": timestamp,
                    "stages": {},
                }
            atomic_write_json(manifest_path, manifest)
        return deepcopy(manifest)

    def read_manifest(self, trade_date: str | date) -> dict[str, Any]:
        path = self.manifest_path(trade_date)
        if not path.exists():
            raise RepositoryNotInitializedError(f"run manifest does not exist: {path}")
        return _load_json_object(path)

    load_manifest = read_manifest

    def load_config_snapshot(self, trade_date: str | date) -> dict[str, Any]:
        path = self.config_snapshot_path(trade_date)
        if not path.exists():
            raise RepositoryNotInitializedError(f"configuration snapshot does not exist: {path}")
        return _load_json_object(path)

    def save_stage(
        self,
        trade_date_or_result: str | date | StageResult | Mapping[str, Any],
        stage: AnalysisStage | str | None = None,
        result: StageResult | Mapping[str, Any] | None = None,
        *,
        markdown: str | None = None,
        final_report: Mapping[str, Any] | None = None,
        force: bool = False,
        strategy_version: str | None = None,
        persisted_at: datetime | None = None,
    ) -> Path:
        """Atomically persist a stage result and update the durable manifest.

        Supported call forms are ``save_stage(StageResult)``,
        ``save_stage(mapping_with_trade_date_and_stage)``, and
        ``save_stage(trade_date, stage, mapping)``.
        """

        date_text, parsed_stage, payload = _normalize_stage_arguments(trade_date_or_result, stage, result)
        version = self._resolve_version(
            strategy_version or _optional_text(payload.get("strategy_version")),
            trade_date=date_text,
        )
        key = self.logical_run_key(date_text, parsed_stage, version)
        _set_or_validate(payload, "trade_date", date_text)
        _set_or_validate(payload, "stage", parsed_stage.value)
        _set_or_validate(payload, "strategy_version", version)
        _set_or_validate(payload, "run_key", key)
        timestamp = _timestamp(persisted_at)
        destination = self.stage_path(date_text, parsed_stage)

        with self._manifest_lock(date_text):
            manifest = self.read_manifest(date_text)
            _validate_manifest_identity(manifest, date_text, version)
            stages = manifest.setdefault("stages", {})
            existing = stages.get(parsed_stage.value)
            if isinstance(existing, Mapping) and _manifest_entry_complete(existing) and not force:
                raise DuplicateRunError(f"logical run already completed: {key}")

            atomic_write_json(destination, payload)
            files = [destination.name]
            markdown_path = self.markdown_path(date_text, parsed_stage)
            if markdown is not None and markdown_path is not None:
                atomic_write_text(markdown_path, markdown.rstrip() + "\n")
                files.append(markdown_path.name)
            if final_report is not None:
                final_path = self.final_report_path(date_text)
                atomic_write_json(final_path, dict(final_report))
                files.append(final_path.name)

            previous_attempts = int(existing.get("attempts", 0)) if isinstance(existing, Mapping) else 0
            status = str(payload.get("status", "COMPLETED"))
            stages[parsed_stage.value] = {
                "run_key": key,
                "status": status,
                "files": files,
                "persisted_at": timestamp,
                "attempts": previous_attempts + 1,
                "forced": bool(force),
            }
            manifest["updated_at"] = timestamp
            atomic_write_json(self.manifest_path(date_text), manifest)
        return destination

    # Name used by some pipeline implementations.
    persist_stage = save_stage

    def save_final_report(
        self,
        trade_date: str | date,
        report: Mapping[str, Any],
        *,
        markdown: str | None = None,
    ) -> Path:
        """Persist final JSON/Markdown artifacts without changing stage truth."""

        date_text = _date_text(trade_date)
        if not self.manifest_path(date_text).exists():
            raise RepositoryNotInitializedError(f"run is not initialized: {date_text}")
        destination = self.final_report_path(date_text)
        atomic_write_json(destination, dict(report))
        if markdown is not None:
            atomic_write_text(self.final_report_path(date_text, markdown=True), markdown.rstrip() + "\n")
        return destination

    def load_stage(self, trade_date: str | date, stage: AnalysisStage | str) -> dict[str, Any]:
        path = self.stage_path(trade_date, stage)
        if not path.exists():
            raise FileNotFoundError(path)
        return _load_json_object(path)

    def has_stage(self, trade_date: str | date, stage: AnalysisStage | str) -> bool:
        return self.stage_path(trade_date, stage).is_file()

    def is_complete(
        self,
        trade_date: str | date,
        stage: AnalysisStage | str,
        strategy_version: str | None = None,
    ) -> bool:
        """Return durable terminal state for the exact logical run key."""

        date_text = _date_text(trade_date)
        parsed_stage = _stage(stage)
        try:
            version = self._resolve_version(strategy_version, trade_date=date_text)
        except RepositoryNotInitializedError:
            return False
        expected_key = self.logical_run_key(date_text, parsed_stage, version)
        try:
            manifest = self.read_manifest(date_text)
        except RepositoryNotInitializedError:
            return False
        entry = manifest.get("stages", {}).get(parsed_stage.value)
        if isinstance(entry, Mapping):
            return entry.get("run_key") == expected_key and _manifest_entry_complete(entry)

        # Recover cleanly from a crash after the atomic stage write but before
        # its manifest update.
        path = self.stage_path(date_text, parsed_stage)
        if not path.exists():
            return False
        try:
            payload = _load_json_object(path)
        except (OSError, json.JSONDecodeError, InvalidRunDataError):
            return False
        status = str(payload.get("status", "COMPLETED")).upper()
        return payload.get("run_key") == expected_key and status not in TERMINAL_FAILURE_STATUSES

    def _resolve_version(
        self,
        supplied: str | None,
        *,
        trade_date: str | date | None = None,
    ) -> str:
        if supplied:
            version = _required_text(supplied, "strategy_version")
            if self.strategy_version is not None and version != self.strategy_version:
                raise InvalidRunDataError("strategy_version conflicts with repository strategy_version")
            return version
        if self.strategy_version:
            return self.strategy_version
        if trade_date is not None and self.manifest_path(trade_date).exists():
            return _required_text(self.read_manifest(trade_date).get("strategy_version"), "manifest.strategy_version")
        raise RepositoryNotInitializedError("strategy_version is unavailable before initialize_run")

    def _manifest_lock(self, trade_date: str | date) -> JobLock:
        return JobLock(
            self.root / ".locks" / _date_text(trade_date) / "manifest.lock",
            blocking=True,
        )


def _normalize_stage_arguments(
    trade_date_or_result: str | date | StageResult | Mapping[str, Any],
    stage: AnalysisStage | str | None,
    result: StageResult | Mapping[str, Any] | None,
) -> tuple[str, AnalysisStage, dict[str, Any]]:
    if isinstance(trade_date_or_result, StageResult):
        if stage is not None or result is not None:
            raise TypeError("stage/result arguments are invalid when passing StageResult")
        payload = trade_date_or_result.to_dict()
        return trade_date_or_result.context.trade_date, trade_date_or_result.context.stage, payload
    if isinstance(trade_date_or_result, Mapping):
        if stage is not None or result is not None:
            raise TypeError("stage/result arguments are invalid when passing a result mapping")
        payload = deepcopy(dict(trade_date_or_result))
        return _date_text(payload.get("trade_date")), _stage(payload.get("stage")), payload

    if stage is None or result is None:
        raise TypeError("save_stage requires trade_date, stage, and result")
    if isinstance(result, StageResult):
        payload = result.to_dict()
    elif isinstance(result, Mapping):
        payload = deepcopy(dict(result))
    elif is_dataclass(result):
        payload = asdict(result)
    else:
        raise TypeError("result must be StageResult or a mapping")
    return _date_text(trade_date_or_result), _stage(stage), payload


def _date_text(value: str | date | Any) -> str:
    if isinstance(value, datetime):
        raise InvalidRunDataError("trade_date must not be a datetime")
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise InvalidRunDataError(f"invalid trade_date: {value!r}") from exc


def _stage(value: AnalysisStage | str | Any) -> AnalysisStage:
    if isinstance(value, AnalysisStage):
        return value
    try:
        return AnalysisStage.parse(str(value))
    except (ValueError, AttributeError) as exc:
        raise InvalidRunDataError(f"invalid analysis stage: {value!r}") from exc


def _required_text(value: Any, field: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise InvalidRunDataError(f"{field} must be a non-empty string")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _timestamp(value: datetime | None) -> str:
    instant = value or datetime.now(timezone.utc)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise InvalidRunDataError("persistence timestamps must be timezone-aware")
    return instant.isoformat()


def _load_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise InvalidRunDataError(f"expected JSON object: {path}")
    return value


def _set_or_validate(payload: dict[str, Any], key: str, value: str) -> None:
    existing = payload.get(key)
    if existing is not None and str(existing) != value:
        raise InvalidRunDataError(f"result {key} {existing!r} conflicts with {value!r}")
    payload[key] = value


def _validate_manifest_identity(manifest: Mapping[str, Any], trade_date: str, strategy_version: str) -> None:
    if manifest.get("trade_date") != trade_date:
        raise InvalidRunDataError("manifest trade_date does not match its directory")
    if manifest.get("strategy_version") != strategy_version:
        raise ConfigurationSnapshotMismatchError(
            "manifest strategy_version does not match the requested logical run"
        )


def _manifest_entry_complete(entry: Mapping[str, Any]) -> bool:
    status = str(entry.get("status", "COMPLETED")).upper()
    return status not in TERMINAL_FAILURE_STATUSES
