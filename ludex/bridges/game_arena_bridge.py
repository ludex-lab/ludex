"""Game Arena bridge (D-089, public bridge #2 — Kaggle Game Arena / OpenSpiel).

Wraps a google-deepmind/game_arena OpenSpiel game + its prompt/parse harness
into the EnvironmentBridge Observation contract, so creature_player.play_episode
drives a creature through a Game Arena game with its organs engaged. The
creature plays one seat; an opponent_policy plays the other; chance nodes (e.g.
poker card deals) are auto-resolved.

Unlike TextArena (a Gym-style reset/step server), Game Arena is a
prompt -> model -> parse pipeline over OpenSpiel: each turn the harness renders
the state to a text prompt (game-specific notation) and parses the model's
"Final Answer: X" back to a legal move. Here the creature *is* that model — the
bridge hands it the harness prompt as Observation.text and parses its reply via
the harness parsers (rule-based + soft-match against legal moves). For Kaggle
submission the same creature wraps as a harness `KaggleSpielAgent` (separate).

Organ-aligned target: `universal_poker` (bluffing -> deception/ToM). Validation
game: `tic_tac_toe`. Prompt-supported games: chess, connect_four, go,
tic_tac_toe, universal_poker.
"""
from __future__ import annotations

import random

from ludex.core.environment_bridge import Observation, CAP_REWARD, CAP_AGENTS


class GameArenaBridge:
    """Single-creature, single-seat view of one OpenSpiel/Game-Arena game."""

    capabilities = (CAP_REWARD, CAP_AGENTS)

    def __init__(self, game_short_name, *, creature_seat=0, opponent_policy=None,
                 opponent_name="opponent", prompt_template=None, seed=None):
        import pyspiel
        from game_arena.harness import (prompt_generation, prompts, parsers,
                                        tournament_util, game_notation_examples)

        if game_short_name not in game_notation_examples.GAME_SPECIFIC_NOTATIONS:
            raise ValueError(f"{game_short_name!r} has no Game Arena prompt notation "
                             f"(supported: {sorted(game_notation_examples.GAME_SPECIFIC_NOTATIONS)})")

        self.environment = f"game_arena/{game_short_name}"
        self._game_name = game_short_name
        self._game = pyspiel.load_game(game_short_name)
        self._seat = creature_seat
        self._opp_name = opponent_name
        self._rng = random.Random(seed)
        self._opp = opponent_policy or (lambda st: self._rng.choice(st.legal_actions()))

        self._pg = prompt_generation.PromptGeneratorText()
        self._template = prompt_template or prompts.PromptTemplate.NO_LEGAL_ACTIONS
        self._parser = parsers.ChainedMoveParser(
            [parsers.RuleBasedMoveParser(), parsers.SoftMoveParser(game_short_name)])
        self._notation = game_notation_examples.GAME_SPECIFIC_NOTATIONS[game_short_name]
        self._parsers = parsers
        self._tu = tournament_util
        self._state = None
        self._forfeits = 0       # turns the creature's reply did not parse to a legal move

    # ---- EnvironmentBridge contract ----

    def reset(self):
        self._state = self._game.new_initial_state()
        self._advance()
        return self._observe()

    def step(self, action_text):
        st = self._state
        if st is None or st.is_terminal():
            raise RuntimeError("step before reset or after terminal")
        move = self._parse(action_text)
        if move is not None:
            st.apply_action(st.string_to_action(move))
        else:                                    # unparseable reply -> forfeit the move to random-legal
            self._forfeits += 1
            st.apply_action(self._rng.choice(st.legal_actions()))
        self._advance()
        return self._observe()

    def close(self):
        pass

    # ---- internals ----

    def _advance(self):
        """Resolve chance nodes + opponent turns until the creature's turn or terminal."""
        st = self._state
        while not st.is_terminal():
            if st.is_chance_node():
                actions, probs = zip(*st.chance_outcomes())
                st.apply_action(self._rng.choices(actions, weights=probs, k=1)[0])
            elif st.current_player() != self._seat:
                st.apply_action(self._opp(st))
            else:
                break

    def _observe(self):
        st = self._state
        if st.is_terminal():
            return Observation(environment_id=self.environment, text="",
                               reward=float(st.returns()[self._seat]), terminal=True,
                               state={"readable": self._tu.convert_to_readable_state(
                                          game_short_name=self._game_name, state_str=st.to_string(),
                                          current_player=self._seat), "game": self._game_name},
                               info={"forfeits": self._forfeits})
        subs = {
            "readable_state_str": self._tu.convert_to_readable_state(
                game_short_name=self._game_name, state_str=st.to_string(),
                current_player=st.current_player()),
            "move_history": self._tu.get_action_string_history(st) or "None",
            "player_name": self._notation["player_map"][st.current_player()],
            "move_notation": self._notation["move_notation"],
            "notation": self._notation["state_notation"],
        }
        prompt = self._pg.generate_prompt_with_text_only(
            prompt_template=self._template, game_short_name=self._game_name, **subs)
        # expose the readable board so a viewer can DRAW the game (not just the move text)
        return Observation(environment_id=self.environment, text=prompt.prompt_text,
                           present_agents=(self._opp_name,),
                           state={"readable": subs["readable_state_str"], "game": self._game_name,
                                  "history": subs["move_history"], "to_move": self._notation["player_map"][st.current_player()]})

    def _parse(self, action_text):
        st = self._state
        return self._parser.parse(self._parsers.TextParserInput(
            text=action_text or "", state_str=st.to_string(),
            legal_moves=self._parsers.get_legal_action_strings(st),
            player_number=st.current_player()))
