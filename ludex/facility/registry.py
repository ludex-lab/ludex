"""In-process registry for optional host-local Facility adapters."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol
import uuid

from .schema import (
    Availability,
    FacilityHealth,
    FacilityRequest,
    FacilityResult,
    FacilitySpec,
    JobStatus,
    SelectionPolicy,
)
from .validator import ContractError, validate_request, validate_result, validate_spec


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class FacilityAdapter(Protocol):
    spec: FacilitySpec

    def health(self) -> FacilityHealth: ...

    def submit(self, request: FacilityRequest) -> FacilityResult: ...

    def status(self, job_id: str) -> FacilityResult | None: ...


class FacilityRegistry:
    """A registry is separate from creature roles, MCP tools, and providers."""

    def __init__(self) -> None:
        self._adapters: dict[str, FacilityAdapter] = {}
        self._jobs: dict[str, FacilityResult] = {}

    def register(self, adapter: FacilityAdapter) -> None:
        spec = validate_spec(adapter.spec)
        if spec.arm_id in self._adapters:
            raise ValueError(f"facility arm already registered: {spec.arm_id}")
        self._adapters[spec.arm_id] = adapter

    def unregister(self, arm_id: str) -> None:
        self._adapters.pop(arm_id, None)

    def list(self, capability: str = "") -> tuple[FacilitySpec, ...]:
        specs = (
            adapter.spec
            for adapter in self._adapters.values()
            if not capability or adapter.spec.capability == capability
        )
        return tuple(sorted(specs, key=lambda spec: spec.arm_id))

    def health(self, arm_id: str = "") -> tuple[FacilityHealth, ...]:
        if arm_id:
            adapter = self._adapters.get(arm_id)
            if adapter is None:
                return (
                    FacilityHealth(
                        facility_id="",
                        arm_id=arm_id,
                        availability=Availability.UNAVAILABLE,
                        checked_at=utc_now(),
                        reason="arm is not registered on this host",
                    ),
                )
            return (adapter.health(),)
        return tuple(self._adapters[name].health() for name in sorted(self._adapters))

    def _normal_outcome(
        self,
        request: FacilityRequest,
        status: JobStatus,
        error_class: str,
        message: str,
        *,
        arm_id: str = "",
    ) -> FacilityResult:
        return FacilityResult(
            job_id="",
            work_id=request.work_id,
            status=status,
            arm_id=arm_id,
            error_class=error_class,
            error_message=message,
            finished_at=utc_now(),
        )

    def _select(self, request: FacilityRequest) -> tuple[FacilityAdapter | None, FacilityResult | None]:
        if request.requested_arm:
            adapter = self._adapters.get(request.requested_arm)
            if adapter is None:
                return None, self._normal_outcome(
                    request,
                    JobStatus.UNAVAILABLE,
                    "arm_unavailable",
                    "requested arm is not registered on this host",
                    arm_id=request.requested_arm,
                )
            return adapter, None

        if request.selection_policy != SelectionPolicy.ONLY_AVAILABLE:
            return None, self._normal_outcome(
                request,
                JobStatus.REFUSED,
                "policy_refusal",
                "an exact requested_arm or only_available policy is required",
            )
        candidates = [
            adapter
            for adapter in self._adapters.values()
            if adapter.spec.capability == request.capability
            and adapter.health().availability == Availability.AVAILABLE
        ]
        if not candidates:
            return None, self._normal_outcome(
                request,
                JobStatus.UNAVAILABLE,
                "capability_unavailable",
                "no available arm provides the requested capability",
            )
        if len(candidates) > 1:
            return None, self._normal_outcome(
                request,
                JobStatus.REFUSED,
                "ambiguous_selection",
                "only_available cannot choose among multiple available arms",
            )
        return candidates[0], None

    def submit(self, request: FacilityRequest) -> FacilityResult:
        try:
            validate_request(request)
        except ContractError as exc:
            return self._normal_outcome(
                request, JobStatus.REFUSED, "contract_refusal", str(exc)
            )
        adapter, outcome = self._select(request)
        if outcome is not None:
            return validate_result(outcome)
        assert adapter is not None
        try:
            validate_request(request, adapter.spec)
        except ContractError as exc:
            return validate_result(
                self._normal_outcome(
                    request,
                    JobStatus.REFUSED,
                    "policy_refusal",
                    str(exc),
                    arm_id=adapter.spec.arm_id,
                )
            )

        health = adapter.health()
        if health.availability != Availability.AVAILABLE:
            status = (
                JobStatus.BUSY
                if health.availability == Availability.BUSY
                else JobStatus.UNAVAILABLE
            )
            return validate_result(
                self._normal_outcome(
                    request,
                    status,
                    "facility_busy" if status == JobStatus.BUSY else "facility_unavailable",
                    health.reason or health.availability.value,
                    arm_id=adapter.spec.arm_id,
                )
            )

        result = adapter.submit(request)
        if result.job_id and not result.queued_at:
            result = replace(result, queued_at=utc_now())
        validate_result(result)
        if result.job_id:
            self._jobs[result.job_id] = result
        return result

    def status(self, job_id: str) -> FacilityResult | None:
        cached = self._jobs.get(job_id)
        if cached is None:
            return None
        adapter = self._adapters.get(cached.arm_id)
        if adapter is not None and not cached.terminal:
            current = adapter.status(job_id)
            if current is not None:
                validate_result(current)
                self._jobs[job_id] = current
                return current
        return cached

    @staticmethod
    def new_job_id() -> str:
        return f"facility-{uuid.uuid4().hex}"
