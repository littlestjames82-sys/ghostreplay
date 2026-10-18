"""GhostSlice bundle-manifest join.

The manifest is the ground truth for what was SENT (PLAN.md).
GhostReplay reads a GhostSlice bundle's manifest.json:

  {"tool": "ghostslice", "version": ..., "root": ...,
   "total_original_tokens": n, "total_sliced_tokens": n,
   "files": [{"path", "mode", "language", "original_tokens",
              "sliced_tokens", "sha256",
              "symbols_included": [...] (symbols mode only)}, ...]}

Join rules:
- An explicit bundle dir / manifest path always wins.
- Else <project>/.claude_context/manifest.json when it exists.
- Else no bundle: analysis still runs (re-reads / repetition from
  the transcript alone), and every bundle-dependent section is
  reported as unavailable, never guessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class BundleError(ValueError):
    pass


def load_manifest(bundle: str | Path) -> dict:
    bundle = Path(bundle)
    mpath = bundle / "manifest.json" if bundle.is_dir() else bundle
    try:
        raw = mpath.read_text(encoding="utf-8")
    except OSError as exc:
        raise BundleError(f"{mpath}: cannot read manifest: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise BundleError(f"{mpath}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise BundleError(
            f"{mpath}: not a GhostSlice manifest "
            "(needs an object with a 'files' list)")
    files = []
    for i, entry in enumerate(data["files"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise BundleError(f"{mpath}: files[{i}] has no string 'path'")
        files.append({
            "path": entry["path"],
            "mode": entry.get("mode") or "full",
            "language": entry.get("language"),
            "original_tokens": int(entry.get("original_tokens") or 0),
            "sliced_tokens": int(entry.get("sliced_tokens") or 0),
            "sha256": entry.get("sha256") or "",
            "symbols_included": list(entry.get("symbols_included") or []),
        })
    return {
        "manifest_path": str(mpath),
        "manifest_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "root": data.get("root"),
        "tool_version": data.get("version"),
        "total_original_tokens": int(data.get("total_original_tokens") or 0),
        "total_sliced_tokens": int(data.get("total_sliced_tokens") or 0),
        "files": files,
        "by_path": {f["path"]: f for f in files},
    }


def find_bundle(project: str | Path,
                explicit: str | Path | None = None) -> dict | None:
    if explicit is not None:
        return load_manifest(explicit)
    candidate = Path(project) / ".claude_context" / "manifest.json"
    if candidate.is_file():
        return load_manifest(candidate)
    return None


def match_path(candidate: str, bundle_files: list[str],
               project: str | Path | None = None) -> str | None:
    """Map an absolute or relative transcript path to a bundle
    relative path. Exact relative match wins, then project-relative
    resolution, then a unique suffix match (an absolute path ending
    in exactly one bundle path). Ambiguity returns None."""
    cand = candidate.replace("\\", "/")
    if cand in bundle_files:
        return cand
    if project is not None:
        try:
            rel = str(Path(candidate).resolve().relative_to(
                Path(project).resolve())).replace("\\", "/")
            if rel in bundle_files:
                return rel
        except (ValueError, OSError):
            pass
    suffix = [p for p in bundle_files if cand.endswith("/" + p)]
    if len(suffix) == 1:
        return suffix[0]
    return None
