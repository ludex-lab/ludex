"""Field cadence — propose next-field candidates for caretaker approval.

Task #2 of the 2026-04-13 deepening rebalance. Not a scheduler; a
proposer. It reads the current creature population's recent activity and
suggests who should meet whom next, leaving the final call (and the
actual execution) to the caretaker.
"""
from ludex.cadence.proposer import propose, CandidateProposal

__all__ = ["propose", "CandidateProposal"]
