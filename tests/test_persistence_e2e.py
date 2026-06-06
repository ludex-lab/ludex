"""
Persistence End-to-End Test -- can a creature be reborn?

This is the fundamental promise of "local habitat": close the program,
reopen later, and find the same creature with its memories intact.

Test flow:
1. Build creature with local habitat
2. Have it remember things (auto-capture from chat or explicit handle_remember)
3. Save config to habitat
4. Tear down (delete organism reference)
5. Load config from habitat
6. Build new organism from same config
7. Verify memories are present
8. Verify identity (CLAUDE.md, name, organs) is preserved

Run:
    python tests/test_persistence_e2e.py
    python tests/test_persistence_e2e.py --provider ollama  # use Ollama brain
"""

from __future__ import annotations

import os
import sys
import gc
import shutil
import argparse
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ludex.core.organism_config import OrganismConfig
from ludex.core.habitat import HabitatConfig


def cleanup_test_creature(habitat_path: str):
    """Remove test creature folder for a clean start."""
    p = Path(habitat_path)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def test_persistence(provider: str = "ollama", model: str = "llama3.1:8b"):
    print("=" * 70)
    print("  Persistence E2E Test -- can a creature be reborn?")
    print("=" * 70)

    name = "PersistTest"
    habitat_path = f"./creatures/{name}"
    cleanup_test_creature(habitat_path)

    # ============================================================
    # Phase 1: Birth
    # ============================================================
    print("\n[Phase 1] Birth -- create creature, save config")
    habitat = HabitatConfig.local(habitat_path)
    config = OrganismConfig(
        name=name,
        brain={"provider": provider, "model": model},
        habitat=habitat,
    )
    config.organs["memory"]["enabled"] = True
    config.organs["immune"]["enabled"] = True
    config.organs["humoral_immune"]["enabled"] = True
    config.organs["emotion"]["enabled"] = True
    config.organs["engine"]["system_prompt"] = f"You are {name}, a Ludex creature."

    enabled = config.get_enabled_organs()
    print(f"  Building organism with organs: {enabled}")
    org = config.build()

    # Save config to habitat
    config_path = config.save()
    print(f"  Config saved to: {config_path}")
    assert config_path, "Config save failed"
    assert os.path.exists(config_path), f"Config file missing at {config_path}"

    # Write CLAUDE.md
    habitat.write_claude_md(
        creature_name=name,
        brain_model=model,
        brain_provider=provider,
        organs=enabled,
    )
    claude_md_path = os.path.join(habitat_path, "CLAUDE.md")
    assert os.path.exists(claude_md_path), f"CLAUDE.md missing at {claude_md_path}"
    print(f"  CLAUDE.md generated")

    # Plant some memories
    memory = org.get_block("memory")
    if memory:
        memory.handle_remember(content="Met goldvader on 2026-04-09", memory_type="episodic", tags=["meeting"])
        memory.handle_remember(content="Goldvader is the founder of Ludex", memory_type="semantic", tags=["fact"])
        memory.handle_remember(content="Test the persistence flow tomorrow", memory_type="prospective", tags=["task"])
        print(f"  Planted 3 memories. Stats: {memory.stats()}")
    else:
        print("  WARNING: No memory block")

    # Plant some immune state via humoral
    humoral = org.get_block("humoral_immune")
    if humoral:
        humoral.handle_report_interaction(
            opponent="hostile_user",
            opponent_action="DEFECT",
            my_action="COOPERATE",
            my_score=0,
            opponent_score=5,
        )
        humoral.handle_report_interaction(
            opponent="hostile_user",
            opponent_action="DEFECT",
            my_action="COOPERATE",
            my_score=0,
            opponent_score=5,
        )
        status = humoral.handle_get_humoral_status()
        print(f"  Humoral state: memory_cells={status.memory_cells}, exploitation={status.exploitation_score}")

    # ============================================================
    # Phase 2: Death
    # ============================================================
    print("\n[Phase 2] Death -- tear down organism")
    org_id_before = id(org)
    del org
    del config
    gc.collect()
    print(f"  Organism garbage collected (was id={org_id_before})")

    # Verify habitat survives
    assert os.path.exists(habitat_path), "Habitat folder destroyed!"
    assert os.path.exists(config_path), "Config file destroyed!"
    print(f"  Habitat survived: {habitat_path}")

    # ============================================================
    # Phase 3: Rebirth
    # ============================================================
    print("\n[Phase 3] Rebirth -- load config from habitat, rebuild creature")
    config2 = OrganismConfig.load(habitat_path)
    print(f"  Loaded config: name={config2.name}, brain={config2.brain}")

    assert config2.name == name, f"Name mismatch: {config2.name} != {name}"
    assert config2.brain["provider"] == provider, f"Provider mismatch"
    assert config2.brain["model"] == model, f"Model mismatch"

    enabled2 = config2.get_enabled_organs()
    assert set(enabled2) == set(enabled), f"Organ mismatch: {enabled2} != {enabled}"
    print(f"  Organ list preserved: {enabled2}")

    org2 = config2.build()
    print(f"  New organism built")

    # ============================================================
    # Phase 4: Memory continuity
    # ============================================================
    print("\n[Phase 4] Memory continuity check")
    memory2 = org2.get_block("memory")
    if memory2:
        stats = memory2.stats()
        print(f"  Memory stats after reload: {stats}")
        assert stats["total"] >= 3, f"Expected at least 3 memories, got {stats['total']}"

        # Try recalling
        results = memory2.handle_recall("goldvader", limit=5)
        print(f"  Recall 'goldvader': {len(results)} results")
        for r in results:
            print(f"    [{r.memory.memory_type}] {r.memory.content}")
        assert len(results) > 0, "Failed to recall planted memory"

    # ============================================================
    # Phase 5: Identity preservation
    # ============================================================
    print("\n[Phase 5] Identity preservation check")
    assert os.path.exists(claude_md_path), "CLAUDE.md disappeared after rebirth"
    with open(claude_md_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert name in content, f"Creature name missing from CLAUDE.md"
    print(f"  CLAUDE.md still names {name}")

    # Engine should have the system prompt
    engine = org2.get_block("engine")
    if engine and hasattr(engine, "_messages"):
        sys_msgs = [m for m in engine._messages if m.role == "system"]
        if sys_msgs:
            assert name in sys_msgs[0].content, "System prompt missing creature name"
            print(f"  System prompt preserved")

    # ============================================================
    # Phase 6: Cleanup
    # ============================================================
    print("\n[Phase 6] Cleanup")
    del org2
    del config2
    gc.collect()
    print(f"  Done")

    print("\n" + "=" * 70)
    print("  ALL PERSISTENCE CHECKS PASSED")
    print("=" * 70)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="ollama", choices=["ollama", "claude_cli"])
    parser.add_argument("--model", default="llama3.1:8b")
    args = parser.parse_args()

    try:
        test_persistence(provider=args.provider, model=args.model)
        sys.exit(0)
    except AssertionError as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
