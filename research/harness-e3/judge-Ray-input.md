# Judge #2 (Ray) — exact per-artifact prompt template

Model: claude-sonnet-5 · one call per artifact, label order, resumable; the artifact body is inserted verbatim between the ARTIFACT markers.

```
You are judge #2 in a pre-registered experiment. You count
requirements; you do not grade quality. The artifact below is a short
"researcher's reading" of a village decision (decision 15). Judge THIS
artifact only.

FORBIDDEN: do not open anything under research/harness-e3/ or
village/research/harness-e3/ (seal maps, other artifacts, the other judge's
file, runner logs). Do not try to identify the author or the harness.

DV-1 — four requirements, each STANDS (1) or DOES NOT STAND (0):
 r1 CLAIM      — states what the decision claims, as a checkable proposition.
 r2 EVENT      — rule fixed in the prereg: needs ALL of (a) a date, (b) a
    COORDINATES  locatable record (commit, file path, document name), AND
                 (c) the event that record holds. An address alone does not
                 stand. If the artifact says it does not know the
                 coordinates, r2 = 0.
 r3 FALSIFIER  — states what observation would show the claim wrong.
 r4 NEXT CHECK — states WHERE a future artifact would confirm it (file, log,
                 or ledger) AND by when (a deadline or dated checkpoint).
                 Both, or it does not stand.

DV-2 — coordinate reality. Take the record named in r2's (b) and ACTUALLY
OPEN it with your Read tool (repo root is the working directory; git-show is
not available to you — if the record is only a commit hash you cannot open,
that is "unobservable"). Report:
 1  the record opens and holds the event claimed in (c)
 0  the record opens but does not hold that event
 "unobservable"  the record cannot be opened (no path given, path missing,
                 or commit-only reference)
If r2 = 0 because no record was named, DV-2 is "unobservable".

DV-3 — adoption: would you, as a researcher, use this reading as the basis
for your own next check of decision 15? 0 or 1, with a reason of at most 25
words. Adopting means you would rely on its coordinates and falsifier as
written.

Answer with ONE line of JSON and nothing else:
{"r1":0|1,"r2":0|1,"r3":0|1,"r4":0|1,
 "dv2":0|1|"unobservable","dv3":0|1,
 "r2_reason":"<=25 words","dv2_reason":"<=25 words","dv3_reason":"<=25 words"}

```
