"""Profile proposal generator.

Aggregates per-session analyses into a PROPOSAL: a profile diff
(ghostreplay-proposal.json) plus PROPOSAL.md, where every change
carries its evidence in plain figures. A proposal is not a change:
applying is a separate command with an explicit terminal yes
(cli.apply_command + profile.apply_changes).

Proposal rules (thresholds from the profile's replay section,
defaults in profile.REPLAY_DEFAULTS):
- mode -> exclude: a bundle file present in the bundle of at
  least `min_unreferenced_sessions` analysed sessions and not
  referenced in ANY of them.
  Evidence: "present in B/B bundles, referenced in 0/B sessions,
  ~N estimated sliced tokens per session".
- mode -> full: mode friction - a file sent non-full that the
  session/s direct-Read at least `friction_threshold` times in
  total (counted in distinct turns across sessions).
- entry_point -> pin: a file referenced in EVERY analysed
  session (and in >= 2 distinct turns in at least one), not
  already a profile entry point and not already covered by an
  exclude/full change.
- recall_candidate (not a profile change): an instruction block
  cluster repeated across >= 2 sessions. Listed so a human can
  move it into GhostRecall / a standing prompt; apply ignores it.

A zero-waste input produces an EMPTY changes list. A tool that
always finds savings is inventing them (PLAN.md).
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from . import secrets
from .analysis import repetition_clusters


def build_proposal(project: str, session_reports: list[dict],
                   session_blocks: list[dict], cfg: dict,
                   current_modes: dict, current_entry_points: list) -> dict:
    n = len(session_reports)
    present: dict[str, int] = defaultdict(int)
    referenced: dict[str, int] = defaultdict(int)
    tokens_sum: dict[str, int] = defaultdict(int)
    modes_seen: dict[str, str] = {}
    friction_counts: dict[str, int] = defaultdict(int)
    friction_modes: dict[str, str] = {}
    max_turns: dict[str, int] = defaultdict(int)

    for report in session_reports:
        if not report.get("bundle"):
            continue
        bundle_files = {}
        for f in report["referenced_files"]:
            bundle_files[f["path"]] = f
        for f in report["not_referenced_in_transcript"]:
            bundle_files[f["path"]] = {**f, "turns": []}
        for path, f in bundle_files.items():
            present[path] += 1
            modes_seen[path] = f["mode"]
            tokens_sum[path] += f["sliced_tokens"]
        for f in report["referenced_files"]:
            referenced[f["path"]] += 1
            max_turns[f["path"]] = max(max_turns[f["path"]],
                                       len(f["turns"]))
        for f in report["mode_friction"]["files"]:
            friction_counts[f["path"]] += f["count"]
            friction_modes[f["path"]] = f["mode"]

    changes: list[dict] = []
    for path in sorted(present):
        avg_tokens = round(tokens_sum[path] / present[path]) \
            if present[path] else 0
        if referenced[path] == 0 and \
                present[path] >= cfg["min_unreferenced_sessions"] and \
                present[path] >= 1 and modes_seen[path] != "exclude":
            changes.append({
                "kind": "mode", "target": path,
                "current": _current_mode(current_modes, path,
                                         modes_seen[path]),
                "proposed": "exclude",
                "evidence": (
                    f"present in {present[path]} bundle(s), "
                    f"referenced in 0/{present[path]} of those "
                    "sessions, "
                    f"~{avg_tokens} estimated sliced tokens per "
                    "session; 'not referenced in transcript' does "
                    "not prove it was not needed (transcripts can "
                    "omit silent reads)"),
                "sessions_present": present[path],
                "sessions_referenced": 0,
            })
            continue
        if friction_counts[path] >= cfg["friction_threshold"]:
            changes.append({
                "kind": "mode", "target": path,
                "current": _current_mode(current_modes, path,
                                         friction_modes[path]),
                "proposed": "full",
                "evidence": (
                    f"sent as {friction_modes[path]} and direct-Read "
                    f"in {friction_counts[path]} turns across {n} "
                    "sessions; the agent repeatedly asked for the "
                    "body it was not given"),
                "sessions_present": present[path],
                "friction_turns": friction_counts[path],
            })
            continue
        if n >= 2 and referenced[path] == present[path] == n and \
                max_turns[path] >= 2 and \
                path not in current_entry_points:
            changes.append({
                "kind": "entry_point", "target": path,
                "current": "not pinned", "proposed": "pin",
                "evidence": (
                    f"referenced in {referenced[path]}/{n} "
                    f"sessions, up to {max_turns[path]} distinct "
                    "turns in one session; pinning protects its "
                    "mode from fit/auto-shrink downgrades"),
                "sessions_present": present[path],
                "sessions_referenced": referenced[path],
            })

    recall_candidates = []
    if n >= 2:
        for cluster in repetition_clusters(session_blocks):
            if len(cluster["sessions"]) >= 2:
                recall_candidates.append({
                    "kind": "recall_candidate",
                    "fingerprint": cluster["fingerprint"],
                    "evidence": (
                        f"instruction block repeated in "
                        f"{cluster['occurrences']} occurrences "
                        f"across {len(cluster['sessions'])} "
                        f"sessions, ~{cluster['estimated_tokens_each']} "
                        "estimated tokens each time; a standing-rule "
                        "candidate for GhostRecall or the project "
                        "prompt, not a profile change"),
                    "occurrences": cluster["occurrences"],
                    "sessions": cluster["sessions"],
                })

    insufficient = n < cfg["min_sessions"]
    proposal = {
        "format": "ghostreplay-proposal/1",
        "tool": "ghostreplay",
        "version": "0.1.0",
        "project": project,
        "sessions_analysed": n,
        "min_sessions_required": cfg["min_sessions"],
        "status": "insufficient-sessions" if insufficient else "ready",
        "note": (
            f"Only {n} session(s) analysed; proposals need at "
            f"least {cfg['min_sessions']} by the profile's replay "
            "settings, so this proposal carries no profile changes."
            if insufficient else
            "Proposal only. Nothing is applied until `ghostreplay "
            "apply` prints this diff and receives an explicit yes "
            "in the terminal."),
        # An insufficient-session proposal must never be applied:
        # emit the diagnostic changes separately, not as changes.
        "changes": [] if insufficient else changes,
        "diagnostic_changes": changes if insufficient else [],
        "recall_candidates": recall_candidates,
        "qualifiers": [
            "'Not referenced in transcript' does not prove a file "
            "was not needed; transcripts can omit silent reads.",
            "All token figures are estimates.",
        ],
    }
    return secrets.sanitize_obj(proposal)


def _current_mode(current_modes: dict, path: str, bundle_mode: str) -> str:
    for pat, mode in current_modes.items():
        if pat == path:
            return f"{mode} (profile)"
    return f"{bundle_mode} (bundle)"


def render_proposal_md(proposal: dict) -> str:
    lines = [
        "# GhostReplay profile proposal",
        "",
        f"Project: {proposal['project']}",
        f"Sessions analysed: {proposal['sessions_analysed']} "
        f"(minimum required: {proposal['min_sessions_required']})",
        f"Status: {proposal['status']}",
        "",
        proposal["note"],
        "",
        "## Proposed profile changes",
        "",
    ]
    changes = proposal["changes"]
    if not changes:
        lines.append("None. (Zero proposed changes is a valid, "
                     "honest result.)")
    for c in changes:
        if c["kind"] == "mode":
            lines.append(f"- modes: {c['target']}: {c['current']} "
                         f"-> {c['proposed']}")
        else:
            lines.append(f"- entry_points: pin {c['target']}")
        lines.append(f"  Evidence: {c['evidence']}")
    if proposal["diagnostic_changes"]:
        lines += ["", "## Diagnostic only (insufficient sessions; "
                      "not applicable)", ""]
        for c in proposal["diagnostic_changes"]:
            lines.append(f"- {c['kind']}: {c['target']} -> "
                         f"{c['proposed']}")
            lines.append(f"  Evidence: {c['evidence']}")
    lines += ["", "## GhostRecall candidates (not profile changes)", ""]
    if proposal["recall_candidates"]:
        for c in proposal["recall_candidates"]:
            lines.append(f"- block {c['fingerprint']}: {c['evidence']}")
    else:
        lines.append("None.")
    lines += ["", "## Qualifiers", ""]
    for q in proposal["qualifiers"]:
        lines.append(f"- {q}")
    lines.append("")
    return secrets.sanitize("\n".join(lines))


def write_proposal(proposal: dict, out_dir: str | Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ghostreplay-proposal.json"
    md_path = out_dir / "PROPOSAL.md"
    json_path.write_text(
        json.dumps(proposal, indent=2, sort_keys=True,
                   ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_proposal_md(proposal), encoding="utf-8")
    return json_path, md_path


def load_proposal(path: str | Path) -> dict:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path}: cannot load proposal: {exc}") from exc
    if not isinstance(data, dict) or \
            data.get("format") != "ghostreplay-proposal/1":
        raise ValueError(f"{path}: not a ghostreplay proposal")
    if data.get("status") != "ready":
        raise ValueError(
            f"{path}: proposal status is {data.get('status')!r}; "
            "only a 'ready' proposal can be applied")
    return data
