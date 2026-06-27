"""publish_card.py — publish a creature's opt-in 명함 (business card) to the MTI gallery.

Builds the card (identity + temperament radar + owner intro) per the cards.json contract
(Ray ↔ Cody, 2026-06-23) and appends/updates it in the gallery's web/data/cards.json.
OPT-IN, per-creature, deliberate: the creature's intimate SELF.md / bonds / raw memory are
NEVER published — only the OWNER's public intro + the OBJECTIVE MTI temperament.

Prereqs: the creature has an mti.json (run tools/mti_measure.py first — the card needs the
radar). The gallery repo (github.com/JihoonJeong/mti-gallery) cloned at --gallery (push
access is JJ-owned). Without --gallery this just prints the card (dry preview).

Usage:
    python tools/publish_card.py <creature> --intro "owner's public intro…" [--tagline "…"]
        [--root creatures] [--gallery <mti-gallery clone>] [--push]
"""
import os, sys, json, re, argparse, subprocess, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ludex.core.organism_config import OrganismConfig

# provider → short label for the card's readable brain string ("codex (gpt-5.5)").
_PROVIDER_SHORT = {"claude_cli": "claude", "codex_cli": "codex", "gemini_cli": "gemini",
                   "agy_cli": "agy", "ollama": "ollama", "anthropic": "claude",
                   "openai": "openai", "gemini_api": "gemini"}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_card(habitat: Path, intro: str, tagline: str = "") -> dict:
    """The public card — owner's voice + objective temperament. NEVER SELF.md/bonds/memory."""
    cfg = OrganismConfig.load(str(habitat))
    name = cfg.name or habitat.name
    brain = cfg.brain or {}
    prov, model = brain.get("provider", ""), brain.get("model", "")
    brain_str = f"{_PROVIDER_SHORT.get(prov, prov)} ({model})" if model else (prov or "?")
    card = {"id": _slug(name), "name": name, "brain": brain_str,
            "owner_intro": intro, "published_at": _iso_now()}
    if tagline:
        card["tagline"] = tagline
    mp = habitat / "mti.json"   # the radar (lived.x_mti); private narrative is NOT read
    if mp.is_file():
        mti = json.loads(mp.read_text(encoding="utf-8"))
        card["x_mti"] = {"cohort_ref": mti.get("cohort_ref", ""), "axes": mti.get("axes", {})}
    return card


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publish a creature's opt-in card to the MTI gallery cards.json")
    ap.add_argument("creature")
    ap.add_argument("--root", default="creatures")
    ap.add_argument("--intro", default="", help="owner's public intro (or put it in creatures/<c>/card.md)")
    ap.add_argument("--tagline", default="")
    ap.add_argument("--gallery", default=os.environ.get("MTI_GALLERY_PATH", ""),
                    help="path to the mti-gallery repo clone; writes web/data/cards.json")
    ap.add_argument("--push", action="store_true", help="commit + push the gallery repo after writing")
    args = ap.parse_args(argv)

    habitat = Path(args.root) / args.creature
    if not habitat.is_dir():
        print(f"no creature habitat at {habitat}", file=sys.stderr); return 1
    intro = args.intro.strip()
    if not intro:
        cm = habitat / "card.md"
        if cm.is_file():
            intro = cm.read_text(encoding="utf-8").strip()
    if not intro:
        print("--intro required (the owner's public intro), or add creatures/<c>/card.md", file=sys.stderr); return 1
    if not (habitat / "mti.json").is_file():
        print(f"{args.creature}: no mti.json — run `tools/mti_measure.py {args.creature}` first "
              f"(the card needs the temperament radar)", file=sys.stderr); return 1

    card = build_card(habitat, intro, args.tagline)

    if not args.gallery:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        print("\n(dry: no --gallery. Set --gallery <mti-gallery clone> to write cards.json.)", file=sys.stderr)
        return 0

    cards_path = Path(args.gallery) / "web" / "data" / "cards.json"
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"cards": []}
    if cards_path.is_file():
        try:
            doc = json.loads(cards_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    cards = [c for c in doc.get("cards", []) if c.get("id") != card["id"]]   # replace by id
    cards.append(card)
    doc["cards"] = cards
    cards_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {card['name']} → {cards_path} ({len(cards)} card(s) in the 명함첩)")
    if args.push:
        g = str(args.gallery)
        for c in (["git", "-C", g, "add", "web/data/cards.json"],
                  ["git", "-C", g, "commit", "-m", f"cards: publish {card['name']}"],
                  ["git", "-C", g, "push"]):
            subprocess.run(c, check=True)
        print("✓ pushed to mti-gallery → GH Pages rebuilds → card goes live")
    else:
        print("(written to the gallery clone; --push to commit+push, or push it manually)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
