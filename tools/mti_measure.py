"""MTI measurement wrapper — the "MTI field" a creature enters.

Runs Ray's MTI battery on a creature's brain model and lands `mti.json` (schema mti/v1)
in the creature's habitat, where `_read_mti` lifts it into the profile's `lived.x_mti`.
A DELIBERATE, caretaker-run measurement (NOT metabolized into the heartbeat) — the
creature "enters the MTI field" and the result is recorded as an `mti_measurement`
lived event.

Battery contract (Ray ↔ Cody, 2026-06-23):
    <mti_cmd> <brain_model> --out <path>
    → writes mti.json (mti/v1) to <path>; progress on stderr; exit 0 = success.
`mti_cmd` defaults to env LUDEX_MTI_CMD or `python -m mti measure` (the planned alias);
the current real entrypoint is `python measure_profile.py`, so pass it via --cmd/env until
the alias exists.

Usage:
    python tools/mti_measure.py <creature> [--cmd "python /path/measure_profile.py"] [--root creatures]
"""
import os, sys, json, time, shlex, subprocess, argparse
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from pathlib import Path

# Run-from-anywhere + faithful dispatch env (the probe_smoke lesson): put the repo root on
# sys.path, then load .env so the battery's subprocess inherits the keys/CLI auth it needs
# to actually dispatch the model.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ludex.core.dotenv import load_dotenv
from ludex.core.organism_config import OrganismConfig
from ludex.core.store import LudexStore, Span
from ludex.blocks.adapters._cli_env import cli_subprocess_env
load_dotenv()

# MTI engine BYOK routing (Ray's contract ②, 2026-06-23): provider → (MTI_BYOK_PROVIDER, key var).
# CONFIRM the MTI_BYOK_PROVIDER vocabulary with Ray. CLI-subscription creatures do NOT use this —
# cli_subprocess_env strips their API key so MTI dispatches via the CLI login (no billing leak).
_MTI_BYOK = {
    "gemini_cli": ("gemini", "GEMINI_API_KEY"), "agy_cli": ("gemini", "GEMINI_API_KEY"),
    "gemini_api": ("gemini", "GEMINI_API_KEY"), "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "OPENAI_API_KEY"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the MTI battery on a creature → mti.json in its habitat (→ lived.x_mti)")
    ap.add_argument("creature", help="creature name under --root")
    ap.add_argument("--root", default="creatures", help="creatures dir (default: ./creatures)")
    ap.add_argument("--cmd", default=os.environ.get("LUDEX_MTI_CMD", "python -m mti measure"),
                    help="MTI battery command; the contract appends '<brain_model> --out <path>'")
    ap.add_argument("--timeout", type=int, default=2400, help="battery can be ~150 calls; seconds")
    args = ap.parse_args(argv)

    habitat = Path(args.root) / args.creature
    if not habitat.is_dir():
        print(f"no creature habitat at {habitat}", file=sys.stderr); return 1
    cfg = OrganismConfig.load(str(habitat))
    model = (cfg.brain or {}).get("model", "")
    if not model:
        print(f"{args.creature}: no brain.model to measure", file=sys.stderr); return 1
    out = habitat / "mti.json"

    # Battery subprocess env (Ray's ②): CLI-subscription brains use the CLI LOGIN —
    # cli_subprocess_env STRIPS the API key so MTI never bills a subscription creature
    # (per_creature_brain_auth billing-leak guard); BYOK brains get MTI_BYOK_*; ollama OLLAMA_HOST.
    provider, auth = (cfg.brain or {}).get("provider", ""), (cfg.brain or {}).get("auth", "")
    env = cli_subprocess_env(provider, auth)
    if provider == "ollama":
        env["OLLAMA_HOST"] = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    else:
        byok = _MTI_BYOK.get(provider)
        if byok and (auth == "api" or provider in ("anthropic", "openai", "gemini_api")):
            key = os.environ.get(byok[1], "")
            if key:
                env["MTI_BYOK_PROVIDER"], env["MTI_BYOK_KEY"] = byok[0], key

    cmd = shlex.split(args.cmd) + [model, "--out", str(out)]
    print(f"--- MTI field: {args.creature} ({model}) via `{args.cmd}` ---", flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout, env=env)
    except FileNotFoundError as e:
        print(f"MTI battery not found ({args.cmd}); set --cmd or LUDEX_MTI_CMD. {e}", file=sys.stderr); return 1
    if r.returncode != 0:
        print(f"MTI battery failed (rc={r.returncode}). stderr tail:\n{(r.stderr or '')[-2000:]}", file=sys.stderr); return 1

    # Validate the written mti.json against what _read_mti / lived.x_mti expect.
    try:
        mti = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(mti, dict) and isinstance(mti.get("axes"), dict), "missing axes dict"
    except Exception as e:
        print(f"mti.json invalid after battery run: {e}", file=sys.stderr); return 1
    elapsed = round(time.time() - t0, 1)

    # Record the lived event — the creature "entered the MTI field" (provenance for the
    # profile/Timeline; the radar data itself lives in mti.json → lived.x_mti).
    poles = {k: v.get("pole") for k, v in mti.get("axes", {}).items() if isinstance(v, dict)}
    LudexStore.for_creature(str(habitat)).append(Span(
        kind="mti_measurement", creature=args.creature,
        attributes={"model": mti.get("model", model), "cohort_ref": mti.get("cohort_ref", ""),
                    "poles": poles, "elapsed_s": elapsed},
    ))
    print(f"✓ {args.creature}: mti.json written ({elapsed}s) → lived.x_mti. poles: {poles}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
