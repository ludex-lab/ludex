"""D-072 Phase A pillar 1 smoke test.

Probes a creature's brain capabilities and prints the result
*without* persisting to disk (we save the original config and
restore at the end). Run:

    python tools/probe_smoke.py <creature-path>
"""
import os, sys, time, logging, shutil
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

from pathlib import Path

# Run-from-anywhere: put the repo root on sys.path so `python tools/probe_smoke.py`
# works per the docstring above. A plain script run puts tools/ on sys.path[0]
# (not the repo root), so `import ludex` would fail; `-m` invocations are unaffected.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ludex.core.dotenv import load_dotenv
from ludex.core.organism_config import OrganismConfig
from ludex.core.birth_probe import probe_brain_capabilities, capability_set

# Faithfully match the live brain path: gemini_cli/agy_cli with auth:api need their
# billing key in os.environ. The web server, heartbeat, and CLI all load it from
# .env first; without this the probe reports a FALSE failure for those CLI brains
# (observed 2026-06-22 — a smoke false-negative right after the Gemini-CLI
# deprecation looked like an outage but was only this missing call).
load_dotenv()

if len(sys.argv) < 2:
    print("usage: probe_smoke.py <creature-path>")
    sys.exit(2)

path = Path(sys.argv[1]).resolve()
print(f"--- probing {path} ---", flush=True)

# Snapshot ludex.yaml so the probe doesn't pollute the creature.
yaml_path = path / "ludex.yaml"
backup = yaml_path.read_text(encoding="utf-8") if yaml_path.exists() else ""

cfg = OrganismConfig.load(str(path))
provider = cfg.brain.get("provider", "")
model = cfg.brain.get("model", "")
print(f"brain: {provider}:{model}", flush=True)

t0 = time.time()
snap = probe_brain_capabilities(
    provider_name=provider,
    model=model,
    cwd=str(path),
    timeout_ms=30000,
)
print(f"probe elapsed: {time.time()-t0:.2f}s", flush=True)
print(f"snapshot: {snap}", flush=True)
print(f"capability_set: {capability_set(snap)}", flush=True)

# Restore in case any save() ran as a side effect.
if backup:
    yaml_path.write_text(backup, encoding="utf-8")
print("--- restored ---", flush=True)
