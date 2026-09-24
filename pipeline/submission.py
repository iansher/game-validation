"""Discover the relevant files inside a submission directory.

REPO_STRUCTURE.md is explicit that there is no required folder layout for
third-party games, so this is heuristic glob-based discovery rather than a
fixed schema. Explicit --contract/--manifest/--ui-dir CLI flags always win.
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

VENDOR_DIR_NAMES = {
    "node_modules", "lib", "out", "artifacts", "cache", ".git",
    "typechain-types", "__MACOSX", ".foundry",
}

UI_HINT_PATTERNS = (
    "*penpal*", "*connectGameToHost*", "*host-entry*", "*external-bridge*",
    "*guest*", "*bridge*",
)


def _walk_files(root: Path, suffixes: tuple) -> List[Path]:
    matches: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in VENDOR_DIR_NAMES and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(suffixes):
                matches.append(Path(dirpath) / fn)
    return matches


@dataclass
class Submission:
    root: Path
    contracts: List[Path] = field(default_factory=list)
    manifest: Optional[Path] = None
    ui_files: List[Path] = field(default_factory=list)
    package_json: Optional[Path] = None

    @property
    def name(self) -> str:
        return self.root.name


def discover(
    root: Path,
    contract_override: Optional[Path] = None,
    manifest_override: Optional[Path] = None,
    ui_dir_override: Optional[Path] = None,
) -> Submission:
    root = root.resolve()
    sub = Submission(root=root)

    if contract_override:
        sub.contracts = [contract_override.resolve()]
    else:
        sub.contracts = sorted(_walk_files(root, (".sol",)))

    if manifest_override:
        sub.manifest = manifest_override.resolve()
    else:
        found = _walk_files(root, ("game.manifest.json",))
        # exact filename match only
        found = [f for f in found if f.name == "game.manifest.json"]
        sub.manifest = found[0] if found else None

    ui_root = ui_dir_override.resolve() if ui_dir_override else root
    js_files = _walk_files(ui_root, (".js", ".ts", ".mjs", ".jsx", ".tsx"))
    # Prefer files that look like SDK bridge/guest code; fall back to all JS/TS.
    hinted = [
        f for f in js_files
        if any(fnmatch.fnmatch(f.name.lower(), pat) for pat in UI_HINT_PATTERNS)
    ]
    sub.ui_files = sorted(hinted) if hinted else sorted(js_files)

    pkg = _walk_files(root, ("package.json",))
    pkg = [f for f in pkg if f.name == "package.json"]
    sub.package_json = pkg[0] if pkg else None

    return sub
