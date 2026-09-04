"""Optional non-creature generation facilities.

This package intentionally imports only the Python standard library. Heavy
model runtimes belong to host-local adapters such as ``village-studio``.
"""

from .mock import MockFacilityAdapter
from .registry import FacilityAdapter, FacilityRegistry
from .schema import (
    ArtifactRef,
    Availability,
    FacilityHealth,
    FacilityRequest,
    FacilityResult,
    FacilitySpec,
    JobStatus,
    ReleaseScope,
    SelectionPolicy,
    VoiceIdentity,
)
from .validator import (
    ContractError,
    load_manifest,
    request_problems,
    spec_from_manifest,
    spec_problems,
    validate_request,
    validate_result,
    validate_spec,
)

__all__ = [
    "ArtifactRef",
    "Availability",
    "ContractError",
    "FacilityAdapter",
    "FacilityHealth",
    "FacilityRegistry",
    "FacilityRequest",
    "FacilityResult",
    "FacilitySpec",
    "JobStatus",
    "MockFacilityAdapter",
    "ReleaseScope",
    "SelectionPolicy",
    "VoiceIdentity",
    "load_manifest",
    "request_problems",
    "spec_from_manifest",
    "spec_problems",
    "validate_request",
    "validate_result",
    "validate_spec",
]
