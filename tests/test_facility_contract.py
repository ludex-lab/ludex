from __future__ import annotations

import json

import pytest

from ludex.facility import (
    ArtifactRef,
    Availability,
    ContractError,
    FacilityRegistry,
    FacilityRequest,
    FacilitySpec,
    JobStatus,
    MockFacilityAdapter,
    ReleaseScope,
    SelectionPolicy,
    VoiceIdentity,
    load_manifest,
    validate_request,
    validate_spec,
)


REVISION = "0c0e3051f131929182e2c023b9537f8b1c68adfe"


def voice_spec(**changes):
    values = {
        "facility_id": "studio.voice",
        "capability": "voice.synthesize",
        "arm_id": "qwen3-customvoice",
        "model_id": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "model_revision": REVISION,
        "backend": "transformers",
        "device": "mps",
        "output_media_type": "audio/wav",
        "resource_class": "studio-m3-ultra",
        "sample_rate": 24000,
        "voice_identity": VoiceIdentity.PRESET,
        "postprocess_provenance": ("qwen-codec-decode", "wav-pcm16"),
    }
    values.update(changes)
    return FacilitySpec(**values)


def voice_request(**changes):
    values = {
        "work_id": "studio-voice-smoke",
        "actor_id": "ieum",
        "capability": "voice.synthesize",
        "payload": {"text": "안녕하세요.", "speaker": "Sohee"},
        "requested_arm": "qwen3-customvoice",
    }
    values.update(changes)
    return FacilityRequest(**values)


def test_manifest_round_trip_is_dependency_free_and_revision_pinned(tmp_path):
    spec = validate_spec(voice_spec())
    manifest = tmp_path / "facility.json"
    manifest.write_text(json.dumps(spec.to_dict()), encoding="utf-8")

    assert load_manifest(manifest) == spec

    with pytest.raises(ContractError, match="exact"):
        validate_spec(voice_spec(model_revision="main"))


def test_unavailable_is_normal_result_and_not_policy_refusal():
    registry = FacilityRegistry()

    result = registry.submit(voice_request())

    assert result.status == JobStatus.UNAVAILABLE
    assert result.error_class == "arm_unavailable"
    assert result.job_id == ""


def test_busy_is_normal_result_and_refusal_stays_separate():
    registry = FacilityRegistry()
    registry.register(
        MockFacilityAdapter(voice_spec(), availability=Availability.BUSY)
    )

    busy = registry.submit(voice_request())
    public = registry.submit(voice_request(release_scope=ReleaseScope.PUBLIC))

    assert busy.status == JobStatus.BUSY
    assert busy.error_class == "facility_busy"
    assert public.status == JobStatus.REFUSED
    assert public.error_class == "contract_refusal"


def test_only_available_policy_refuses_ambiguous_arm_selection():
    registry = FacilityRegistry()
    registry.register(MockFacilityAdapter(voice_spec()))
    registry.register(
        MockFacilityAdapter(
            voice_spec(
                arm_id="chatterbox-v3",
                model_id="ResembleAI/chatterbox",
                model_revision="5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18",
            )
        )
    )
    request = voice_request(
        requested_arm="", selection_policy=SelectionPolicy.ONLY_AVAILABLE
    )

    result = registry.submit(request)

    assert result.status == JobStatus.REFUSED
    assert result.error_class == "ambiguous_selection"


def test_mock_supports_immediate_and_queued_job_contracts():
    immediate_registry = FacilityRegistry()
    immediate_registry.register(MockFacilityAdapter(voice_spec()))
    done = immediate_registry.submit(voice_request())

    assert done.status == JobStatus.SUCCEEDED
    assert done.output_artifacts[0].media_type == "audio/wav"
    assert immediate_registry.status(done.job_id) == done

    queued_registry = FacilityRegistry()
    adapter = MockFacilityAdapter(voice_spec(), complete_immediately=False)
    queued_registry.register(adapter)
    queued = queued_registry.submit(voice_request())

    assert queued.status == JobStatus.QUEUED
    adapter.complete(queued.job_id)
    assert queued_registry.status(queued.job_id).status == JobStatus.SUCCEEDED


def test_cloned_person_requires_reference_consent_and_public_authorization():
    cloned = voice_spec(
        arm_id="consented-clone",
        voice_identity=VoiceIdentity.CLONED_PERSON,
        requires_reference=True,
    )
    validate_spec(cloned)
    digest = "a" * 64
    no_consent = voice_request(
        requested_arm="consented-clone",
        input_artifacts=(
            ArtifactRef(
                locator="artifact://voice-ref",
                sha256=digest,
                media_type="audio/wav",
                reference_role="voice_identity",
            ),
        ),
    )

    with pytest.raises(ContractError, match="consent_id"):
        validate_request(no_consent, cloned)

    consented = voice_request(
        requested_arm="consented-clone",
        input_artifacts=(
            ArtifactRef(
                locator="artifact://voice-ref",
                sha256=digest,
                media_type="audio/wav",
                reference_role="voice_identity",
                consent_id="consent-jj-001",
            ),
        ),
        release_scope=ReleaseScope.PUBLIC,
        authorization_ref="approval-jj-001",
    )
    assert validate_request(consented, cloned) == consented
