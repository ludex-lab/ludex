"""A weightless Facility adapter for hosts without generation hardware."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

from .registry import FacilityRegistry, utc_now
from .schema import (
    ArtifactRef,
    Availability,
    FacilityHealth,
    FacilityRequest,
    FacilityResult,
    FacilitySpec,
    JobStatus,
)


class MockFacilityAdapter:
    def __init__(
        self,
        spec: FacilitySpec,
        *,
        availability: Availability = Availability.AVAILABLE,
        complete_immediately: bool = True,
    ) -> None:
        self.spec = spec
        self.availability = availability
        self.complete_immediately = complete_immediately
        self._jobs: dict[str, FacilityResult] = {}

    def health(self) -> FacilityHealth:
        return FacilityHealth(
            facility_id=self.spec.facility_id,
            arm_id=self.spec.arm_id,
            availability=self.availability,
            checked_at=utc_now(),
            reason="" if self.availability == Availability.AVAILABLE else "mock gate",
            queue_depth=sum(not result.terminal for result in self._jobs.values()),
        )

    def submit(self, request: FacilityRequest) -> FacilityResult:
        job_id = FacilityRegistry.new_job_id()
        now = utc_now()
        result = FacilityResult(
            job_id=job_id,
            work_id=request.work_id,
            status=JobStatus.QUEUED,
            facility_id=self.spec.facility_id,
            arm_id=self.spec.arm_id,
            model_id=self.spec.model_id,
            model_revision=self.spec.model_revision,
            backend=self.spec.backend,
            device=self.spec.device,
            queued_at=now,
            lineage={"mock": True, "request_id": request.request_id},
        )
        if self.complete_immediately:
            digest = hashlib.sha256(
                json.dumps(request.payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            result = replace(
                result,
                status=JobStatus.SUCCEEDED,
                output_artifacts=(
                    ArtifactRef(
                        locator=f"mock://{job_id}",
                        sha256=digest,
                        media_type=self.spec.output_media_type,
                        release_scope=request.release_scope,
                    ),
                ),
                started_at=now,
                finished_at=now,
            )
        self._jobs[job_id] = result
        return result

    def status(self, job_id: str) -> FacilityResult | None:
        return self._jobs.get(job_id)

    def complete(self, job_id: str) -> FacilityResult:
        result = self._jobs[job_id]
        digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
        completed = replace(
            result,
            status=JobStatus.SUCCEEDED,
            output_artifacts=(
                ArtifactRef(
                    locator=f"mock://{job_id}",
                    sha256=digest,
                    media_type=self.spec.output_media_type,
                ),
            ),
            started_at=result.started_at or utc_now(),
            finished_at=utc_now(),
        )
        self._jobs[job_id] = completed
        return completed
