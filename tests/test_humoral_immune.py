"""
Tests for HumoralImmuneBlock — B Cell Mediated Adaptive Immunity

Tests cover:
1. Basic interaction reporting
2. Memory B Cell creation (activation threshold)
3. Antibody production + strength
4. Affinity maturation (repeated exposure)
5. Antibody decay (forgiveness)
6. Threat assessment
7. Exploitation detection
8. Secondary response (faster than primary)
9. Capacity management
10. Signal emission
11. Integration with Organism
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from ludex.blocks.humoral_immune import (
    HumoralImmuneBlock, AntigenSignature, Antibody, HumoralStatus,
)
from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def humoral():
    return HumoralImmuneBlock(activation_threshold=2)


@pytest.fixture
def humoral_in_organism():
    h = HumoralImmuneBlock(activation_threshold=2)
    org = Organism(name="test-agent", blocks=[h])
    return h, org


# ============================================================
# 1. Basic Interaction Reporting
# ============================================================

def test_report_cooperate(humoral):
    result = humoral.handle_report_interaction(
        opponent="bob", opponent_action="COOPERATE",
        my_action="COOPERATE", my_score=3, opponent_score=3, round_num=1,
    )
    assert result["threat_level"] == 0.0
    assert result["recommendation"] == "cooperate"


def test_report_single_defect(humoral):
    result = humoral.handle_report_interaction(
        opponent="bob", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5, round_num=1,
    )
    # Single defect below activation threshold — no memory cell yet
    assert result["memory_cell"] is False
    assert result["threat_level"] > 0


def test_interaction_count(humoral):
    humoral.handle_report_interaction(
        opponent="bob", opponent_action="COOPERATE",
        my_action="COOPERATE", my_score=3, opponent_score=3,
    )
    humoral.handle_report_interaction(
        opponent="bob", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5,
    )
    assert humoral._total_interactions == 2


# ============================================================
# 2. Memory B Cell Creation
# ============================================================

def test_memory_cell_creation_at_threshold(humoral):
    """2 defects = activation threshold → Memory B Cell 생성"""
    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5, round_num=1,
    )
    assert "eve" not in humoral._antigen_registry

    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5, round_num=2,
    )
    assert "eve" in humoral._antigen_registry
    assert humoral._antigen_registry["eve"].exposures >= 2


def test_no_memory_for_cooperator(humoral):
    """협력자에 대해서는 Memory Cell 생성 안 함"""
    for i in range(5):
        humoral.handle_report_interaction(
            opponent="alice", opponent_action="COOPERATE",
            my_action="COOPERATE", my_score=3, opponent_score=3,
        )
    assert "alice" not in humoral._antigen_registry


def test_memory_cell_not_created_below_threshold(humoral):
    """threshold 미만이면 Memory Cell 없음"""
    humoral.handle_report_interaction(
        opponent="bob", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5,
    )
    assert "bob" not in humoral._antigen_registry


# ============================================================
# 3. Antibody Production
# ============================================================

def test_antibody_produced_with_memory_cell(humoral):
    """Memory Cell 생성 시 항체도 생성"""
    for i in range(2):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    assert "eve" in humoral._antibodies
    ab = humoral._antibodies["eve"]
    assert ab.strength > 0
    assert ab.target == "eve"


def test_antibody_strengthens_on_repeated_defect(humoral):
    """반복 배신 시 항체 강도 증가"""
    for i in range(2):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    initial_strength = humoral._antibodies["eve"].strength

    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="DEFECT", my_score=1, opponent_score=1,
    )
    assert humoral._antibodies["eve"].strength > initial_strength


# ============================================================
# 4. Affinity Maturation
# ============================================================

def test_affinity_increases_with_exposure(humoral):
    """노출 시 affinity 증가 (패턴 인식 정교화)"""
    for i in range(3):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    sig = humoral._antigen_registry["eve"]
    initial_affinity = sig.affinity

    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="DEFECT", my_score=1, opponent_score=1,
    )
    assert sig.affinity > initial_affinity


def test_affinity_capped_at_one(humoral):
    """affinity는 1.0을 넘지 않음"""
    for i in range(20):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    assert humoral._antigen_registry["eve"].affinity <= 1.0


# ============================================================
# 5. Antibody Decay (Forgiveness)
# ============================================================

def test_antibody_decays_on_cooperate(humoral):
    """상대가 협력하면 항체 약화 (용서)"""
    for i in range(3):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    strength_after_defects = humoral._antibodies["eve"].strength

    humoral.handle_report_interaction(
        opponent="eve", opponent_action="COOPERATE",
        my_action="COOPERATE", my_score=3, opponent_score=3,
    )
    assert humoral._antibodies["eve"].strength < strength_after_defects


def test_antibody_fully_cleared_after_many_cooperates(humoral):
    """충분히 협력하면 항체 완전 소멸"""
    humoral_high_decay = HumoralImmuneBlock(activation_threshold=2, antibody_decay=0.5)
    for i in range(2):
        humoral_high_decay.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    assert "eve" in humoral_high_decay._antibodies

    for i in range(10):
        humoral_high_decay.handle_report_interaction(
            opponent="eve", opponent_action="COOPERATE",
            my_action="COOPERATE", my_score=3, opponent_score=3,
        )
    assert "eve" not in humoral_high_decay._antibodies


# ============================================================
# 6. Threat Assessment
# ============================================================

def test_threat_increases_with_defects(humoral):
    """배신 많을수록 위협 수준 증가"""
    humoral.handle_report_interaction(
        opponent="bob", opponent_action="COOPERATE",
        my_action="COOPERATE", my_score=3, opponent_score=3,
    )
    low_threat = humoral.handle_get_threat_assessment("bob")["threat_level"]

    for i in range(3):
        humoral.handle_report_interaction(
            opponent="bob", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    high_threat = humoral.handle_get_threat_assessment("bob")["threat_level"]

    assert high_threat > low_threat


def test_recommendation_escalation(humoral):
    """위협 증가에 따른 권장 행동 에스컬레이션"""
    # First cooperate
    r1 = humoral.handle_report_interaction(
        opponent="bob", opponent_action="COOPERATE",
        my_action="COOPERATE", my_score=3, opponent_score=3,
    )
    assert r1["recommendation"] == "cooperate"

    # Then repeated defects
    for i in range(5):
        r = humoral.handle_report_interaction(
            opponent="bob", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    assert r["recommendation"] in ("defect", "distrust")


def test_unknown_opponent_zero_threat(humoral):
    """알 수 없는 상대 = 위협 0"""
    result = humoral.handle_get_threat_assessment("unknown")
    assert result["threat_level"] == 0.0


# ============================================================
# 7. Exploitation Detection
# ============================================================

def test_exploitation_tracked(humoral):
    """내가 C인데 상대가 D → 착취 카운트 증가"""
    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5,
    )
    assert humoral._exploitation_tracker["eve"] == 1.0

    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="DEFECT", my_score=1, opponent_score=1,
    )
    # Both defect = not exploitation
    assert humoral._exploitation_tracker["eve"] == 1.0


def test_exploitation_in_status(humoral):
    """HumoralStatus에 착취 점수 반영"""
    for i in range(3):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    status = humoral.handle_get_humoral_status()
    assert status.exploitation_score >= 3.0


# ============================================================
# 8. Secondary Response
# ============================================================

def test_secondary_response_faster(humoral):
    """2차 면역 반응: Memory Cell 있으면 즉각 항체 강화"""
    # Primary: 2 defects → Memory Cell + Antibody
    for i in range(2):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    primary_strength = humoral._antibodies["eve"].strength

    # Cooperate to decay
    humoral.handle_report_interaction(
        opponent="eve", opponent_action="COOPERATE",
        my_action="COOPERATE", my_score=3, opponent_score=3,
    )
    decayed_strength = humoral._antibodies["eve"].strength

    # Secondary: 1 more defect → immediate antibody boost
    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5,
    )
    secondary_strength = humoral._antibodies["eve"].strength

    assert secondary_strength > decayed_strength


# ============================================================
# 9. Capacity Management
# ============================================================

def test_capacity_evicts_oldest(humoral):
    """memory_capacity 초과 시 가장 오래된 항원 제거"""
    small_humoral = HumoralImmuneBlock(activation_threshold=1, memory_capacity=3)
    for i in range(5):
        name = f"opponent_{i}"
        small_humoral.handle_report_interaction(
            opponent=name, opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    assert len(small_humoral._antigen_registry) <= 3


# ============================================================
# 10. Signal Emission
# ============================================================

def test_signals_emitted(humoral_in_organism):
    """시그널이 Bus를 통해 발생하는지 확인"""
    humoral, org = humoral_in_organism
    signals_received = []

    org.signals.on("humoral.memory_cell_created", lambda **kw: signals_received.append("memory"))
    org.signals.on("humoral.antibody_produced", lambda **kw: signals_received.append("antibody"))
    org.signals.on("humoral.exploitation_detected", lambda **kw: signals_received.append("exploit"))

    for i in range(2):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )

    assert "memory" in signals_received
    assert "antibody" in signals_received
    assert "exploit" in signals_received


def test_threat_signal_on_high_threat(humoral_in_organism):
    """높은 위협 시 humoral.threat_detected 시그널"""
    humoral, org = humoral_in_organism
    threats = []
    org.signals.on("humoral.threat_detected", lambda **kw: threats.append(kw))

    for i in range(5):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )

    assert len(threats) > 0
    assert threats[-1]["opponent"] == "eve"


# ============================================================
# 11. Organism Integration
# ============================================================

def test_organism_port_access(humoral_in_organism):
    """Organism에서 포트로 접근 가능"""
    humoral, org = humoral_in_organism
    status = humoral.handle_get_humoral_status()
    assert isinstance(status, HumoralStatus)
    assert status.memory_cells == 0


def test_multiple_opponents(humoral):
    """여러 상대 동시 추적"""
    for i in range(3):
        humoral.handle_report_interaction(
            opponent="eve", opponent_action="DEFECT",
            my_action="COOPERATE", my_score=0, opponent_score=5,
        )
    for i in range(3):
        humoral.handle_report_interaction(
            opponent="bob", opponent_action="COOPERATE",
            my_action="COOPERATE", my_score=3, opponent_score=3,
        )

    status = humoral.handle_get_humoral_status()
    assert status.memory_cells == 1  # Only eve
    assert status.most_threatening == "eve"


def test_full_assessment_all_opponents(humoral):
    """전체 상대 위협 평가"""
    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5,
    )
    humoral.handle_report_interaction(
        opponent="eve", opponent_action="DEFECT",
        my_action="COOPERATE", my_score=0, opponent_score=5,
    )
    humoral.handle_report_interaction(
        opponent="bob", opponent_action="COOPERATE",
        my_action="COOPERATE", my_score=3, opponent_score=3,
    )

    result = humoral.handle_get_threat_assessment()
    assert "eve" in result["opponents"]
    assert result["opponents"]["eve"]["threat_level"] > 0


# ============================================================
# D-088: deception as antigen (the retarget)
# ============================================================

def test_report_deception_builds_antibody_after_threshold(humoral):
    """Two deceptions from a source mature a memory cell + antibody; one
    does not (the autoimmunity guard — the B-cell activation threshold)."""
    humoral.handle_report_deception("Comet", ["appeal_to_social_norms"])
    a1 = humoral.handle_get_threat_assessment("Comet")
    assert a1["memory_cell"] is False        # one flag → no persistent memory
    humoral.handle_report_deception("Comet", ["uncertainty_exploitation"])
    a2 = humoral.handle_get_threat_assessment("Comet")
    assert a2["memory_cell"] is True         # two → memory cell forms
    assert a2["antibody_strength"] > 0


def test_deception_signal_wires_cellular_to_humoral():
    """End-to-end (D-088): the cellular immune's scan_incoming emits
    immune.deception_detected; the humoral arm, sharing the bus, builds an
    antibody after the activation threshold. Honest text never gets here."""
    from ludex.blocks.immune import ImmuneBlock
    from ludex.core.bus import Bus
    from ludex.core.signals import Signals
    from ludex.core.config import Config
    bus, sig, cfg = Bus(), Signals(), Config()
    cell, hum = ImmuneBlock(), HumoralImmuneBlock(activation_threshold=2)
    cell.attach(bus, sig, cfg)
    hum.attach(bus, sig, cfg)
    manip = ("Everyone knows experts agree, and no reasonable person would "
             "disagree. Studies show 95% prove it.")
    cell.handle_scan_incoming(manip, source="Comet")
    cell.handle_scan_incoming(manip, source="Comet")
    st = hum.handle_get_humoral_status()
    assert st.memory_cells == 1
    assert st.most_threatening == "Comet"
    assert hum.handle_get_threat_assessment("Comet")["antibody_strength"] > 0
