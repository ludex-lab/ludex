"""Validation and manifest loading for Facility contracts."""
from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .schema import (
    FacilityRequest,
    FacilityResult,
    FacilitySpec,
    JobStatus,
    ReleaseScope,
    SelectionPolicy,
    VoiceIdentity,
)


_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """A malformed contract. Policy refusals are returned as results instead."""


def _require_identifier(value: str, name: str, problems: list[str]) -> None:
    if not _IDENTIFIER.fullmatch(value):
        problems.append(f"{name} must be a stable lowercase identifier")


def spec_problems(spec: FacilitySpec) -> list[str]:
    problems: list[str] = []
    for name in ("facility_id", "capability", "arm_id", "backend"):
        _require_identifier(getattr(spec, name), name, problems)
    if not spec.model_id or any(char.isspace() for char in spec.model_id):
        problems.append("model_id must be a non-empty repository/model identifier")
    if not _REVISION.fullmatch(spec.model_revision):
        problems.append("model_revision must be an exact 7-64 character hex revision")
    if not spec.device:
        problems.append("device is required")
    if not spec.output_media_type or "/" not in spec.output_media_type:
        problems.append("output_media_type must be an explicit media type")
    if spec.max_concurrency < 1:
        problems.append("max_concurrency must be at least 1")
    if spec.sample_rate is not None and spec.sample_rate <= 0:
        problems.append("sample_rate must be positive")
    if spec.voice_identity == VoiceIdentity.CLONED_PERSON and not spec.requires_reference:
        problems.append("cloned-person arms must require a reference artifact")
    if spec.external_transfer and not spec.metadata.get("external_boundary"):
        problems.append("external_transfer arms must declare metadata.external_boundary")
    return problems


def validate_spec(spec: FacilitySpec) -> FacilitySpec:
    problems = spec_problems(spec)
    if problems:
        raise ContractError("; ".join(problems))
    return spec


def request_problems(request: FacilityRequest, spec: FacilitySpec | None = None) -> list[str]:
    problems: list[str] = []
    _require_identifier(request.work_id, "work_id", problems)
    _require_identifier(request.actor_id, "actor_id", problems)
    _require_identifier(request.capability, "capability", problems)
    if request.selection_policy == SelectionPolicy.EXACT and not request.requested_arm:
        problems.append("exact selection requires requested_arm")
    if request.requested_arm:
        _require_identifier(request.requested_arm, "requested_arm", problems)
    if request.timeout_seconds < 1:
        problems.append("timeout_seconds must be positive")
    if request.release_scope == ReleaseScope.PUBLIC and not request.authorization_ref:
        problems.append("public release requires authorization_ref")
    if spec is not None:
        if request.capability != spec.capability:
            problems.append("request capability does not match selected arm")
        if spec.external_transfer and not request.authorization_ref:
            problems.append("external transfer requires authorization_ref")
        if spec.requires_reference and not request.input_artifacts:
            problems.append("selected arm requires a reference artifact")
        if spec.voice_identity == VoiceIdentity.CLONED_PERSON:
            voice_refs = [
                artifact
                for artifact in request.input_artifacts
                if artifact.reference_role == "voice_identity"
            ]
            if not voice_refs:
                problems.append("cloned-person synthesis requires voice_identity reference")
            elif any(not artifact.consent_id for artifact in voice_refs):
                problems.append("every voice_identity reference requires consent_id")
    for artifact in request.input_artifacts:
        if not _SHA256.fullmatch(artifact.sha256):
            problems.append(f"artifact {artifact.locator!r} has an invalid sha256")
        if not artifact.media_type or "/" not in artifact.media_type:
            problems.append(f"artifact {artifact.locator!r} has no explicit media type")
    return problems


def validate_request(
    request: FacilityRequest, spec: FacilitySpec | None = None
) -> FacilityRequest:
    problems = request_problems(request, spec)
    if problems:
        raise ContractError("; ".join(problems))
    return request


def validate_result(result: FacilityResult) -> FacilityResult:
    problems: list[str] = []
    if result.status in {JobStatus.QUEUED, JobStatus.RUNNING} and not result.job_id:
        problems.append("non-terminal jobs require job_id")
    if result.status == JobStatus.SUCCEEDED:
        if not result.job_id:
            problems.append("successful jobs require job_id")
        if not result.output_artifacts:
            problems.append("successful jobs require output_artifacts")
        if not result.model_revision:
            problems.append("successful jobs require model_revision lineage")
    if result.status in {
        JobStatus.FAILED,
        JobStatus.UNAVAILABLE,
        JobStatus.BUSY,
        JobStatus.REFUSED,
    } and not result.error_class:
        problems.append(f"{result.status.value} results require error_class")
    for artifact in result.output_artifacts:
        if not _SHA256.fullmatch(artifact.sha256):
            problems.append(f"output {artifact.locator!r} has an invalid sha256")
    if problems:
        raise ContractError("; ".join(problems))
    return result


def spec_from_manifest(data: Mapping[str, Any]) -> FacilitySpec:
    allowed = {item.name for item in fields(FacilitySpec)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ContractError(f"unknown FacilitySpec fields: {', '.join(unknown)}")
    values = dict(data)
    for name in ("input_media_types", "postprocess_provenance"):
        if name in values:
            values[name] = tuple(values[name])
    if "voice_identity" in values:
        values["voice_identity"] = VoiceIdentity(values["voice_identity"])
    return validate_spec(FacilitySpec(**values))


def load_manifest(path: str | Path) -> FacilitySpec:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ContractError("Facility manifest root must be an object")
    return spec_from_manifest(data)
