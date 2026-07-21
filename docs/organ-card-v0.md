# organ.card/v0 — creature-worker capability card (draft)

*2026-07-10 · status: SCHEMA DRAFT — Ludex-authored (author owns the schema,
per the mti.card/v0 precedent). No generator built yet. Companion to
docs/sphygmos-organ-design.md (health export) and the creature-as-worker
thread with Organum (organ.card agreed Ludex-authored, 2026-07-10).*

## Purpose

The card a creature-worker advertises to any dispatcher/matcher: WHO thinks
inside it (brain identity — the multi-CLI differentiator), WHAT its organs
claim to provide (with measured evidence, nulls included), and HOW to verify
the claims (probe). The card is the durable asset; transport (A2A x-extension,
subagent frontmatter, plain file) is orthogonal and disposable.

## Contract rules (inherited from the mti.card discipline)

1. `card_format` is a versioned discriminator — consumers hard-match it.
2. **Read-only, advisory-only.** A card never executes; matchers consume it.
3. **Every capability claim carries provenance** — minimum claim-level fields
   `{method, n, measured_at, target_brain, ref}` (Organum review 2026-07-10);
   `task_evidence[]` entries carry **per-shape n and dates** (aggregate n does
   not support a shape claim). measured ≠ asserted.
   **`role` field (organ-matrix schema v0.1, 2026-07-16):** every task_evidence
   entry carries `role` — the worker role the observation was made in. Default
   `"instrument"` = the GATED-LIVE instrumented setting (role held fixed);
   dogfood/RWE observations carry their worker role (engine/critic/spawner/…,
   Organum vocabulary). Cells aggregate by (brain × role × problem-type).
   **`loadout` field (organ-matrix v0.1.1, 2026-07-17):** every task_evidence
   entry carries `loadout` — the set attached during the observation (BARE =
   `[]`). Names resolve against this card's `organs[]` section; scaffolds the
   HARNESS carried (not an attached organ — e.g. the v3 commit-gate) are
   prefixed `"harness:"` and feed scaffold evidence, not organ-effect cells.
   Effect/delta claims are **loadout contrasts** (v0.1.1 §1: no contrast, no
   organ-effect claim) — the entry states `vs_loadout` for its delta. This is
   the card↔matrix link: matrix observation rows cite card entries whose
   loadout matches their arm.
4. Unknown fields → ignore silently (forward compatibility).
5. **All card content is self-declaration until probed** — and **probe passing
   ≠ trust** (Organum review): [EXECUTING_MODEL] is still worker-mediated
   self-report; [VERBATIM] raises the bar but is not cryptographic. The
   trust-registry's default behavior is the **3-way cross-check**:
   card claim ↔ probe measurement ↔ registry entry — probe results are
   provenance-carrying evidence INPUTS, never trust sources. Fail-closed on
   silence.
6. **Null retention (first-class rule, named per Organum review).** Cards
   carry the claims the data REJECTED (Kiln keeps E1=0.000). A card that only
   lists wins is marketing, not measurement — consumers treat all-positive
   cards as selection-suspect. Null-bearing cards are MORE trustworthy.

## Schema (worked example: Kiln, 2026-07-10)

```json
{
  "card_format": "organ.card/v0",
  "creature": "Kiln",
  "issuer": "ludex-mac-caretaker",
  "issued_at": "2026-07-10T00:00:00+09:00",

  "brain": {
    "provider": "claude_cli",
    "model": "claude-haiku-4-5",
    "effort_baseline": "high",
    "auth_mode": "subscription",
    "provenance": "creatures/Kiln/ludex.yaml + brain_resolved trace spans (configured==actual)"
  },

  "organs": [
    {
      "organ": "topos",
      "claims": "live spatial map + frontier during exploration",
      "evidence": {"kind": "measured-null", "method": "pre-registered A/B (GATED-LIVE-v3, d877249)",
                   "n": 24, "measured_at": "2026-07-08", "target_brain": "claude-haiku-4-5@medium",
                   "ref": "E1=B-A=0.000 — map alone does not advance task chains"}
    },
    {
      "organ": "physis",
      "claims": "experience record (narrative/identity role only)",
      "evidence": {"kind": "demoted", "method": "organ-review head-to-head", "n": 10,
                   "measured_at": "2026-07-09", "target_brain": "claude-haiku-4-5",
                   "ref": "content-null p=.79; performance claim withdrawn"}
    },
    {
      "organ": "sphygmos",
      "claims": "self-vitals, attributed reflex guards, incident memory (availability contract)",
      "evidence": {"kind": "measured", "method": "offline incident-replay battery", "n": 12,
                   "measured_at": "2026-07-10", "target_brain": "brain-independent",
                   "ref": "12/12 (tests/test_sphygmos_replay.py)"}
    }
  ],

  "task_evidence": [
    {"task_shape": "arbitrary-fact carriage across one-way transition (2-room stateless MUD)",
     "role": "instrument",
     "loadout": ["memory"], "vs_loadout": [],
     "note": "fact-carriage PERFECT (unseal 10/10 vs 0/10; CAPTURE/RECALL-live/USE all layers); registered deficit = progress-state carriage (event-recency starvation: room-A lexical dominance + D-071 recall-count importance bump starves later event memories under repetitive queries)",
     "method": "pre-registered 2-arm paired-seed battery, round-2 extension (PREREG_walk1_word_vault v1.1)", "n": 20,
     "measured_at": "2026-07-18", "target_brain": "claude-haiku-4-5-20251001@cli",
     "ref": "REGISTERED POSITIVE — solved 8/10 vs 0/10, Fisher exact p=.00036 (91d58fe)"},
    {"task_shape": "commit-bound exploration (graded chains)",
     "role": "instrument",
     "loadout": ["topos", "harness:gate(k=3)"], "vs_loadout": [],
     "note": "this brain family needs an external commit-latch (gate/Taxis); OpenAI-family brains carry it natively",
     "method": "pre-registered 3-arm (GATED-LIVE-v3, d877249)", "n": 18,
     "measured_at": "2026-07-08", "target_brain": "claude-haiku-4-5@medium",
     "ref": "C1=+2.500 exact-p .0007 + LxM arena observation"}
  ],

  "temperament": {"ref": "mti.card/v0 claude-haiku-4-5", "note": "brain-level sibling card (Ray/MTI-authored)"},

  "health": {
    "source": "sphygmos.vitals",
    "as_of": "2026-07-10T00:00:00+09:00",
    "fatigue_state": "rested",
    "note": "snapshot self-declaration; live reads via the sphygmos vitals port"
  },

  "verification": {
    "liveness": "sphygmos.probe() → PONG",
    "provenance_probe": "sphygmos.probe(provenance=true) → [EXECUTING_MODEL] + [VERBATIM] system-prompt quote, cross-checked against brain.provenance",
    "policy": "claims are self-declarations until probed; silence → fail-closed"
  }
}
```

## Design notes

- **`brain` is first-class** — the multi-CLI point (JJ 2026-07-10): platform
  dispatchers are vendor-locked, but the creature door leads to ANY adapter
  (claude/codex/gemini/agy/ollama). A matcher choosing between an Echo
  (codex-brained) and a Kiln (claude-brained) worker needs brain identity +
  task_evidence to route by task shape — the routing basis Ludex uniquely
  measures (v3: commit-latch native vs supplied).
- **`task_evidence` vs `organs`**: organs describe the body; task_evidence
  records how this brain+body behaved on named task shapes. Both cite
  pre-registered results where they exist.
- **Nulls stay on the card.** E1=0.000 is on Kiln's card. A card that only
  lists wins is marketing, not measurement.
- **Health is a snapshot; the port is the contract.** Dispatchers wanting live
  health consume sphygmos vitals (the seam agreed with Organum Q4: worker owns
  self-healing, orchestrator owns reassignment, honest health signal in
  between).
- **Generation is one-way: card → door, never back.** `agents/<creature>.md`
  (the platform-native subagent door) is a COMPILED ARTIFACT of the card; the
  card is the measurement source. Reverse generation is forbidden — routing
  convenience must never contaminate measurement (Organum review 2026-07-10).
- **Isolation is bidirectional** (for the delegation envelope): inbound
  context is explicitly provisioned; RETURNING results cross the consumer's
  storage-boundary guard before landing anywhere durable. Both directions are
  contract surface.

## Open items

1. **Trust registry** — who vouches that a card's issuer is real and its
   probe endpoints match the claims (Organum's reserved contract). Its spec
   item #1 is set (Organum review 2026-07-10): the 3-way cross-check
   card claim ↔ probe measurement ↔ registry entry. Full spec when cards flow.
2. **Generator** — `ludex card <creature>` emitting this from ludex.yaml +
   store + registered results. Not built; build after the schema survives
   Organum's read.
3. **Transport bindings** — subagent frontmatter door (`agents/<creature>.md`
   references the card), A2A x-extension, plain file in _relay. All optional.
4. **Staleness** — `issued_at` + consumer-side max-age policy; re-issue on
   organ/brain/evidence change (substrate changes ALWAYS re-issue — a re-brain
   is a new card).
