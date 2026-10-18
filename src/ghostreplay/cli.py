"""GhostReplay CLI.

Commands:
  ghostreplay ingest  <project> <transcript> [-o <dir>]
      Parse the ONE documented format (Claude Code session
      JSON/JSONL, see transcript.py) into a canonical session at
      <dir>/<session-id>.json (default <project>/.ghostreplay/).
  ghostreplay analyse <project> <session-or-transcript>
                      [--bundle <dir>] [-o <dir>]
      (alias: analyze) Analyse one session, write REPLAY.md +
      replay-report.json (default <project>/.ghostreplay/reports/
      <session-id>/), and append the trend ledger.
  ghostreplay propose <project> <session...> [--bundle <dir>]
                      [-o <dir>]
      Aggregate >= min_sessions sessions into a profile proposal
      (ghostreplay-proposal.json + PROPOSAL.md). Proposal only.
  ghostreplay apply   <project> [--proposal <file>]
      Print the proposal diff, require an explicit yes typed in
      the terminal, back the profile up, then apply. There is
      deliberately NO --yes flag: the yes must be typed.
  ghostreplay restore <project>
      Undo an apply from .ghostslice.json.bak (or remove a
      profile GhostReplay created).
  ghostreplay trend   <project>
      Session trend from .ghostreplay-history.jsonl.

Exit codes: 0 ok, 1 findings-level refusal (e.g. proposal not
ready, apply declined is 0 - a human said no, that is not an
error), 2 user error (bad input, unrecognised format, invalid
profile/proposal). Local files only; no network anywhere.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .analysis import analyse_session
from .bundle import BundleError, find_bundle
from .ledger import append_record, read_records, trend_summary
from .profile import (apply_changes, load_replay_config,
                      read_profile_data, restore_profile)
from .propose import build_proposal, load_proposal, write_proposal
from .report import write_reports
from .transcript import TranscriptError, load_session, parse_transcript, dump_canonical


def _err(msg: str) -> int:
    print(f"ghostreplay: {msg}", file=sys.stderr)
    return 2


def _load(project: Path, session_path: str, bundle_arg):
    session = load_session(session_path)
    bundle = find_bundle(project, bundle_arg)
    return session, bundle


def cmd_ingest(args) -> int:
    project = Path(args.project)
    try:
        session = parse_transcript(args.transcript)
    except TranscriptError as exc:
        return _err(str(exc))
    out_dir = Path(args.out) if args.out else project / ".ghostreplay"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{session['session_id']}.json"
    out.write_text(dump_canonical(session), encoding="utf-8")
    print(f"ingested session {session['session_id']}: "
          f"{session['entry_count']} entries, "
          f"{session['turn_count']} turns -> {out}")
    if session["secret_findings"]:
        print(f"secret-shaped findings (values never stored): "
              f"{len(session['secret_findings'])} - see canonical "
              "file's secret_findings (source + line + type only)")
    return 0


def cmd_analyse(args) -> int:
    project = Path(args.project)
    try:
        session, bundle = _load(project, args.session, args.bundle)
        report = analyse_session(session, bundle, project)
    except (TranscriptError, BundleError, ValueError) as exc:
        return _err(str(exc))
    out_dir = Path(args.out) if args.out else (
        project / ".ghostreplay" / "reports" / session["session_id"])
    json_path, md_path = write_reports(report, out_dir)
    append_record(project, report)
    t = report["totals"]
    print(f"session {report['session_id']}: {report['turn_count']} turns")
    if bundle:
        print(f"sent (est.): {t['sent_sliced_estimated_tokens']} tokens; "
              f"not referenced in transcript (est.): "
              f"{t['not_referenced_estimated_tokens']}; re-read waste "
              f"(est.): {t['reread_waste_estimated_tokens']}; mode "
              f"friction files: {len(report['mode_friction']['files'])}")
    else:
        print("no bundle manifest joined: sent-vs-referenced, "
              "re-read cost and friction unavailable")
    print(f"reports -> {md_path} , {json_path}")
    print(f"ledger -> {project / '.ghostreplay-history.jsonl'}")
    return 0


def cmd_propose(args) -> int:
    project = Path(args.project)
    try:
        cfg = load_replay_config(project)
        reports = []
        blocks: list[dict] = []
        for session_path in args.sessions:
            session, bundle = _load(project, session_path, args.bundle)
            reports.append(analyse_session(session, bundle, project))
            for turn in session.get("turns", []):
                for block in turn.get("instruction_blocks", []):
                    blocks.append({"session_id": session["session_id"],
                                   "turn": turn["turn"], "text": block})
        data, _ = read_profile_data(project)
    except (TranscriptError, BundleError, ValueError) as exc:
        return _err(str(exc))
    proposal = build_proposal(
        str(project), reports, blocks, cfg,
        current_modes=data.get("modes") or {},
        current_entry_points=data.get("entry_points") or [])
    out_dir = Path(args.out) if args.out else project / ".ghostreplay"
    json_path, md_path = write_proposal(proposal, out_dir)
    print(f"sessions analysed: {proposal['sessions_analysed']} "
          f"(minimum {proposal['min_sessions_required']}); status: "
          f"{proposal['status']}; profile changes proposed: "
          f"{len(proposal['changes'])}; recall candidates: "
          f"{len(proposal['recall_candidates'])}")
    print(f"proposal -> {md_path} , {json_path}")
    print("nothing applied: run `ghostreplay apply` and type yes "
          "to apply")
    return 0


def cmd_apply(args) -> int:
    project = Path(args.project)
    proposal_path = Path(args.proposal) if args.proposal else (
        project / ".ghostreplay" / "ghostreplay-proposal.json")
    try:
        proposal = load_proposal(proposal_path)
    except ValueError as exc:
        return _err(str(exc))
    changes = [c for c in proposal["changes"]
               if c.get("kind") in ("mode", "entry_point")]
    print(f"Proposal from {proposal_path}: "
          f"{len(proposal['changes'])} change(s) "
          f"({proposal['sessions_analysed']} sessions analysed)")
    for c in proposal["changes"]:
        print(f"  - {c['kind']}: {c['target']}: {c['current']} -> "
              f"{c['proposed']}")
        print(f"    evidence: {c['evidence']}")
    if not changes:
        print("proposal has no profile changes; nothing to apply")
        return 0
    try:
        answer = input("Apply these changes to .ghostslice.json? "
                       "Type yes to apply: ")
    except EOFError:
        print("no input available; not applied")
        return 1
    if answer.strip().lower() not in ("yes", "y"):
        print("not applied (explicit yes not given)")
        return 0
    try:
        result = apply_changes(project, proposal["changes"])
    except ValueError as exc:
        return _err(str(exc))
    backup = result["backup"] or "(no prior profile existed)"
    print(f"applied {result['applied']} change(s) to "
          f"{project / '.ghostslice.json'}; backup: {backup}")
    print(f"undo any time with: ghostreplay restore {project}")
    return 0


def cmd_restore(args) -> int:
    project = Path(args.project)
    try:
        msg = restore_profile(project)
    except ValueError as exc:
        return _err(str(exc))
    print(msg)
    return 0


def cmd_trend(args) -> int:
    project = Path(args.project)
    summary = trend_summary(read_records(project))
    if summary["records"] == 0:
        print("no GhostReplay history yet "
              f"({project / '.ghostreplay-history.jsonl'} is empty "
              "or missing); run analyse first")
        return 0
    print(f"sessions: {summary['sessions']} "
          f"(ledger records: {summary['records']})")
    print(f"{'recorded at':<28} {'session':<22} {'sent est.':>10} "
          f"{'waste est.':>11} {'waste %':>8}")
    for row in summary["rows"]:
        print(f"{(row.get('ts') or '')[:27]:<28} "
              f"{(row.get('session_id') or '')[:21]:<22} "
              f"{row.get('sent_sliced_estimated_tokens', 0):>10} "
              f"{row.get('estimated_waste_tokens', 0):>11} "
              f"{row.get('estimated_waste_percent', 0.0):>8}")
    print(f"mean waste: {summary['mean_waste_percent']}% "
          f"(first {summary['first_waste_percent']}%, "
          f"last {summary['last_waste_percent']}%); "
          f"total re-read waste (est.): "
          f"{summary['total_reread_waste_estimated_tokens']} tokens")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="ghostreplay",
        description="GhostReplay: learn from local agent session "
                    "transcripts, propose GhostSlice profile "
                    "improvements with evidence (propose-only).")
    ap.add_argument("--version", action="version",
                    version=f"ghostreplay {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="parse a transcript into a "
                                      "canonical session")
    p.add_argument("project")
    p.add_argument("transcript")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_ingest)

    for name in ("analyse", "analyze"):
        p = sub.add_parser(name, help="analyse one session")
        p.add_argument("project")
        p.add_argument("session")
        p.add_argument("--bundle", help="GhostSlice bundle dir or "
                                        "manifest.json (default: "
                                        "<project>/.claude_context)")
        p.add_argument("-o", "--out")
        p.set_defaults(fn=cmd_analyse)

    p = sub.add_parser("propose", help="aggregate sessions into a "
                                       "profile proposal")
    p.add_argument("project")
    p.add_argument("sessions", nargs="+")
    p.add_argument("--bundle")
    p.add_argument("-o", "--out")
    p.set_defaults(fn=cmd_propose)

    p = sub.add_parser("apply", help="apply a proposal after an "
                                     "explicit terminal yes")
    p.add_argument("project")
    p.add_argument("--proposal")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("restore", help="undo an apply")
    p.add_argument("project")
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("trend", help="session trend from the ledger")
    p.add_argument("project")
    p.set_defaults(fn=cmd_trend)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
