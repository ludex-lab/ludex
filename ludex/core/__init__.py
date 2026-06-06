"""Core communication and assembly systems — the connective tissue of Ludex."""

from ludex.core.bus import Bus
from ludex.core.signals import Signals
from ludex.core.config import Config
from ludex.core.port import Port
from ludex.core.block import Block
from ludex.core.organism import Organism
from ludex.core.vitals import VitalSigns, TimeAwareness, EmotionalVitals
from ludex.core.membrane import Membrane

__all__ = [
    "Bus", "Signals", "Config", "Port", "Block",
    "Organism", "VitalSigns", "TimeAwareness", "Membrane",
]
