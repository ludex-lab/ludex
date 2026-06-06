"""
Ludex — Modular Agent Block Library
Ludus + Ex: "From Play"

A biological approach to agent architecture.
Organs that connect, communicate, and maintain homeostasis.
"""

__version__ = "0.1.0"

from ludex.core.organism import Organism
from ludex.core.block import Block
from ludex.core.port import Port

__all__ = ["Organism", "Block", "Port"]
