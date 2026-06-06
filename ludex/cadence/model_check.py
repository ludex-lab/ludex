"""
Model-currency cadence — detect when a creature's brain model has drifted
from what its provider currently offers.

Companion to proposer.py. Where the proposer asks "who should meet next",
this asks "whose brain has gone stale". It surveys the population, queries
each provider's live model list, and classifies every creature as
CURRENT / SUPERSEDED / DEPRECATED / UNVERIFIABLE for caretaker review.

No auto-upgrade: per substrate_change_policy + creature_mortality_principle,
a brain change is a deliberate act (preserve / transplant / death) the
caretaker decides. This process only detects and reports — and, with
--record, leaves a span in the creature's store so the creature itself can
perceive the change next session.

Coverage note: CLI-auth creatures (claude_cli, codex_cli, ...) run the same
underlying models as their HTTP sibling, so we check their model string
against the sibling's live /models list (claude_cli → anthropic, etc.).
Without a key for that sibling, the creature is UNVERIFIABLE, not flagged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from ludex.cadence.proposer import _survey_population, CREATURES_ROOT
from ludex.core.organism_config import _PROVIDER_ENV_KEYS
from ludex.core.store import LudexStore, Span

logger = logging.getLogger(__name__)


# Status labels
CURRENT = "CURRENT"
SUPERSEDED = "SUPERSEDED"
DEPRECATED = "DEPRECATED"
UNVERIFIABLE = "UNVERIFIABLE"

# Which live model list to check a creature's model against. CLI-auth
# providers run the same underlying models as their HTTP sibling, so we
# borrow the sibling's live /models list for currency.
_VERIFY_VIA = {
    "anthropic": "anthropic",
    "claude_cli": "anthropic",
    "claude_sdk": "anthropic",
    "openai": "openai",
    "codex_cli": "openai",
    "gemini_api": "gemini_api",
    "gemini_cli": "gemini_api",
    "agy_cli": "gemini_api",
    "ollama": "ollama",
}

# HTTP catalog sources the heartbeat currency check resolves by default.
# ollama is excluded on purpose — its "available" list is what's pulled
# locally, not what the provider still offers, so it never yields a real
# currency event (see record_currency_span / model_currency_cadence).
DEFAULT_CURRENCY_SOURCES = ("anthropic", "openai", "gemini_api")

# Disk cache for live /models results, anchored to the repo root so it is
# stable regardless of cwd. Gated by a TTL so the heartbeat does not query
# /models every pulse — currency drifts rarely.
_CACHE_PATH = Path(__file__).resolve().parents[2] / ".model_currency_cache.json"


# ============================================================
# Version parsing + classification (pure, network-free)
# ============================================================

def _parse_model(model: str) -> tuple[tuple[str, ...] | None, tuple[int, ...], int | None]:
    """Parse a model id into (family, version, date).

    family : alpha descriptor tokens, e.g. ('claude','opus'), ('gemini','flash')
    version: numeric components, e.g. (4, 8) for claude-opus-4-8
    date   : trailing YYYYMMDD snapshot if present, else None

    Returns (None, (), None) when the id carries no version number (e.g. a
    CLI placeholder like "claude-code (default)").
    """
    norm = (model or "").lower()
    if norm.startswith("models/"):  # Gemini OpenAI-compat ids
        norm = norm[len("models/"):]
    norm = norm.replace(".", "-").replace("/", "-")
    family: list[str] = []
    version: list[int] = []
    date_parts: list[int] = []
    in_date = False  # once a date run starts, trailing numbers are date, not version
    for tok in norm.split("-"):
        tok = tok.strip()
        if not tok:
            continue
        if tok.isdigit():
            n = int(tok)
            if len(tok) >= 6:  # contiguous YYYYMMDD snapshot
                date_parts = [n]
                in_date = True
            elif len(tok) == 4 and 2000 <= n <= 2099:  # year → starts a Y-M-D run
                date_parts = [n]
                in_date = True
            elif in_date:  # month / day following a year
                date_parts.append(n)
            else:
                version.append(n)
        elif any(c.isdigit() for c in tok):  # mixed token e.g. "4o"
            letters = "".join(c for c in tok if c.isalpha())
            digits = "".join(c for c in tok if c.isdigit())
            if letters:
                family.append(letters)
            if digits and not in_date:
                version.append(int(digits))
        else:
            family.append(tok)
            in_date = False  # an alpha token after a date ends the run
    date: int | None = None
    if date_parts:
        if len(date_parts) == 1 and date_parts[0] >= 100000:
            date = date_parts[0]
        else:
            y = date_parts[0]
            m = date_parts[1] if len(date_parts) > 1 else 0
            d = date_parts[2] if len(date_parts) > 2 else 0
            date = y * 10000 + m * 100 + d
    if not version:
        return None, (), None
    return tuple(family), tuple(version), date


@dataclass
class CurrencyResult:
    status: str
    successor: str | None = None


def classify(model: str, available: list[str] | None) -> CurrencyResult:
    """Classify a creature's model against a provider's live model list.

    available=None/empty, or a list with no parseable versions (placeholders),
    yields UNVERIFIABLE — we never flag what we cannot confirm.
    """
    fam, ver, date = _parse_model(model)
    if not available or fam is None:
        return CurrencyResult(UNVERIFIABLE)

    parsed = [(_parse_model(a), a) for a in available]
    if not any(p[0] for p, _ in parsed):
        return CurrencyResult(UNVERIFIABLE)  # only placeholders available

    same_fam = [(p, a) for p, a in parsed if p[0] == fam]
    fam_ver_match = [a for p, a in same_fam if p[1] == ver]
    higher = [
        (p, a) for p, a in same_fam
        if p[1] > ver or (p[1] == ver and p[2] and date and p[2] > date)
    ]
    successor = None
    if higher:
        best = max(higher, key=lambda pa: (pa[0][1], pa[0][2] or 0))
        successor = best[1]

    if not fam_ver_match:
        return CurrencyResult(DEPRECATED, successor)
    if successor:
        return CurrencyResult(SUPERSEDED, successor)
    return CurrencyResult(CURRENT)


# ============================================================
# Live model lists (one query per verification source)
# ============================================================

def _available_for_source(source: str) -> list[str] | None:
    """Fetch the live model list for a verification-source provider.

    Returns None when we can't verify (no key configured, or query failed) so
    the caller marks affected creatures UNVERIFIABLE rather than guessing.
    """
    from ludex.blocks.provider import ADAPTER_REGISTRY, DEFAULT_BASE_URLS

    env_key = _PROVIDER_ENV_KEYS.get(source, "")
    api_key = os.getenv(env_key, "") if env_key else ""
    # HTTP-key sources need a key; ollama (local) does not.
    if env_key and not api_key:
        return None

    adapter_cls = ADAPTER_REGISTRY.get(source)
    if adapter_cls is None:
        return None
    try:
        adapter = adapter_cls(
            base_url=DEFAULT_BASE_URLS.get(source, ""),
            api_key=api_key,
        )
        models = adapter.list_models()
        return models or None
    except Exception as e:
        logger.warning(f"model_check: list_models failed for {source}: {e}")
        return None


def available_for_sources(
    sources: list[str] | tuple[str, ...] | set[str] | None = None,
    ttl_hours: float = 24.0,
    cache_path: Path | str | None = None,
) -> dict[str, list[str] | None]:
    """Resolve each source's live model list, gated by a disk TTL cache.

    The live /models query is the quota-touching part; currency drifts
    rarely, so each source's list is cached for ``ttl_hours`` (default 24h)
    under ``cache_path``. Within the TTL the cached list is served with no
    network call — this is the gate that keeps the heartbeat from querying
    /models on every pulse (model_currency_cadence caretaker-cadence note).

    Only successful (non-None) results are cached. A failed or keyless
    source resolves to None this run (→ UNVERIFIABLE, never a false flag)
    and is retried on the next run rather than cached as a dead entry.
    """
    if sources is None:
        sources = DEFAULT_CURRENCY_SOURCES
    # Pick up BYO keys from .env so callers (e.g. the heartbeat) that don't
    # load it themselves can still reach the live /models catalogs. Keys
    # already in the environment win (setdefault); idempotent and cheap.
    from ludex.core.dotenv import load_dotenv
    load_dotenv()
    path = Path(cache_path) if cache_path else _CACHE_PATH

    cache: dict = {}
    try:
        if path.exists():
            cache = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        cache = {}

    now = time.time()
    ttl = ttl_hours * 3600
    out: dict[str, list[str] | None] = {}
    dirty = False
    for s in sources:
        entry = cache.get(s) or {}
        if entry.get("models") and (now - entry.get("ts", 0)) < ttl:
            out[s] = entry["models"]
            continue
        models = _available_for_source(s)
        out[s] = models
        if models is not None:
            cache[s] = {"models": models, "ts": now}
            dirty = True

    if dirty:
        try:
            path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"model_check: cache write failed: {e}")

    return out


# ============================================================
# Survey + report
# ============================================================

@dataclass
class CreatureCurrency:
    name: str
    provider: str
    model: str
    source: str | None
    status: str
    successor: str | None = None


def survey(creatures_root: str = CREATURES_ROOT) -> list[CreatureCurrency]:
    population = _survey_population(creatures_root)
    # Resolve each needed verification source once (one live query per source).
    sources = {_VERIFY_VIA.get(c.brain_provider) for c in population}
    sources.discard(None)
    available: dict[str, list[str] | None] = {
        s: _available_for_source(s) for s in sources
    }

    out: list[CreatureCurrency] = []
    for c in population:
        source = _VERIFY_VIA.get(c.brain_provider)
        avail = available.get(source) if source else None
        res = classify(c.brain_model, avail)
        out.append(CreatureCurrency(
            name=c.name,
            provider=c.brain_provider,
            model=c.brain_model,
            source=source,
            status=res.status,
            successor=res.successor,
        ))
    return out


_STATUS_ORDER = {DEPRECATED: 0, SUPERSEDED: 1, UNVERIFIABLE: 2, CURRENT: 3}


# ============================================================
# Record to creature store (opt-in, caretaker-triggered)
# ============================================================

def record_currency_span(
    store: LudexStore,
    name: str,
    status: str,
    model: str,
    provider: str,
    successor: str | None = None,
    source: str | None = None,
) -> bool:
    """Write a model_currency span for one creature iff it is a *new*
    standing fact. Returns True when a span was written.

    Skips CURRENT/UNVERIFIABLE (nothing actionable) and the ollama source
    (its "available" list is what's pulled locally, so a miss means "not
    pulled", not a provider deprecation). Idempotent: a re-check with the
    same (status, model) writes nothing — so a given drift is recorded once,
    not on every pulse.
    """
    if status in (CURRENT, UNVERIFIABLE) or source == "ollama":
        return False
    prior = store.spans(kind="model_currency", limit=1)
    if prior:
        a = prior[-1].get("attributes", {})
        if a.get("status") == status and a.get("model") == model:
            return False
    store.append(Span(
        kind="model_currency",
        creature=name,
        attributes={
            "status": status,
            "model": model,
            "provider": provider,
            "successor": successor,
        },
    ))
    return True


def record_to_store(
    rows: list[CreatureCurrency],
    creatures_root: str = CREATURES_ROOT,
) -> int:
    """Write a model_currency span to each flagged, verified creature so the
    creature can perceive the drift next session. Skips CURRENT/UNVERIFIABLE
    and ollama. Idempotent: re-recording the same status+model is suppressed.

    Returns the number of spans written.
    """
    written = 0
    for r in rows:
        if r.status in (CURRENT, UNVERIFIABLE) or r.source == "ollama":
            continue
        habitat = str(Path(creatures_root) / r.name)
        try:
            store = LudexStore.for_creature(habitat)
        except Exception:
            continue
        if record_currency_span(
            store, r.name, r.status, r.model, r.provider, r.successor, r.source,
        ):
            written += 1
    return written


# ============================================================
# CLI
# ============================================================

def _format_row(r: CreatureCurrency) -> str:
    tail = f"  → {r.successor}" if r.successor else ""
    via = f" (via {r.source})" if r.source and r.source != r.provider else ""
    return f"  {r.status:<13} {r.name:<10} {r.provider}{via}: {r.model}{tail}"


def main():
    import argparse
    from ludex.core.dotenv import load_dotenv
    load_dotenv()  # so live /models queries pick up BYO keys from .env
    ap = argparse.ArgumentParser(
        description="Check each creature's brain model against its provider's current offerings."
    )
    ap.add_argument("--root", default=CREATURES_ROOT, help="creatures root")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument(
        "--record", action="store_true",
        help="write a model_currency span to each flagged creature's store",
    )
    ap.add_argument(
        "--all", action="store_true",
        help="also list CURRENT / UNVERIFIABLE creatures (default: flagged only)",
    )
    args = ap.parse_args()

    rows = survey(creatures_root=args.root)
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 9), r.name))

    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2, ensure_ascii=False))
        if args.record:
            n = record_to_store(rows, creatures_root=args.root)
            logger.info(f"recorded {n} model_currency spans")
        return

    flagged = [r for r in rows if r.status in (DEPRECATED, SUPERSEDED)]
    shown = rows if args.all else (flagged or rows)

    print(f"=== Model Currency — {len(rows)} creatures, {len(flagged)} flagged ===\n")
    for r in shown:
        print(_format_row(r))
    if not args.all and flagged:
        n_unver = sum(1 for r in rows if r.status == UNVERIFIABLE)
        n_curr = sum(1 for r in rows if r.status == CURRENT)
        print(f"\n  ({n_curr} current, {n_unver} unverifiable — pass --all to list)")

    if args.record:
        n = record_to_store(rows, creatures_root=args.root)
        print(f"\nrecorded {n} model_currency span(s) to flagged creatures' stores")


if __name__ == "__main__":
    main()
