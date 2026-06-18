"""mark_habitats.py — backfill the living-creature marker into existing habitats.

Writes DO_NOT_DELETE_living_creature.md (see creature_mortality_principle): the folder
IS the creature; deleting it is irreversible death. New creatures get the marker at
creation (forge_assemble); this covers ones made before that. Idempotent.

Usage:
  python tools/mark_habitats.py                      # ./creatures
  python tools/mark_habitats.py --root ~/ludex/creatures
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ludex.core.habitat import write_living_creature_marker, LIVING_MARKER_FILE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="creatures", help="creatures dir (default: ./creatures)")
    args = ap.parse_args()
    n = 0
    for d in sorted(os.listdir(args.root)):
        p = os.path.join(args.root, d)
        if not os.path.isdir(p) or not os.path.exists(os.path.join(p, "ludex.yaml")):
            continue                                    # only real habitats (have a config)
        write_living_creature_marker(p, d)
        n += 1
        print(f"  marked {d}")
    print(f"{n} habitats marked ({LIVING_MARKER_FILE})")


if __name__ == "__main__":
    main()
