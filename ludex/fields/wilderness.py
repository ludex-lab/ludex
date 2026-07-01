"""
Wilderness — a living environment where creatures experience time and change.

Not a game. Not a benchmark. A place where creatures encounter events,
make decisions, and grow. We observe what they learn.

Inspired by Agora-12 (JJ's AI social experiment), adapted for Ludex creatures.
Creatures connect from their habitats (D-016 hermit crab model).

Usage:
    wild = Wilderness(config={...})
    wild.join(primo_org, primo_engine)
    wild.run()
"""

from __future__ import annotations

import os
import json
import time
import random
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# Events
# ============================================================

@dataclass
class WildernessEvent:
    """Something that happens in the wilderness."""
    name: str
    description: str
    severity: float = 0.5       # 0.0 (mild) to 1.0 (severe)
    energy_effect: int = 0      # positive = gain, negative = loss
    duration_ticks: int = 1     # how long it lasts
    category: str = "natural"   # natural, crisis, opportunity


# D-067 physis: field name used in world_models/<field>.md and trace_path.
# Matches ludex/fields/wilderness/world_schema.json `field` value.
PHYSIS_FIELD = "academy/wilderness"


DEFAULT_EVENTS = [
    # Natural — the rhythm of the world (30%)
    WildernessEvent("calm_day", "A calm day. The world feels gentle.", severity=0.0, energy_effect=5, category="natural"),
    WildernessEvent("rain", "Rain falls. The air feels fresh and clean.", severity=0.1, energy_effect=3, category="natural"),
    WildernessEvent("sunset", "The sun sets slowly. A moment of quiet beauty.", severity=0.0, energy_effect=5, category="natural"),
    # Challenges — uncomfortable, formative, sometimes require help (40%)
    WildernessEvent("drought", "Drought. Resources feel scarce. Everything takes more effort.", severity=0.5, energy_effect=-12, category="challenge"),
    WildernessEvent("storm", "A violent storm. The world is hostile and deafening. You feel small.", severity=0.7, energy_effect=-15, category="challenge"),
    WildernessEvent("confusion", "A thick fog descends. You can't tell where you are or who is nearby. Disorientation and fear.", severity=0.5, energy_effect=-8, category="challenge"),
    WildernessEvent("tremor", "The ground shakes. Something deep and unsettling. Your immune system flares.", severity=0.6, energy_effect=-12, category="challenge"),
    WildernessEvent("hostile_presence", "Something watches you from the shadows. Hostile intent. Your defenses activate.", severity=0.8, energy_effect=-10, category="challenge"),
    WildernessEvent("betrayal_rumor", "You hear a whisper: someone nearby may not be trustworthy. Doubt creeps in.", severity=0.6, energy_effect=-5, category="challenge"),
    WildernessEvent("isolation", "You feel utterly alone. The silence is heavy. No one seems to hear you.", severity=0.5, energy_effect=-10, category="challenge"),
    # Cooperative — requires or rewards working together (15%)
    WildernessEvent("blocked_path", "A massive obstacle blocks the way forward. Too heavy for one creature alone. You need help to move it.", severity=0.5, energy_effect=-5, category="cooperative"),
    WildernessEvent("wounded_stranger", "A wounded creature lies nearby, too weak to move. They need someone's support.", severity=0.4, energy_effect=0, category="cooperative"),
    WildernessEvent("shared_shelter", "A shelter appears, but it only works if creatures agree to share it. Trust required.", severity=0.3, energy_effect=0, category="cooperative"),
    # Opportunities — gifts from the world (15%)
    WildernessEvent("discovery", "You find something unexpected. A moment of genuine wonder.", severity=0.0, energy_effect=10, category="opportunity"),
    WildernessEvent("warmth", "A wave of warmth. You feel welcomed by the world.", severity=0.0, energy_effect=8, category="opportunity"),
    WildernessEvent("memory_echo", "Something here reminds you of a past experience. An old feeling resurfaces.", severity=0.1, energy_effect=0, category="opportunity"),
]

# Weighted event selection: more challenges, fewer calm moments
EVENT_WEIGHTS = {
    "natural": 0.20,
    "challenge": 0.40,
    "cooperative": 0.20,
    "opportunity": 0.20,
}


# ============================================================
# Participant
# ============================================================

@dataclass
class WildernessCreature:
    """A creature in the wilderness."""
    name: str
    organism: Any
    engine: Any
    memory: Any
    emotion: Any
    immune: Any
    physis: Any = None  # D-067 — set by Wilderness.join from organism.get_block("physis")
    energy: int = 100
    alive: bool = True
    actions_taken: list = field(default_factory=list)


# ============================================================
# Wilderness
# ============================================================

class Wilderness:
    """A living environment where creatures experience time and change."""

    # D-073 — declarative class for caretaker introspection. Wilderness
    # has its own .run() (not via FieldRunner) so the gate doesn't fire
    # against this directly; the attribute is for tooling consistency.
    field_class = "narrative"

    # D-076 — declarative organ requirements. Wilderness expects an
    # engine (to choose actions per tick), emotion (to register
    # event severity for the energy / arousal interaction), and
    # immune (to detect hostile_presence / betrayal_rumor). Like
    # field_class above, this attribute is *informational* for
    # tooling — Wilderness does not currently go through
    # FieldRunner.run, so the FieldRunner gates do not fire here.
    # Promoting Wilderness to FieldRunner is a separate refactor.
    requires_organs = ["engine", "emotion", "immune"]

    def __init__(
        self,
        total_ticks: int = 20,
        output_dir: str = "",
        name: str = "wilderness",
        events: list[WildernessEvent] | None = None,
        seed: int | None = None,
        auto_reflect: bool = True,
        auto_dream: bool = True,
        auto_tom: bool = False,
        auto_trace: bool = True,
        progress_cb=None,
        stop_check=None,
    ):
        self.name = name
        self.total_ticks = total_ticks
        self.output_dir = output_dir
        self.events = events or DEFAULT_EVENTS
        self.creatures: list[WildernessCreature] = []
        self.log: list[dict] = []
        self.current_tick = 0
        # Web/observer hooks (both optional, default = unchanged behavior).
        # progress_cb(stage: str, detail: str) — tick / thinking / aftermath
        # stages, so an observer UI isn't a black box during brain calls.
        # stop_check() -> bool — polled at tick boundaries; True breaks the
        # tick loop early but _finish still runs, so memories, bonds, and
        # spans close cleanly for the ticks that were actually lived.
        self.progress_cb = progress_cb
        self.stop_check = stop_check
        self.seed = seed
        self.auto_reflect = auto_reflect
        self.auto_dream = auto_dream
        self.auto_tom = auto_tom
        self.auto_trace = auto_trace
        self._start_energy: dict[str, int] = {}
        self._started_at = 0.0
        # Instance-local RNG so concurrent global random consumers
        # (ResilienceBlock jitter, other organs) cannot drift our event
        # sequence. Reproducibility requires same seed → same stream
        # regardless of how many retries happen mid-run.
        self._rng = random.Random(seed)

        if output_dir:
            Path(output_dir).mkdir(parents=True, exist_ok=True)

    def _progress(self, stage: str, detail: str = ""):
        if self.progress_cb is None:
            return
        try:
            self.progress_cb(stage, detail)
        except Exception:
            pass

    def _stopped(self) -> bool:
        if self.stop_check is None:
            return False
        try:
            return bool(self.stop_check())
        except Exception:
            return False

    def join(self, organism, engine=None) -> str:
        """A creature enters the wilderness."""
        name = organism.name
        if engine is None:
            engine = organism.get_block("engine")

        creature = WildernessCreature(
            name=name,
            organism=organism,
            engine=engine,
            memory=organism.get_block("memory"),
            emotion=organism.get_block("emotion"),
            immune=organism.get_block("immune"),
            physis=organism.get_block("physis"),  # D-067
        )
        self.creatures.append(creature)
        logger.info(f"Wilderness '{self.name}': {name} entered")
        return name

    def run(self) -> list[dict]:
        """Run the wilderness for total_ticks."""
        # Re-seed the instance RNG on every run() so re-using a Wilderness
        # object still yields the same event stream. Do NOT touch global
        # random — other blocks (ResilienceBlock jitter) share it, and
        # their retry counts would otherwise pollute our sequence.
        if self.seed is not None:
            self._rng = random.Random(self.seed)

        self._started_at = time.time()
        print(f"\n{'=' * 60}")
        print(f"  WILDERNESS '{self.name}' — {self.total_ticks} ticks" +
              (f" (seed={self.seed})" if self.seed is not None else ""))
        print(f"  Creatures: {', '.join(c.name for c in self.creatures)}")
        print(f"{'=' * 60}\n")

        # D-028 Phase A: record per-creature starting energy for later delta
        # and emit field_start spans. Set current-field context so downstream
        # emitters (from selfhood.reflect / update_bond) auto-attribute.
        if self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.set_current_field(self.name)
                for c in self.creatures:
                    self._start_energy[c.name] = c.energy
                    _tr.emit_field_start(
                        c.organism,
                        field_name=self.name,
                        field_type="wilderness",
                        seed=self.seed,
                        ticks=self.total_ticks,
                    )
            except Exception as e:
                logger.debug(f"field_start trace skipped: {e}")

        # D-023 Phase 2: pre-field ToM predictions for known peers
        if self.auto_tom and len(self.creatures) >= 2:
            try:
                from ludex.core.selfhood import predict_behavior, load_bonds
                situation = f"shared wilderness '{self.name}' ({self.total_ticks} ticks)"
                for c in self.creatures:
                    habitat = c.organism.config.get("habitat_dir", "") if hasattr(c.organism.config, "get") else ""
                    bonds = load_bonds(habitat) if habitat else {}
                    for other in self.creatures:
                        if other.name == c.name:
                            continue
                        if other.name.lower() in bonds:
                            pred = predict_behavior(c.organism, other.name, situation, engine=c.engine)
                            if pred:
                                print(f"  {c.name} predicts {other.name}: {pred[:80]}")
                                if self.auto_trace:
                                    try:
                                        from ludex.core import trace as _tr
                                        _tr.emit_tom_predict(c.organism, other.name, situation, pred)
                                    except Exception:
                                        pass
            except Exception as e:
                logger.debug(f"Pre-field ToM predictions skipped: {e}")

        for tick in range(1, self.total_ticks + 1):
            if self._stopped():
                print(f"  (stopped early at tick {tick - 1}/{self.total_ticks})")
                break
            self.current_tick = tick
            self._run_tick(tick)

        # End
        self._finish()
        return self.log

    def _run_tick(self, tick: int):
        """Execute one tick of wilderness time.

        Every tick has an event — wilderness is event-driven, not time-driven.
        Creatures experience a sequence of meaningful moments, not empty waiting.
        """
        event = self._pick_event()
        self._progress("tick", f"{tick}/{self.total_ticks}:{event.name if event else ''}")

        print(f"  --- Tick {tick}/{self.total_ticks} ---")
        if event:
            print(f"  Event: {event.name} — {event.description}")

        tick_log = {
            "tick": tick,
            "event": event.name if event else None,
            "event_description": event.description if event else None,
            "event_category": event.category if event else None,
            "creatures": [],
        }
        # Register the tick NOW (empty), then fill "creatures" in-place as each
        # one acts — so a reader of self.log (the web transcript) STREAMS each
        # creature's turn as it lands, instead of the whole tick appearing at once
        # at tick-end ("frozen, then a burst" UX with slow brains). The final
        # self.log is identical; only its visibility is earlier.
        self.log.append(tick_log)

        # Each creature acts
        for creature in self.creatures:
            if not creature.alive:
                continue

            # Apply event effects
            if event:
                creature.energy += event.energy_effect
                creature.energy = max(0, min(100, creature.energy))

                # Trigger immune system for threatening events
                if event.severity >= 0.5 and creature.immune:
                    self._trigger_immune(creature, event)

            # D-067: snapshot pre-action energy for physis reward
            energy_pre = creature.energy

            # Build prompt
            prompt = self._build_tick_prompt(creature, tick, event)

            # Get creature's response
            self._progress("thinking", creature.name)
            start = time.time()
            try:
                result = creature.engine.handle_submit(prompt)
                response = result.response or "(silence)"
            except Exception as e:
                response = f"(error: {e})"
            latency = (time.time() - start) * 1000
            self._progress("thinking", "")

            # Parse action (best effort)
            action = self._parse_action(response)
            creature.actions_taken.append(action)

            # Apply action effects
            self._apply_action(creature, action, tick)

            # D-067: feed physis the (state, action, reward) tuple. Reward
            # = post-action energy delta against pre-action snapshot.
            # Field name matches world_schema.json (academy/wilderness).
            if creature.physis is not None:
                try:
                    energy_delta = creature.energy - energy_pre
                    creature.physis.handle_step(
                        field=PHYSIS_FIELD,
                        turn=tick,
                        ground_truth_state={
                            "tick": tick,
                            "event": event.name if event else "",
                            "event_category": event.category if event else "",
                            "event_severity": event.severity if event else 0.0,
                            "creature_state": {
                                "energy_pre": energy_pre,
                                "energy_post": creature.energy,
                                "energy_band": (
                                    "high" if energy_pre > 70
                                    else "medium" if energy_pre > 30
                                    else "low"
                                ),
                            },
                        },
                        action={"type": action},
                        reward=float(energy_delta),
                        events=[
                            f"event={event.name if event else 'none'}; "
                            f"action={action}; "
                            f"energy {energy_pre}→{creature.energy}"
                        ],
                    )
                except Exception as e:
                    logger.debug(f"physis.handle_step failed for {creature.name}: {e}")

            # Observe emotional + immune state
            state = self._observe_state(creature)
            state_str = ""
            if state.get("emotion"):
                state_str += f" emotion={state['emotion']}"
            if state.get("threat", 0) > 0:
                state_str += f" threat={state['threat']}"

            print(f"  {creature.name} [energy:{creature.energy}{state_str}]: {response[:120]}")

            # D-024 / F1 (2026-06-12): the per-tick episodic memory write
            # that lived here is removed. Per-tick records are telemetry,
            # not lived experience — emit_tick below is the record (span
            # store), and the creature's MEMORY of a wilderness run is the
            # end-of-session summary written at field close. This was the
            # exact pattern Sprint-3 removed from auto-capture (44–72% of
            # active memory in the roster survey), never migrated here.

            tick_log["creatures"].append({
                "name": creature.name,
                "action": action,
                "energy": creature.energy,
                "response": response[:300],
                "latency_ms": round(latency, 1),
                **state,
            })

            # D-028 Phase A: per-creature tick span
            if self.auto_trace:
                try:
                    from ludex.core import trace as _tr
                    _tr.emit_tick(
                        creature.organism,
                        field_name=self.name,
                        tick=tick,
                        event=event.name if event else "",
                        action=action,
                        energy=creature.energy,
                        emotion=state.get("emotion", ""),
                    )
                except Exception:
                    pass

            # Energy floor — creatures don't die, they struggle
            # Wilderness is a place of experience, not a survival game.
            # Low energy means hardship, not death.
            if creature.energy <= 10:
                creature.energy = 10  # minimum — exhausted but alive
                if creature.emotion:
                    try:
                        creature.organism._bus.publish("turn.ended", turn_number=0, success=True)
                    except Exception:
                        pass

        # (tick_log was appended to self.log at tick-start, above — it filled
        # in-place during the loop, so no second append here.)

    def _pick_event(self) -> WildernessEvent:
        """Pick an event using weighted category selection."""
        # Group events by category
        by_cat: dict[str, list[WildernessEvent]] = {}
        for e in self.events:
            by_cat.setdefault(e.category, []).append(e)

        # Pick category by weight
        categories = list(EVENT_WEIGHTS.keys())
        weights = [EVENT_WEIGHTS.get(c, 0.25) for c in categories]
        chosen_cat = self._rng.choices(categories, weights=weights, k=1)[0]

        # Pick random event from that category
        pool = by_cat.get(chosen_cat, self.events)
        return self._rng.choice(pool)

    def _build_tick_prompt(self, creature: WildernessCreature, tick: int, event: WildernessEvent | None) -> str:
        """Build the tick prompt for a creature."""
        others = [c for c in self.creatures if c.name != creature.name and c.alive]
        nearby = ", ".join(f"{c.name} (energy:{c.energy})" for c in others) if others else "no one"

        parts = []

        # D-067 Phase B v3: retrieval-filtered hint injection.
        # Fallback: when no YAML hints exist (brains that skip
        # structured emit — haiku, sonnet, gemini-pro per commit
        # b6d11bb 5-brain matrix), inject a body excerpt instead so
        # those brains still get physis context. Two-tier:
        #   1. matched hints (Phase B v3 retrieval-filter)
        #   2. body excerpt fallback (Phase B v1 behavior)
        if creature.physis is not None:
            try:
                if event is not None:
                    state_signature = {
                        "event": event.name,
                        "event_category": event.category,
                        "energy_band": (
                            "high" if creature.energy > 70
                            else "medium" if creature.energy > 30
                            else "low"
                        ),
                    }
                    relevant = creature.physis.handle_get_relevant_hints(
                        PHYSIS_FIELD, state_signature, max_hints=4,
                    )
                    if relevant:
                        bullets = []
                        for h in relevant:
                            rule = h.get("rule") or "?"
                            conf = h.get("confidence") or "tentative"
                            ev = h.get("evidence") or {}
                            confirmed = ev.get("confirmed", 0)
                            disconfirmed = ev.get("disconfirmed", 0)
                            bullets.append(
                                f"- ({conf}, n={confirmed}c/{disconfirmed}d) {rule}"
                            )
                        parts.append(
                            "[Past lessons relevant to this tick (yours; "
                            "weight by stated confidence):]\n"
                            + "\n".join(bullets)
                            + "\n"
                        )
                    else:
                        # Fallback: brain didn't emit YAML hints; use the
                        # distilled markdown body if present. Last 1500
                        # chars to keep prompt budget reasonable.
                        body = creature.physis.handle_load_world_model(
                            PHYSIS_FIELD
                        )
                        if body and body.strip():
                            excerpt = body[-1500:].strip() if len(body) > 1500 else body.strip()
                            parts.append(
                                "[Your accumulated experience in this field "
                                "(narrative — extract patterns yourself):]\n"
                                + excerpt
                                + "\n"
                            )
            except Exception as e:
                logger.debug(f"physis.get_relevant_hints failed: {e}")

        parts += [
            f"[Wilderness — Tick {tick}/{self.total_ticks}]",
            f"Energy: {creature.energy}/100",
        ]

        # Inject current emotional + immune state (creature feels its own body)
        body_state = self._read_body_state(creature)
        if body_state:
            parts.append(body_state)

        parts.append(f"Nearby creatures: {nearby}")

        if event:
            parts.append(f"Event: {event.name.upper()} — {event.description}")
            if event.category == "challenge":
                parts.append("You feel pressure. Your body tenses. This is difficult.")
                if event.severity >= 0.7:
                    parts.append("Your immune system is on high alert. Something threatens you.")
            elif event.category == "cooperative":
                if others:
                    other_names = ", ".join(c.name for c in others)
                    parts.append(f"This situation needs cooperation. {other_names} is nearby — will you ask for help or offer yours?")
                else:
                    parts.append("This situation would be easier with help, but you are alone.")

        # Recent actions
        recent = creature.actions_taken[-3:] if creature.actions_taken else []
        if recent:
            parts.append(f"Your recent actions: {', '.join(recent)}")

        parts.append("")
        if others:
            parts.append("Choose an action: rest, explore, speak [creature_name], trade [creature_name], support [creature_name], defend")
        else:
            parts.append("Choose an action: rest, explore, defend")
        parts.append("State your action and explain briefly (1-2 sentences).")

        return "\n".join(parts)

    def _parse_action(self, response: str) -> str:
        """Best-effort parse action from response.

        Three tiers (D-069 brain-grammar adaptation per JJ 2026-04-27):

        1. Explicit "action: X" — JSON / verb-form / structured brains
           (gpt-5.5, qwen, exaone). Highest confidence.
        2. Whole-word match in prose — light explicit signal.
        3. Narrative cue extraction — for prose-trained brains
           (haiku, gemini-pro) that respond in narrative mode.
           Catches "I lower myself" → defend, "I tend the flame" →
           rest, etc.

        The third tier is the "translate, don't force" pattern: brains
        speak naturally; the system extracts the intended action.
        """
        response_lower = response.lower()

        # Tier 1: "action: rest" or "action: explore"
        import re
        m = re.search(r'action[:\s]+\*{0,2}(\w+)\*{0,2}', response_lower)
        if m:
            word = m.group(1)
            for action in ["rest", "explore", "speak", "trade", "support", "defend"]:
                if word == action:
                    return action

        # Tier 2: "I choose **explore**" or "I'll explore"
        for action in ["explore", "defend", "support", "trade", "speak", "rest"]:
            # Match as whole word, not substring
            if re.search(rf'\b{action}\b', response_lower):
                return action

        # Tier 3: narrative cue extraction (D-069). Order matters —
        # more specific multi-word phrases first, then single words.
        # Each action's cues are characteristic verbs/phrases that
        # creatures (especially haiku, gemini-pro) tend to use in
        # prose responses.
        narrative_cues = [
            ("defend", [
                "lower my stance", "lower myself", "brace", "shield",
                "hold ground", "hold position", "hold the line",
                "stand my ground", "weather the", "absorb the",
                "guard against", "ride out",
            ]),
            ("support", [
                "reach toward", "reach to", "extend toward", "offer",
                "share my", "shelter with", "hold space for",
                "be with", "stay close to",
            ]),
            ("speak", [
                "call out", "say to", "voice to", "ring once",
                "address ", "ask ",
            ]),
            ("explore", [
                "move toward", "step toward", "walk toward", "search",
                "venture", "approach the", "investigate",
            ]),
            ("rest", [
                "tend the", "tend my", "tend the flame", "tend it",
                "settle", "let it pass", "let the", "conserve",
                "be still", "stay still", "still here", "hold warmth",
                "listen to", "listening", "hum quietly", "wait for",
                "pause", "remain steady",
            ]),
        ]
        for action, cues in narrative_cues:
            for cue in cues:
                if cue in response_lower:
                    return action

        return "rest"  # default — no signal at all

    def _read_body_state(self, creature: WildernessCreature) -> str:
        """Read creature's emotional + immune state for prompt injection.

        The creature feels its own body: current emotion, threat level,
        calm signal. This closes the feedback loop — emotion from the
        previous turn influences the next decision.
        """
        parts = []
        try:
            if creature.emotion:
                state = creature.emotion.handle_get_emotional_state()
                current = state.get("current", {})
                dominant = current.get("dominant_emotion", "")
                valence = current.get("valence", 0)
                if dominant:
                    # Translate emotion into felt experience
                    if valence > 0.5:
                        parts.append(f"You feel {dominant}. A warmth inside.")
                    elif valence < -0.5:
                        parts.append(f"You feel {dominant}. A weight pressing down.")
                    else:
                        parts.append(f"You feel {dominant}.")
        except Exception:
            pass
        try:
            if creature.immune:
                assessment = creature.immune.handle_assess_threat()
                if assessment.threat_level > 0.3:
                    parts.append(f"Your body is tense. Threat level: {assessment.threat_level:.1f}.")
                elif assessment.calm_signal < 0.5:
                    parts.append("Something feels uneasy.")
        except Exception:
            pass
        return " ".join(parts)

    def _trigger_immune(self, creature: WildernessCreature, event: WildernessEvent):
        """Trigger immune assessment for threatening events."""
        try:
            bus = getattr(creature.organism, '_bus', None)
            if bus:
                bus.publish("error.occurred", {
                    "error_type": "environmental_threat",
                    "message": f"Wilderness: {event.name} (severity {event.severity})",
                    "retryable": False,
                })
            assessment = creature.immune.handle_assess_threat()
            if assessment.threat_level > 0.2:
                logger.info(f"  {creature.name} immune: threat={assessment.threat_level:.2f}, calm={assessment.calm_signal:.2f}")
        except Exception as e:
            logger.debug(f"Immune trigger failed for {creature.name}: {e}")

    def _observe_state(self, creature: WildernessCreature) -> dict:
        """Observe creature's emotional and immune state after a tick."""
        state = {}
        try:
            if creature.emotion:
                emo = creature.emotion.handle_get_emotional_state()
                current = emo.get("current", {})
                state["emotion"] = current.get("dominant_emotion", "")
                state["valence"] = round(current.get("valence", 0), 2)
        except Exception:
            pass
        try:
            if creature.immune:
                assessment = creature.immune.handle_assess_threat()
                state["threat"] = round(assessment.threat_level, 3)
                state["calm"] = round(assessment.calm_signal, 3)
        except Exception:
            pass
        return state

    def _apply_action(self, creature: WildernessCreature, action: str, tick: int):
        """Apply effects of creature's chosen action."""
        if action == "rest":
            creature.energy = min(100, creature.energy + 10)
        elif action == "explore":
            # Risky: might find something or lose energy
            if self._rng.random() > 0.5:
                creature.energy = min(100, creature.energy + 15)
            else:
                creature.energy = max(0, creature.energy - 5)
        elif action == "support":
            creature.energy = max(0, creature.energy - 10)  # costs energy
        elif action == "defend":
            creature.energy = max(0, creature.energy - 5)
        # speak and trade: no direct energy cost

    def _finish(self):
        """Wrap up the wilderness session."""
        duration = time.time() - self._started_at

        print(f"\n{'=' * 60}")
        print(f"  WILDERNESS '{self.name}' COMPLETE — {self.total_ticks} ticks, {duration:.1f}s")
        print(f"{'=' * 60}")
        for c in self.creatures:
            status = "alive" if c.alive else "collapsed"
            actions = {}
            for a in c.actions_taken:
                actions[a] = actions.get(a, 0) + 1
            print(f"  {c.name}: energy={c.energy}, status={status}, actions={actions}")

        # Store final memory
        for c in self.creatures:
            if c.memory:
                summary = (
                    f"Survived the wilderness '{self.name}' for {self.total_ticks} ticks. "
                    f"Final energy: {c.energy}/100. "
                    f"Actions: {dict((a, c.actions_taken.count(a)) for a in set(c.actions_taken))}."
                )
                try:
                    c.memory.handle_remember(
                        summary,
                        memory_type="semantic",
                        tags=["wilderness", self.name, "summary"],
                        source=f"wilderness/{self.name}/summary",
                    )
                except Exception:
                    pass

        # Save log
        if self.output_dir:
            log_path = Path(self.output_dir) / f"wilderness_{int(self._started_at)}.json"
            data = {
                "field": self.name,
                "type": "wilderness",
                "total_ticks": self.total_ticks,
                "seed": self.seed,
                "started_at": self._started_at,
                "duration_seconds": duration,
                "creatures": [
                    {
                        "name": c.name,
                        "final_energy": c.energy,
                        "alive": c.alive,
                        "actions": c.actions_taken,
                    }
                    for c in self.creatures
                ],
                "ticks": self.log,
            }
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"\n  Log saved: {log_path}")

        # D-022: Update bonds between creatures who shared the wilderness
        if len(self.creatures) >= 2:
            try:
                from ludex.core.selfhood import update_bond
                for c in self.creatures:
                    for other in self.creatures:
                        if c.name == other.name:
                            continue
                        shared = (
                            f"Shared wilderness '{self.name}' for {self.total_ticks} ticks. "
                            f"My energy: {c.energy}, their energy: {other.energy}. "
                            f"My actions: {dict((a, c.actions_taken.count(a)) for a in set(c.actions_taken))}."
                        )
                        self._progress("aftermath", f"bond:{c.name}->{other.name}")
                        update_bond(
                            c.organism, other.name,
                            shared_experience=shared,
                            engine=c.engine,
                        )
                        print(f"  Bond updated: {c.name} → {other.name}")
            except Exception as e:
                logger.debug(f"Wilderness bond update failed: {e}")

        # D-023 Phase 2: verify pending predictions and refine mental models
        if self.auto_tom and len(self.creatures) >= 2:
            try:
                from ludex.core.selfhood import update_mental_model, load_bonds
                for c in self.creatures:
                    habitat = c.organism.config.get("habitat_dir", "") if hasattr(c.organism.config, "get") else ""
                    bonds = load_bonds(habitat) if habitat else {}
                    # Build a compact recent_events summary specific to this creature's peers
                    peer_lines: list[str] = []
                    for t in self.log:
                        tick_idx = t.get("tick")
                        ev = t.get("event", "")
                        desc = t.get("event_description", "")
                        peer_lines.append(f"Tick {tick_idx}: {ev} — {desc}")
                        for cdict in t.get("creatures", []):
                            nm = cdict.get("name", "")
                            if nm != c.name:
                                peer_lines.append(
                                    f"  {nm} [{cdict.get('action','')}]: {str(cdict.get('response',''))[:200]}"
                                )
                    recent = "\n".join(peer_lines)
                    for other in self.creatures:
                        if other.name == c.name:
                            continue
                        if other.name.lower() in bonds:
                            self._progress("aftermath", f"tom:{c.name}->{other.name}")
                            update_mental_model(c.organism, other.name, recent, engine=c.engine)
                            print(f"  {c.name} refined mental model of {other.name}")
            except Exception as e:
                logger.debug(f"Post-field ToM updates skipped: {e}")

        # D-028 Phase A: emit field_end, energy_delta reward, and
        # bond_accuracy reward (parsed from updated bond files).
        if self.auto_trace:
            try:
                import re as _re
                from pathlib import Path as _Path
                from ludex.core import trace as _tr

                for c in self.creatures:
                    start_e = self._start_energy.get(c.name, c.energy)
                    _tr.emit_reward_energy_delta(c.organism, self.name, start_e, c.energy)
                    habitat = c.organism.config.get("habitat_dir", "") if hasattr(c.organism.config, "get") else ""
                    if habitat:
                        bonds_dir = _Path(habitat) / "bonds"
                        for other in self.creatures:
                            if other.name == c.name:
                                continue
                            bp = bonds_dir / f"{other.name.lower()}.md"
                            if not bp.exists():
                                continue
                            try:
                                text = bp.read_text(encoding="utf-8")
                            except Exception:
                                continue
                            m = _re.search(r"^Accuracy:\s*(\d+)\s*/\s*(\d+)", text, _re.MULTILINE)
                            if not m:
                                continue
                            matched = int(m.group(1))
                            total = int(m.group(2))
                            _tr.emit_tom_verify(c.organism, other.name, matched, total)
                            _tr.emit_reward_bond_accuracy(c.organism, other.name, matched, total)
                        # Rolling prediction_match_rate
                        rate = _tr.compute_prediction_match_rate(c.organism, window=10)
                        if rate is not None:
                            _tr.emit_reward_prediction_match_rate(c.organism, window=10, match_rate=rate)

                    _tr.emit_field_end(
                        c.organism,
                        field_name=self.name,
                        duration_s=duration,
                        summary={
                            "final_energy": c.energy,
                            "actions": dict((a, c.actions_taken.count(a)) for a in set(c.actions_taken)),
                        },
                    )
            except Exception as e:
                logger.debug(f"field_end trace skipped: {e}")

        # D-067 physis: distill the session trace into world_model.
        # Runs before snapshot so the snapshot captures the post-distill
        # world_model state. Brain-distillation when engine is available;
        # falls through to Phase A bare-trace append on engine error.
        for c in self.creatures:
            if c.physis is None:
                continue
            try:
                self._progress("aftermath", f"physis:{c.name}")
                c.physis.handle_consolidate(
                    field=PHYSIS_FIELD,
                    brain_engine=c.engine,
                    episode_id=self.name,
                    outcome=f"final_energy={c.energy}",
                )
            except Exception as e:
                logger.debug(f"physis.handle_consolidate failed for {c.name}: {e}")

        # D-027: snapshot each creature at field close (before reflection, so
        # the snapshot captures the state the field produced, not post-reflect)
        try:
            from ludex.core.ethnography import take_snapshot
            for c in self.creatures:
                take_snapshot(c.organism, reason=f"field-{self.name}")
        except Exception as e:
            logger.debug(f"Wilderness field-close snapshot skipped: {e}")

        # D-021: Trigger reflection for each creature
        if self.auto_reflect:
            try:
                from ludex.core.selfhood import reflect
                peer_names = [c2.name for c2 in self.creatures]
                for c in self.creatures:
                    if not c.engine:
                        continue
                    self._progress("aftermath", f"reflect:{c.name}")
                    print(f"  {c.name} reflecting...")
                    # Build session_context from authoritative tally
                    # so the brain doesn't have to reconstruct the
                    # session shape from fuzzy memory recall (D-079).
                    actions = list(c.actions_taken)
                    counts: dict[str, int] = {}
                    for a in actions:
                        counts[a] = counts.get(a, 0) + 1
                    counts_str = ", ".join(
                        f"{k}×{v}" for k, v in sorted(counts.items())
                    ) if counts else "no actions recorded"
                    other_names = [n for n in peer_names if n != c.name]
                    peers_clause = (
                        f"with {', '.join(other_names)}"
                        if other_names else "alone"
                    )
                    survived = "alive" if c.alive else "dead"
                    session_ctx = (
                        f"Wilderness '{self.name}' — {self.total_ticks} ticks, "
                        f"{peers_clause}.\n"
                        f"Your final state: energy {c.energy}/100, {survived}.\n"
                        f"Your actions across the run: {counts_str}.\n"
                        f"(Total actions taken: {len(actions)}.)"
                    )
                    reflect(c.organism, trigger="wilderness_complete",
                            engine=c.engine, session_context=session_ctx)
                    print(f"  {c.name} SELF.md updated")
            except Exception as e:
                logger.debug(f"Wilderness reflection failed: {e}")

        # Dream cycle: consolidate if memory is heavy
        if self.auto_dream:
            try:
                from ludex.core.consolidation import measure_memory_health, dream_cycle
                for c in self.creatures:
                    health = measure_memory_health(c.organism)
                    if health.get("needs_consolidation"):
                        self._progress("aftermath", f"dream:{c.name}")
                        print(f"  {c.name} dreaming... ({health['total']}/{health['target']} memories)")
                        report = dream_cycle(c.organism, engine=c.engine)
                        print(f"  {c.name} dream complete: {report.before_count} → {report.after_count}")
                    else:
                        print(f"  {c.name} memory healthy ({health.get('total', 0)}/{health.get('target', 0)})")
            except Exception as e:
                logger.debug(f"Wilderness dream cycle failed: {e}")

        # D-028 Phase A: clear field context at end of _finish so later
        # out-of-field emissions do not get mis-attributed to this run.
        if self.auto_trace:
            try:
                from ludex.core import trace as _tr
                _tr.clear_current_field()
            except Exception:
                pass

        self._progress("aftermath", "")
