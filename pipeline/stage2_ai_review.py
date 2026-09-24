"""AI-assisted code review stage.

Uses the local `claude` CLI in headless print mode (`claude -p --output-format
json`) rather than a raw Anthropic API key, because that's what's actually
available and authenticated in this environment right now. This is a
deliberate POC shortcut, not the recommended production shape -- see
README.md "Production notes": a real deployment should call the Anthropic
Messages API directly (python/TS SDK) so cost, concurrency, and structured
output (tool-enforced JSON schema) are under the pipeline's own control
instead of riding on a full CLI agent session's overhead.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

MAX_FILE_CHARS = 20_000

DEFAULT_MODEL = "claude-sonnet-5"

RESPONSE_SCHEMA_INSTRUCTIONS = """
Respond with ONLY a single JSON object (no markdown fences, no prose outside the JSON), matching this shape:

{
  "summary": "<2-4 sentence overall assessment>",
  "overall_recommendation": "approve" | "approve_with_minor_notes" | "manual_review_required" | "reject",
  "findings": [
    {
      "rule_id": "<one of the SEM-* rule ids below, or 'OTHER' for something outside the rubric>",
      "severity": "critical" | "high" | "medium" | "low" | "info",
      "title": "<short title>",
      "file": "<file path or null>",
      "line": <line number or null>,
      "explanation": "<why this is a problem, concrete and specific to this code>",
      "confidence": "high" | "medium" | "low"
    }
  ]
}

Only report a finding if you can point to something concrete in the actual code shown -- do not invent generic advice.
If a rubric item genuinely can't be assessed from the code shown (e.g. it needs a live chain/UI to verify), omit it rather than guessing.
"""


def _read_truncated(path: Path) -> str:
    try:
        text = path.read_text(errors="ignore")
    except OSError as e:
        return f"<could not read {path}: {e}>"
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + f"\n... [truncated, {len(text) - MAX_FILE_CHARS} more chars]"
    return text


def _condense_stage1(stage1: Dict) -> str:
    lines = []
    sol = stage1.get("solidity") or {}
    compile_ok = ((sol.get("compile") or {}).get("ok"))
    lines.append(f"- Solidity compile: {'OK' if compile_ok else 'FAILED/SKIPPED'}")
    iface = sol.get("interface_conformance") or {}
    lines.append(f"- Interface conformance checked: {iface.get('checked')}, missing_functions={iface.get('missing_functions')}, mutability_violations={iface.get('mutability_violations')}")
    for f in sol.get("security_pattern_findings", []):
        lines.append(f"- [static] {f['rule_id']} {f['label']} at {f['file']}:{f['line']}: {f['snippet']}")
    man = stage1.get("manifest") or {}
    for f in man.get("findings", []):
        lines.append(f"- [static] {f['rule_id']} {f['title']}: {f['detail']}")
    ui = stage1.get("ui") or {}
    for f in ui.get("findings", []):
        lines.append(f"- [static] {f['rule_id']} {f['title']}: {f['detail']}")
    return "\n".join(lines) if lines else "(no static findings)"


def build_prompt(submission_name: str, contracts: List[Path], manifest_path: Optional[Path],
                  ui_files: List[Path], stage1: Dict, ai_rules: List[Dict]) -> str:
    rules_text = "\n".join(f"- {r['id']} [{r['severity']}] {r['description']}" for r in ai_rules)

    contract_blobs = "\n\n".join(
        f"--- contract file: {c} ---\n{_read_truncated(c)}" for c in contracts
    ) or "(no .sol files found)"

    manifest_blob = _read_truncated(manifest_path) if manifest_path else "(no game.manifest.json found)"

    ui_blobs = "\n\n".join(
        f"--- ui file: {f} ---\n{_read_truncated(f)}" for f in ui_files[:6]
    ) or "(no guest UI files found)"
    if len(ui_files) > 6:
        ui_blobs += f"\n\n... [{len(ui_files) - 6} more UI files not shown]"

    return f"""You are performing an automated code review of a submission to the Chain hackathon game jam (jam.chain.wtf). Submissions implement a Solidity smart contract conforming to the `ICasinoGameV2` interface, plus a web UI that bridges to a Chain SDK host via postMessage/Penpal.

Submission: {submission_name}

You are reviewing against the following rubric items, which static analysis could NOT already verify mechanically (compilation, interface signatures, manifest schema, and simple pattern-grep were already checked separately -- see "Static analysis findings" below). Focus your review on these semantic/logic items:

{rules_text}

Static analysis findings so far (do not re-report these verbatim, but use them as context -- e.g. if compile failed, say so affects your ability to review):
{_condense_stage1(stage1)}

=== SMART CONTRACT SOURCE ===
{contract_blobs}

=== game.manifest.json ===
{manifest_blob}

=== GUEST UI / HOST-BRIDGE SOURCE (subset) ===
{ui_blobs}

{RESPONSE_SCHEMA_INSTRUCTIONS}
"""


def _extract_json(result_text: str) -> Dict:
    text = result_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        return {"parse_error": True, "raw_result": result_text}


def run(submission_name: str, contracts: List[Path], manifest_path: Optional[Path],
        ui_files: List[Path], stage1: Dict, rubric: Dict, model: str = DEFAULT_MODEL,
        timeout: int = 240) -> Dict:
    ai_rules = [r for r in rubric["rules"] if r["check_type"] == "ai"]
    prompt = build_prompt(submission_name, contracts, manifest_path, ui_files, stage1, ai_rules)

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"AI review timed out after {timeout}s"}
    except FileNotFoundError:
        return {"error": "`claude` CLI not found on PATH -- cannot run AI review stage"}

    if proc.returncode != 0:
        return {"error": f"claude CLI exited {proc.returncode}", "stderr": proc.stderr[-2000:]}

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": "could not parse claude CLI JSON envelope", "raw_stdout": proc.stdout[-2000:]}

    if envelope.get("is_error"):
        return {"error": "claude CLI reported an error", "envelope": envelope}

    review = _extract_json(envelope.get("result", ""))
    return {
        "review": review,
        "model": model,
        "cost_usd": envelope.get("total_cost_usd"),
        "duration_ms": envelope.get("duration_ms"),
    }
