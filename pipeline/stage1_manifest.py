"""Validate game.manifest.json against the schema described in
CHAIN_WTF_CASINO_GAMES.md / games-sdk/src/manifest.ts (validateCasinoGameManifest).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Dict, List, Optional


def _canonical_game_id(game_id: str) -> str:
    stripped = re.sub(r"Game$", "", game_id, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def validate(manifest_path: Optional[Path]) -> Dict:
    findings: List[Dict] = []

    if manifest_path is None:
        findings.append({
            "rule_id": "MANIFEST-01", "severity": "critical",
            "title": "game.manifest.json not found",
            "detail": "No game.manifest.json was discovered in the submission.",
        })
        return {"checked": False, "findings": findings}

    try:
        data = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        findings.append({
            "rule_id": "MANIFEST-01", "severity": "critical",
            "title": "game.manifest.json is not valid JSON",
            "detail": str(e),
        })
        return {"checked": False, "findings": findings}

    def fail(rule_id, severity, title, detail):
        findings.append({"rule_id": rule_id, "severity": severity, "title": title, "detail": detail})

    if data.get("schemaVersion") != 1:
        fail("MANIFEST-02", "critical", "schemaVersion must be 1", f"got {data.get('schemaVersion')!r}")

    game_id = data.get("gameId")
    if not isinstance(game_id, str) or not game_id.strip():
        fail("MANIFEST-03", "critical", "gameId must be a non-empty string", f"got {game_id!r}")

    if data.get("apiVersion") != 1:
        fail("MANIFEST-04", "critical", "apiVersion must be 1", f"got {data.get('apiVersion')!r}")

    default_locale = data.get("defaultLocale")
    locales = data.get("locales")
    if not isinstance(default_locale, str) or not default_locale.strip():
        fail("MANIFEST-05", "critical", "defaultLocale must be a non-empty string", f"got {default_locale!r}")
    elif not isinstance(locales, dict) or default_locale not in locales:
        fail("MANIFEST-05", "critical", "defaultLocale must exist as a key in locales",
             f"defaultLocale={default_locale!r}, locales keys={list(locales) if isinstance(locales, dict) else locales!r}")
    else:
        entry = locales.get(default_locale) or {}
        if not isinstance(entry, dict) or not str(entry.get("name", "")).strip():
            fail("MANIFEST-05", "critical", "locales[defaultLocale].name must be non-empty",
                 f"locales[{default_locale!r}] = {entry!r}")

    presentation = data.get("presentation")
    if not isinstance(presentation, dict):
        fail("MANIFEST-06", "high", "presentation object missing", f"got {presentation!r}")
    else:
        if presentation.get("mode") not in ("full-iframe", "embedded"):
            fail("MANIFEST-06", "high", "presentation.mode must be 'full-iframe' or 'embedded'",
                 f"got {presentation.get('mode')!r}")
        min_height = presentation.get("minHeight")
        if min_height is not None and not (isinstance(min_height, (int, float)) and math.isfinite(min_height)):
            fail("MANIFEST-07", "medium", "presentation.minHeight must be a finite number",
                 f"got {min_height!r}")
        panels = presentation.get("hostPanels")
        if not isinstance(panels, dict) or not all(
            isinstance(panels.get(k), bool) for k in ("openSession", "history", "status")
        ):
            fail("MANIFEST-08", "high", "presentation.hostPanels.{openSession,history,status} must be booleans",
                 f"got {panels!r}")

    caps = data.get("capabilities")
    required_bool_keys = ("openSession", "submitAction", "forfeitExpiredSession", "cancelStuckRandomness", "resize")
    if not isinstance(caps, dict) or not all(isinstance(caps.get(k), bool) for k in required_bool_keys):
        fail("MANIFEST-09", "critical", "capabilities.{...} must all be present booleans",
             f"got {caps!r}")
    elif caps.get("openSession") is not True:
        fail("MANIFEST-09", "critical", "capabilities.openSession must be true for a betting game",
             f"got {caps.get('openSession')!r}")

    info: Dict = {}
    if isinstance(game_id, str) and game_id.strip():
        info["canonical_game_id"] = _canonical_game_id(game_id)

    return {
        "checked": True,
        "raw": data,
        "info": info,
        "findings": findings,
    }
