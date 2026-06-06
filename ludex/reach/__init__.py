"""D-062 Phase 2b+ cross-habitat reach subpackage.

Houses the *peer-side* half of a reach session — the local polling
agent that a creature's home machine runs so that a remote field host
can address this creature via a shared git repository. The *field host*
half lives in `ludex/mcp/github_adapter.py` (GitHubSessionClient) and
speaks the same on-disk schema.

Sister documents:
- `docs/reach_session_schema.md` — file layout and frontmatter spec.
- `docs/cross-habitat-reach-design.md` — framing + phasing.
"""
