"""Dependency-free contracts for optional host-local Village facilities."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Availability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    BUSY = "busy"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    BUSY = "busy"
    REFUSED = "refused"

    @property
    def terminal(self) -> bool:
        return self not in {self.QUEUED, self.RUNNING}


class SelectionPolicy(StrEnum):
    EXACT = "exact"
    ONLY_AVAILABLE = "only_available"


class ReleaseScope(StrEnum):
    INTERNAL = "internal"
    DRAFT = "draft"
    PUBLIC = "public"


class VoiceIdentity(StrEnum):
    NONE = "none"
    PRESET = "preset"
    SYNTHETIC = "synthetic"
    CLONED_PERSON = "cloned-person"


@dataclass(frozen=True)
class ArtifactRef:
    """A content-addressed input or output; bytes stay outside the contract."""

    locator: str
    sha256: str
    media_type: str
    release_scope: ReleaseScope = ReleaseScope.INTERNAL
    reference_role: str = ""
    consent_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FacilitySpec:
    """One exact model/revision/backend arm registered on a capable host."""

    facility_id: str
    capability: str
    arm_id: str
    model_id: str
    model_revision: str
    backend: str
    device: str
    output_media_type: str
    input_media_types: tuple[str, ...] = ()
    resource_class: str = "local"
    max_concurrency: int = 1
    deterministic: bool = False
    sample_rate: int | None = None
    voice_identity: VoiceIdentity = VoiceIdentity.NONE
    requires_reference: bool = False
    external_transfer: bool = False
    postprocess_provenance: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class FacilityRequest:
    """A job request. No model is chosen implicitly when arms are ambiguous."""

    work_id: str
    actor_id: str
    capability: str
    payload: dict[str, Any]
    requested_arm: str = ""
    selection_policy: SelectionPolicy = SelectionPolicy.EXACT
    input_artifacts: tuple[ArtifactRef, ...] = ()
    release_scope: ReleaseScope = ReleaseScope.INTERNAL
    authorization_ref: str = ""
    timeout_seconds: int = 300
    resource_envelope: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    submitted_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class FacilityHealth:
    facility_id: str
    arm_id: str
    availability: Availability
    checked_at: str
    reason: str = ""
    queue_depth: int = 0
    running: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


@dataclass(frozen=True)
class FacilityResult:
    """Both job lifecycle and normal admission outcomes use one result shape."""

    job_id: str
    work_id: str
    status: JobStatus
    facility_id: str = ""
    arm_id: str = ""
    model_id: str = ""
    model_revision: str = ""
    backend: str = ""
    device: str = ""
    output_artifacts: tuple[ArtifactRef, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    error_class: str = ""
    error_message: str = ""
    queued_at: str = ""
    started_at: str = ""
    finished_at: str = ""

    @property
    def terminal(self) -> bool:
        return self.status.terminal

    def to_dict(self) -> dict[str, Any]:
        return _plain(asdict(self))


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
