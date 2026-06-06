"""Opsis acquisition + interpretation adapters (D-048 Phase A).

Each adapter implements one of:
- Acquisition: get raw image bytes / frames from a source
- Interpretation: convert image → text description for Logos fallback

Kept as flat modules (not classes) for Phase A simplicity.
"""
