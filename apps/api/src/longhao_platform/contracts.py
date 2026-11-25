"""Canonical event registry and runtime configuration."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator, FormatChecker

from longhao_platform.types import PlatformError


ROOT = Path(__file__).resolve().parents[4]


def _flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PlatformError(500, "INVALID_CONFIGURATION", "A boolean configuration value is invalid.")


@dataclass(frozen=True, slots=True)
class PlatformConfig:
    environment: str = "development"
    bind_host: str = "127.0.0.1"
    port: int = 8080
    synthetic_providers: bool = True
    allow_missing_modules: bool = True
    manifest_path: Path = ROOT / "project/module-manifest.json"
    event_registry_path: Path = ROOT / "events/registry.json"
    admin_root: Path = ROOT / "apps/admin/dist"

    def __post_init__(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise PlatformError(500, "INVALID_CONFIGURATION", "LONGHAO_ENV must be development, test or production.")
        if not 1 <= self.port <= 65535:
            raise PlatformError(500, "INVALID_CONFIGURATION", "Port is outside the valid range.")
        if self.environment == "production" and self.synthetic_providers:
            raise PlatformError(500, "SYNTHETIC_PROVIDER_FORBIDDEN", "Synthetic providers cannot be enabled in production.")
        if self.environment == "production" and self.allow_missing_modules:
            raise PlatformError(500, "MISSING_MODULES_FORBIDDEN", "Production cannot allow missing modules.")

    @classmethod
    def from_env(cls) -> "PlatformConfig":
        environment = os.getenv("LONGHAO_ENV", "development").strip().lower()
        try:
            port = int(os.getenv("LONGHAO_PORT", "8080"))
        except ValueError as exc:
            raise PlatformError(500, "INVALID_CONFIGURATION", "LONGHAO_PORT must be an integer.") from exc
        return cls(
            environment=environment,
            bind_host=os.getenv("LONGHAO_BIND_HOST", "127.0.0.1"),
            port=port,
            synthetic_providers=_flag(os.getenv("LONGHAO_SYNTHETIC_PROVIDERS"), environment != "production"),
            allow_missing_modules=_flag(os.getenv("LONGHAO_ALLOW_MISSING_MODULES"), environment != "production"),
            manifest_path=Path(os.getenv("LONGHAO_MODULE_MANIFEST", str(ROOT / "project/module-manifest.json"))),
            event_registry_path=Path(os.getenv("LONGHAO_EVENT_REGISTRY", str(ROOT / "events/registry.json"))),
            admin_root=Path(os.getenv("LONGHAO_ADMIN_ROOT", str(ROOT / "apps/admin/dist"))),
        )


@dataclass(frozen=True, slots=True)
class EventDefinition:
    event_type: str
    schema_version: str
    producer: str
    consumers: tuple[str, ...]
    aggregate_type: str
    classification: str
    schema_path: Path


class EventCatalog:
    def __init__(self, definitions: Iterable[EventDefinition]):
        rows = tuple(definitions)
        self._definitions = {row.event_type: row for row in rows}
        if len(self._definitions) != len(rows):
            raise PlatformError(500, "DUPLICATE_EVENT_CONTRACT", "Event types must be unique.")
        self._validators: dict[str, Draft202012Validator] = {}

    @classmethod
    def load(cls, path: Path) -> "EventCatalog":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                EventDefinition(
                    event_type=row["event_type"],
                    schema_version=row["schema_version"],
                    producer=row["producer"],
                    consumers=tuple(row["consumers"]),
                    aggregate_type=row["aggregate_type"],
                    classification=row["classification"],
                    schema_path=path.parent / row["schema"],
                )
                for row in raw
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PlatformError(500, "EVENT_REGISTRY_INVALID", "The canonical event registry cannot be loaded.") from exc

    def require(self, event_type: str, *, producer: str, aggregate_type: str) -> EventDefinition:
        definition = self._definitions.get(event_type)
        if definition is None:
            raise PlatformError(500, "EVENT_CONTRACT_NOT_REGISTERED", event_type)
        if definition.producer != producer:
            raise PlatformError(500, "EVENT_PRODUCER_MISMATCH", event_type)
        if definition.aggregate_type != aggregate_type:
            raise PlatformError(500, "EVENT_AGGREGATE_MISMATCH", event_type)
        return definition

    def get(self, event_type: str) -> EventDefinition | None:
        return self._definitions.get(event_type)

    def validate(self, envelope: dict[str, object]) -> None:
        event_type = str(envelope.get("event_type", ""))
        definition = self._definitions.get(event_type)
        if definition is None:
            raise PlatformError(500, "EVENT_CONTRACT_NOT_REGISTERED", event_type)
        validator = self._validators.get(event_type)
        if validator is None:
            try:
                schema = json.loads(definition.schema_path.read_text(encoding="utf-8"))
                validator = Draft202012Validator(schema, format_checker=FormatChecker())
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise PlatformError(500, "EVENT_SCHEMA_INVALID", event_type) from exc
            self._validators[event_type] = validator
        errors = sorted(validator.iter_errors(envelope), key=lambda error: list(error.absolute_path))
        if errors:
            locations = ["/".join(str(part) for part in error.absolute_path) or "$" for error in errors[:3]]
            raise PlatformError(500, "EVENT_SCHEMA_VALIDATION_FAILED", f"{event_type}: {', '.join(locations)}")
        if event_type == "report.signature_verified":
            payload = envelope.get("payload")
            if not isinstance(payload, dict) or payload.get("report_version_id") != envelope.get("aggregate_id"):
                raise PlatformError(500, "SIGNATURE_EVENT_AGGREGATE_MISMATCH")

    def __len__(self) -> int:
        return len(self._definitions)
