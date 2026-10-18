# GhostReplay

**See what your agent actually used, not what you sent it.**

GhostSlice builds an agent context bundle from static analysis. But real
sessions disagree with static guesses: files ride along in every bundle
and are never touched, the same file is re-read turn after turn, and a
symbol served as signatures gets expanded to full again and again.
GhostReplay replays recorded agent sessions against the GhostSlice
bundle that produced them and turns the waste into evidence: a per-session
report, and a proposed `.ghostslice.json` profile diff where every change
cites its session counts.

By Ghost Developer Studio. MIT licensed. **Zero dependencies** (Python
standard library only), **local-first**: no network calls, no telemetry,
your transcripts never leave the machine.

Position in the Ghost stack: GhostSlice governs what an agent *reads*,
GhostVerify checks a slice was *enough*, GhostBench measures *controlled*
comparisons, GhostReplay learns from *actual runs* in the field.
Agent Seatbelt governs what an agent *does*.

> Naming note: "GhostReplay" is a working name. A GhostGuard add-on also
> uses the word "replay" (policy backtest, a different job); the reserve
> name is GhostTrace. The final call is a deliberate gate, recorded in
> PLAN.md.

## Install

No install needed to try it from a checkout:

```bash
cd ghostreplay
PYTHONPATH=src python3 -m ghostreplay --help
```

Or install it (still zero dependencies):

```bash
pip install .
ghostreplay --help
```

Requires Python 3.10 or newer.

## Quickstart

```bash
# 1. Register a recorded session transcript against a project
ghostreplay ingest ~/my-project ~/sessions/session.jsonl

# 2. Analyse it against the GhostSlice bundle that produced it
ghostreplay analyse ~/my-project ~/sessions/session.jsonl \
    --bundle ~/my-project-context --out /tmp/replay-out
#    -> /tmp/replay-out/REPLAY.md and /tmp/replay-out/replay-report.json

# 3. Propose profile improvements from at least 3 sessions
ghostreplay propose ~/my-project s1.jsonl s2.jsonl s3.jsonl \
    --bundle ~/my-project-context --out /tmp/replay-prop
#    -> /tmp/replay-prop/ghostreplay-proposal.json + PROPOSAL.md
#       (a proposal, nothing applied)

# 4. Apply only after reading the diff, with an explicit terminal yes
ghostreplay apply ~/my-project --proposal /tmp/replay-prop/ghostreplay-proposal.json
#    -> keeps the old profile as .ghostslice.json.bak;
#       `ghostreplay restore ~/my-project` puts it back

# 5. Watch cost and waste over time
ghostreplay trend ~/my-project
```

## The transcript format (v0.1: one shape, documented exactly)

GhostReplay v0.1 reads **Claude Code session transcripts as saved locally:
JSONL, one JSON object per line**. Each line is expected to carry:

- `type`: `"user"` or `"assistant"` (other line types are ignored),
- `sessionId`: the session identifier,
- `timestamp`: ISO-8601,
- `message.role` and `message.content`.

File usage is read from assistant `tool_use` content blocks: a block with
`"name": "Read"` (or `"Edit"`) and `input.file_path` set to the project
relative path counts as a reference to that file. Text content blocks are
scanned for bundle file paths as secondary references.

Any other shape fails loudly with a format error rather than being
analysed badly. Fixture transcripts in exactly this shape live in
`tests/fixtures/sessions/` and `examples/transcripts/`.

## What a report shows

`REPLAY.md` / `replay-report.json` for one session:

- **Re-reads**: a file served as fresh context in N turns, with its
  token total from the bundle manifest.
- **Not referenced in transcript**: bundle files the session never
  mentioned, read, or edited, with the tokens each one cost. The label is
  always the qualified one. A transcript can miss silent or
  provider-side reads, so "not referenced" is evidence for a human, not a
  verdict that the file is "not needed".
- **Mode friction**: symbols first served as signatures that the session
  then expanded to full, counted per symbol.
- **Rule repetition**: near-duplicate instruction blocks repeated across
  sessions, flagged as candidates for GhostRecall (the project-memory
  companion).
- **Totals**: turns, estimated tokens in, estimated waste by category.
  Token figures come from the GhostSlice bundle manifest and the shared
  estimator, and are labelled **estimates**. Provider-reported usage
  figures, when a transcript carries them, are recorded separately as
  *reported* and never blended with estimates.

## Propose-only, always

`propose` never touches your profile. `apply` prints the diff, asks for
an explicit `yes` in the terminal, backs the old profile up to
`.ghostslice.json.bak`, and `ghostreplay restore` undoes it. A proposal needs
at least 3 sessions by default: a single session can be analysed, but one
odd run should not rewrite a profile. There is no silent auto-tuning,
and there will not be one without a separate explicit decision.

## Privacy rules (plain words)

- Transcripts are sensitive: they can hold proprietary code, personal
  prompts, and keys. GhostReplay is local-only and makes no network
  calls.
- Reports, proposals, and ledgers **never quote a secret-shaped string**.
  A secret finding is recorded as path + line + type only, matching the
  GhostSlice secrets guard.
- Reports are written where you point `--out`, and the ledger
  (`.ghostreplay-history.jsonl`) lives in the project only when a command
  writes it. Nothing is uploaded or shared, ever.

## Honest limits

- Transcript incompleteness is the central limit: see the qualified
  label above. Treat proposals as evidence, check them, then apply.
- One transcript format in v0.1. That shape will change upstream
  eventually; GhostReplay pins the shape it supports and fails loudly on
  a shape it does not recognise.
- GhostReplay measures context *use*, not answer quality. Task outcomes
  are GhostBench's job.

## Tests

```bash
PYTHONPATH=src python3 -m pytest tests -q
```

The suite encodes the PLAN.md verification bar: a planted-waste fixture
(one file re-read exactly 5 times, one bundle file never referenced)
must produce both findings with manifest-true token figures; zero-waste
fixtures must propose an *empty* diff; a planted secret-shaped string in
a fixture transcript must appear in zero output files; identical re-runs
must produce identical report totals. Fixture-integrity tests
(`tests/test_fixtures.py`) pass without a core; the CLI contract tests
require the core and fail (not skip) when it is absent.

## Layout

```
src/ghostreplay/    the package (CLI: ingest / analyse / propose / apply / trend)
tests/              contract tests + fixtures (project, bundle, sessions)
examples/           sample project, sample bundle, sample transcripts, demo script
PLAN.md             the original product plan and verification bar
```
