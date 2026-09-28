"""Static checks over the guest UI / host-bridge JS/TS code.

Grep-based, deliberately simple: these are signal for the AI review stage
and human reviewer, not a full JS analyzer. False positives are expected
(e.g. a comment containing "sendTransaction") and should be resolved by
whoever reads the report, not auto-failed.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

WALLET_SIGNING_PATTERNS = [
    r"new\s+ethers\.Wallet\s*\(",
    r"privateKeyToAccount\s*\(",
    r"\.signTransaction\s*\(",
    r"walletClient\.sendTransaction\s*\(",
    r"eth_sendTransaction",
    r"window\.ethereum\.request\s*\(\s*\{\s*method:\s*['\"]eth_sendTransaction",
]

ORIGIN_WILDCARD_PATTERN = r"allowedOrigins\s*[:=][^\n;]*['\"]\*['\"]"

BRIDGE_USAGE_PATTERNS = [
    r"connectGameToHost",
    r"Penpal",
    r"WindowMessenger",
]


def _run(cmd: List[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def _grep_all(files: List[Path], patterns: List[str]) -> List[Dict]:
    findings = []
    compiled = [re.compile(p) for p in patterns]
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for pat, regex in zip(patterns, compiled):
                if regex.search(line):
                    findings.append({"pattern": pat, "file": str(f), "line": i, "snippet": line.strip()[:200]})
    return findings


def check_wallet_signing(ui_files: List[Path]) -> List[Dict]:
    hits = _grep_all(ui_files, WALLET_SIGNING_PATTERNS)
    return [{
        "rule_id": "UI-01", "severity": "critical",
        "title": "Guest code appears to sign/broadcast a transaction directly",
        "detail": f"{h['file']}:{h['line']}: {h['snippet']}",
    } for h in hits]


def check_origin_wildcard(ui_files: List[Path]) -> List[Dict]:
    hits = _grep_all(ui_files, [ORIGIN_WILDCARD_PATTERN])
    return [{
        "rule_id": "UI-02", "severity": "high",
        "title": "postMessage allowedOrigins resolves to '*' (dev-only per VISUAL_AND_UX.md)",
        "detail": f"{h['file']}:{h['line']}: {h['snippet']}",
    } for h in hits]


def check_reveal_outcome_present(ui_files: List[Path]) -> List[Dict]:
    hits = _grep_all(ui_files, [r"hostApi\.revealOutcome|\.revealOutcome\s*\("])
    if hits:
        return []
    return [{
        "rule_id": "UI-06", "severity": "high",
        "title": "No call to hostApi.revealOutcome found",
        "detail": "revealOutcome({ sessionId }) is now a mandatory guest->host call after a win's "
                  "presentation finishes -- without it the host's balance-display guard stays stale "
                  "until the player reloads. Not found in any scanned UI file (may be a false "
                  "negative if revealOutcome is called from a file this pipeline didn't scan).",
    }]


def check_bridge_usage(ui_files: List[Path]) -> Dict:
    hits = _grep_all(ui_files, BRIDGE_USAGE_PATTERNS)
    return {
        "rule_id": "UI-03",
        "uses_sdk_bridge_pattern": len(hits) > 0,
        "evidence": hits[:10],
    }


def run_npm_audit(package_json: Optional[Path]) -> Dict:
    if package_json is None:
        return {"checked": False, "reason": "no package.json found"}
    try:
        proc = _run(["npm", "audit", "--json"], cwd=package_json.parent, timeout=45)
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        return {"checked": False, "reason": f"npm audit failed to run/parse: {e}"}

    vulns = data.get("vulnerabilities", {})
    high_or_critical = {
        name: v for name, v in vulns.items() if v.get("severity") in ("high", "critical")
    }
    return {
        "checked": True,
        "total_vulnerabilities": len(vulns),
        "high_or_critical": {name: v.get("severity") for name, v in high_or_critical.items()},
    }


def analyze(ui_files: List[Path], package_json: Optional[Path]) -> Dict:
    if not ui_files:
        return {"skipped_reason": "no guest UI JS/TS files found"}

    findings = []
    findings.extend(check_wallet_signing(ui_files))
    findings.extend(check_origin_wildcard(ui_files))
    findings.extend(check_reveal_outcome_present(ui_files))
    bridge = check_bridge_usage(ui_files)
    npm_audit = run_npm_audit(package_json)

    if npm_audit.get("checked") and npm_audit.get("high_or_critical"):
        findings.append({
            "rule_id": "UI-04", "severity": "high",
            "title": "High/critical severity npm dependency vulnerabilities",
            "detail": json.dumps(npm_audit["high_or_critical"]),
        })

    return {
        "ui_files_analyzed": [str(f) for f in ui_files],
        "findings": findings,
        "sdk_bridge_usage": bridge,
        "npm_audit": npm_audit,
    }
