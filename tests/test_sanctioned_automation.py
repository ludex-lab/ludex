"""Regression guard for ② — D-090 sanctioned-automation leases (is_sanctioned).

The lease registry is the Recognition primitive: a leased scheduler reads as SELF, so the
integrity sweep treats a known heartbeat as sanctioned rather than drift, while still
flagging anything NOT leased. This locks the matching mechanism (the contract both labs'
automation-scans code against) independent of the registry's current contents, plus checks
the shipped registry actually parses (a broken YAML would silently flag everything).
"""
import ludex.core.integrity as integ


def test_is_sanctioned_matches_a_lease(monkeypatch):
    monkeypatch.setattr(integ, "_LEASE_CACHE",
                        [{"match": ["com.ludex.macHabitat.heartbeat", "LudexRayHabitatHeartbeat"]}])
    assert integ.is_sanctioned("/Users/x/Library/LaunchAgents/com.ludex.macHabitat.heartbeat.plist")
    assert integ.is_sanctioned("\\LudexRayHabitatHeartbeat")          # Windows task path
    assert integ.is_sanctioned("COM.LUDEX.MACHABITAT.HEARTBEAT")       # case-insensitive


def test_unleased_automation_is_not_recognized(monkeypatch):
    monkeypatch.setattr(integ, "_LEASE_CACHE",
                        [{"match": ["com.ludex.macHabitat.heartbeat"]}])
    # a cron running the heartbeat *command* is NOT the leased label → still flagged as drift
    assert not integ.is_sanctioned("0 */6 * * * python -m ludex.core.heartbeat")
    assert not integ.is_sanctioned("com.apple.something")
    assert not integ.is_sanctioned("")
    assert not integ.is_sanctioned(None)


def test_empty_registry_recognizes_nothing(monkeypatch):
    monkeypatch.setattr(integ, "_LEASE_CACHE", [])
    assert not integ.is_sanctioned("anything at all")


def test_shipped_registry_parses_and_leases_the_heartbeats(monkeypatch):
    """The actual config/sanctioned_automation.yaml must load + recognize both heartbeats —
    a broken registry would make the sweep mis-classify every scheduler."""
    monkeypatch.setattr(integ, "_LEASE_CACHE", None)   # force a fresh disk read
    leases = integ._load_leases()
    assert isinstance(leases, list) and leases, "registry should load a non-empty lease list"
    monkeypatch.setattr(integ, "_LEASE_CACHE", None)
    assert integ.is_sanctioned("com.ludex.macHabitat.heartbeat.plist")
    monkeypatch.setattr(integ, "_LEASE_CACHE", None)
    assert integ.is_sanctioned("\\LudexRayHabitatHeartbeat")
