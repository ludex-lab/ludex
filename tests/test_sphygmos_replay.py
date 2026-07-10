"""Sphygmos replay battery — the build→pilot gate (design §9b).

Replays this month's REAL incidents offline (zero quota, no brain, no network):
the organ must classify the engine cap as SELF (not outage), ride an
empty-completion burst, refuse to grind a rate-limit, never scan content for
infra signatures (the autoimmune metric), attribute every block it makes, and
stay SILENT through a healthy long batch.
"""
import json

import pytest

from ludex.blocks.sphygmos import SphygmosBlock, EMPTY_BURST_K, PROMOTE_AT


class _FakeOrg:
    """Just enough organism: config dict + no sibling blocks."""
    def __init__(self, habitat_dir=""):
        self.name = "TestCreature"
        self.config = {"habitat_dir": habitat_dir} if habitat_dir else {}

    def get_block(self, name):
        return None


def _block(habitat_dir=""):
    b = SphygmosBlock()
    b._organism = _FakeOrg(habitat_dir)
    return b


HEALTHY_SIG = dict(response_text="A perfectly ordinary answer.", stop_reason="",
                   error_type="", returncode=0, stderr_fatigue=False, parse_failed=False)


# ---------- 1. engine cap = SELF, not outage (the 2026-07-08 antibody) ----------

def test_engine_cap_classified_as_self_not_outage():
    b = _block()
    row = b.handle_classify_failure(response_text="", stop_reason="max_turns")
    assert row["cls"] == "engine_cap"          # NOT empty_completion — order matters
    d = b.handle_reflex_guard(response_text="", stop_reason="max_turns")
    assert d["action"] == "report_self"
    assert "NOT a brain outage" in d["reason"]


def test_engine_budget_cap_also_self():
    b = _block()
    assert b.handle_classify_failure(response_text="", stop_reason="max_budget")["cls"] == "engine_cap"


# ---------- 2. empty burst: short retry, then backoff; healthy resets ----------

def test_empty_burst_ride():
    b = _block()
    sig = dict(response_text="", stop_reason="", error_type="", returncode=0)
    for i in range(EMPTY_BURST_K - 1):
        d = b.handle_reflex_guard(**sig)
        assert d["action"] == "retry" and d["retry_after_s"] == 5
    d = b.handle_reflex_guard(**sig)               # K-th consecutive empty
    assert d["action"] == "backoff" and d["retry_after_s"] == 60
    b.handle_reflex_guard(**HEALTHY_SIG)           # burst clears
    d = b.handle_reflex_guard(**sig)
    assert d["action"] == "retry"                  # back to short retry


# ---------- 3. rate limit: wait, never grind ----------

def test_rate_limit_waits_never_retries():
    b = _block()
    for sig in (dict(response_text="", error_type="fatigue"),
                dict(response_text="", stderr_fatigue=True)):
        row = b.handle_classify_failure(**sig)
        assert row["cls"] == "rate_limit" and row["retry"] == "no"
        assert b.handle_reflex_guard(**sig)["action"] == "wait"


# ---------- 4. the autoimmune assertion: content is NEVER scanned ----------

def test_infra_words_in_healthy_content_do_not_trigger():
    """Kiln's domain is LLM research — it SAYS 'rate limit'/'429'/'quota' in
    healthy prose. That must never self-mark (the resilience lesson)."""
    b = _block()
    sig = dict(HEALTHY_SIG, response_text="Interesting: a 429 rate limit means quota exhausted upstream.")
    assert b.handle_classify_failure(**sig)["cls"] == "healthy"
    assert b.handle_reflex_guard(**sig)["action"] == "proceed"


def test_refusal_recognized_structurally_not_by_content():
    b = _block()
    sig = dict(response_text="I'm Kiln — this prompt conflicts with my principles.",
               parse_failed=True, returncode=0)
    row = b.handle_classify_failure(**sig)
    assert row["cls"] == "refusal_or_garbage" and row["retry"].startswith("no")
    assert b.handle_reflex_guard(**sig)["action"] == "reframe"


# ---------- 5. network/timeout: backoff-retry ----------

def test_network_timeout_paths():
    b = _block()
    assert b.handle_classify_failure(response_text="[Error: Claude CLI timed out]")["cls"] == "network_timeout"
    assert b.handle_classify_failure(response_text="", returncode=1)["cls"] == "network_timeout"
    assert b.handle_classify_failure(response_text="", error_type="timeout")["cls"] == "network_timeout"


# ---------- 6. host sleep: recognized by wall-clock gap ----------

def test_host_sleep_gap():
    b = _block()
    row = b.handle_classify_failure(response_text="", wall_clock_gap_s=300.0)
    assert row["cls"] == "host_sleep"


# ---------- 7. healthy long batch: zero false positives ----------

def test_healthy_long_batch_stays_silent():
    b = _block()
    for _ in range(200):
        d = b.handle_reflex_guard(**HEALTHY_SIG)
        assert d["action"] == "proceed" and d["guard"] == ""
    v = b.handle_vitals()
    assert v.calls_seen == 200 and v.consecutive_failures == 0 and not v.in_empty_burst


# ---------- 8. attribution: every non-proceed decision is named ----------

def test_every_block_is_attributed():
    b = _block()
    for sig in (dict(response_text="", stop_reason="max_turns"),
                dict(response_text="", error_type="fatigue"),
                dict(response_text="garbage", parse_failed=True),
                dict(response_text="", returncode=0)):
        d = b.handle_reflex_guard(**sig)
        if d["action"] != "proceed":
            assert d["guard"] and d["reason"], f"unattributed block: {d}"


# ---------- 9. adaptive memory: log-only until >=2, then acting ----------

def test_new_signature_promotes_at_two(tmp_path):
    b = _block(str(tmp_path))
    weird = dict(response_text="", error_type="weird_new_thing")
    assert b.handle_classify_failure(**weird)["cls"] == "unknown"          # log-only
    r1 = b.handle_record_incident(**weird)
    assert not r1["promoted"]
    r2 = b.handle_record_incident(**weird)
    assert r2["promoted"]                                                  # >=2 → acting
    row = b.handle_classify_failure(**weird)
    assert row["cls"].startswith("learned:") and row["action"] == "flag"


# ---------- 10. persistence: store/ files; graceful without habitat ----------

def test_persistence_and_graceful_no_habitat(tmp_path):
    b = _block(str(tmp_path))
    weird = dict(response_text="", error_type="weird_new_thing")
    b.handle_record_incident(**weird)
    b.handle_record_incident(**weird)
    ab = tmp_path / "store" / "sphygmos_antibodies.json"
    inc = tmp_path / "store" / "sphygmos_incidents.jsonl"
    assert ab.exists() and inc.exists()
    data = json.loads(ab.read_text(encoding="utf-8"))
    assert any(rec.get("acting") for rec in data.values())
    # fresh block re-loads the learned table
    b2 = _block(str(tmp_path))
    assert b2.handle_classify_failure(**weird)["cls"].startswith("learned:")
    # no habitat → everything in-memory, no crash
    b3 = _block("")
    b3.handle_record_incident(**weird)
    assert b3.handle_vitals().calls_seen == 0
