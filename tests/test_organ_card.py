"""organ.card/v0 generator tests — the compiler never invents content:
identity/brain from yaml, evidence only from the curated file (null retention
verbatim), health honest about sphygmos absence, draft-flagged until final.
Ephemeral tmp creatures only (D-090)."""
import json

from ludex.cards import generate_card, write_card, CARD_FORMAT

YAML = """\
name: TestCard
brain:
  provider: claude_cli
  model: claude-haiku-4-5
  effort: high
organs:
  engine:
    enabled: true
  topos:
    enabled: true
  physis:
    enabled: false
  sphygmos:
    enabled: {sphy}
habitat:
  mode: local
  home_dir: .
"""


def _creature(tmp_path, sphygmos="false"):
    d = tmp_path / "TestCard"
    d.mkdir(parents=True)
    (d / "ludex.yaml").write_text(YAML.format(sphy=sphygmos), encoding="utf-8")
    return str(d)


def test_card_basics_brain_and_draft(tmp_path):
    c = _creature(tmp_path)
    card = generate_card(c)
    assert card["card_format"] == CARD_FORMAT
    assert card["creature"] == "TestCard"
    b = card["brain"]
    assert b["model"] == "claude-haiku-4-5" and b["effort_baseline"] == "high"
    assert b["auth_mode"] == "subscription"        # claude_cli default resolution
    assert "ludex.yaml" in b["provenance"]
    assert card["draft"] is True                    # Phase-1 gate
    assert "draft" not in generate_card(c, final=True)


def test_only_enabled_organs_listed_with_unmeasured_default(tmp_path):
    c = _creature(tmp_path)
    card = generate_card(c)
    names = [o["organ"] for o in card["organs"]]
    assert "engine" in names and "topos" in names
    assert "physis" not in names                    # disabled → not on the card
    topos = next(o for o in card["organs"] if o["organ"] == "topos")
    assert topos["evidence"]["kind"] == "unmeasured"  # compiler invents nothing


def test_curated_evidence_merges_verbatim_null_retention(tmp_path):
    c = _creature(tmp_path)
    store = tmp_path / "TestCard" / "store"
    store.mkdir()
    curated = {
        "organs": {
            "topos": {"claims": "live map+frontier",
                      "evidence": {"kind": "measured-null", "method": "pre-registered A/B",
                                   "n": 24, "measured_at": "2026-07-08",
                                   "target_brain": "claude-haiku-4-5@medium",
                                   "ref": "E1=0.000"}},
        },
        "task_evidence": [{"task_shape": "commit-bound exploration", "n": 18,
                           "measured_at": "2026-07-08", "ref": "C1=+2.500"}],
    }
    (store / "card_evidence.json").write_text(json.dumps(curated), encoding="utf-8")
    card = generate_card(c)
    topos = next(o for o in card["organs"] if o["organ"] == "topos")
    assert topos["evidence"]["kind"] == "measured-null"       # null stays (rule 6)
    assert topos["evidence"]["ref"] == "E1=0.000"
    assert card["task_evidence"][0]["ref"] == "C1=+2.500"


def test_health_honest_about_sphygmos(tmp_path):
    no_sphy = generate_card(_creature(tmp_path))
    assert "no health contract" in no_sphy["health"]["note"]
    with_sphy = generate_card(_creature(tmp_path / "b", sphygmos="true"))
    assert "vitals port" in with_sphy["health"]["note"]


def test_write_card_lands_in_habitat(tmp_path):
    c = _creature(tmp_path)
    out = write_card(c)
    assert out.name == "organ_card.json" and out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["card_format"] == CARD_FORMAT
