"""Static analysis for the smart-contract side of a submission.

Uses `forge` (Foundry) for compilation, ABI extraction, and its built-in
linter, plus plain regex/grep for security anti-patterns that don't need a
full compile. No `slither`/`solc` binary is assumed to be installed --
`forge build` bundles its own solc (downloaded on first use via svm).

Compilation is best-effort: a submission that imports a dependency we don't
have installed (e.g. @openzeppelin without node_modules committed) will fail
to compile here. That failure is reported explicitly rather than silently
degrading -- see `compile` result's `ok` / `skipped_reason`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRATCH = PROJECT_ROOT / ".forge_scratch"
REFERENCE_IFACE = PROJECT_ROOT / "rubric" / "ICasinoGameV2.sol"

DANGEROUS_PATTERNS = [
    ("SEC-01", "critical", "selfdestruct", r"\bselfdestruct\s*\("),
    ("SEC-02", "high", "delegatecall", r"\.delegatecall\s*\("),
    ("SEC-03", "high", "tx.origin", r"\btx\.origin\b"),
    ("SEC-04", "high", "low-level value call", r"\.call\{[^}]*value\s*:"),
    ("SEC-05", "high", "block.timestamp as apparent randomness source",
     r"\b(block\.timestamp|blockhash\s*\(|block\.prevrandao|block\.difficulty)\b"),
    ("SEC-06", "medium", "inline assembly", r"\bassembly\s*\{"),
    ("SEC-08", "medium", "stale `probabilityBps` field (should be probabilityWad)", r"\bprobabilityBps\b"),
]


def _run(cmd: List[str], cwd: Path, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def _ensure_scratch() -> Path:
    src = SCRATCH / "src"
    if not (SCRATCH / "foundry.toml").exists():
        SCRATCH.mkdir(parents=True, exist_ok=True)
        (SCRATCH / "foundry.toml").write_text(
            "[profile.default]\nsrc = \"src\"\nout = \"out\"\ncache_path = \"cache\"\n"
        )
    src.mkdir(parents=True, exist_ok=True)
    ref_dir = src / "_reference"
    ref_dir.mkdir(exist_ok=True)
    shutil.copy(REFERENCE_IFACE, ref_dir / "ICasinoGameV2.sol")
    return SCRATCH


def _reset_submission_copy(contracts: List[Path]) -> Path:
    """Copy submission .sol files into the scratch src/, preserving structure
    relative to their common parent so relative imports keep resolving."""
    sub_dir = SCRATCH / "src" / "_submission"
    if sub_dir.exists():
        shutil.rmtree(sub_dir)
    sub_dir.mkdir(parents=True)
    if not contracts:
        return sub_dir
    common = Path(*_common_prefix_parts(contracts))
    for c in contracts:
        rel = c.relative_to(common)
        dest = sub_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(c, dest)
    return sub_dir


def _common_prefix_parts(paths: List[Path]) -> List[str]:
    parts_lists = [p.parent.parts for p in paths]
    common: List[str] = []
    for tup in zip(*parts_lists):
        if len(set(tup)) == 1:
            common.append(tup[0])
        else:
            break
    return common if common else list(parts_lists[0][:1])


def compile_contracts(contracts: List[Path]) -> Dict:
    _ensure_scratch()
    sub_dir = _reset_submission_copy(contracts)
    proc = _run(["forge", "build", "--skip", "test", "script"], cwd=SCRATCH)
    ok = proc.returncode == 0
    return {
        "ok": ok,
        "stdout": proc.stdout[-8000:],
        "stderr": proc.stderr[-4000:],
        "submission_src_dir": str(sub_dir),
    }


def _find_game_contract_candidates(contracts: List[Path]) -> List[str]:
    """Heuristic: contract declarations that inherit ICasinoGameV2, else any
    contract defining onRandomness."""
    is_pattern = re.compile(r"contract\s+(\w+)\s+is\s+[^{]*\bICasinoGameV2\b")
    fallback_pattern = re.compile(r"contract\s+(\w+)\s*(?:is\s+[^{]*)?\{")
    names: List[str] = []
    fallback_names: List[str] = []
    for c in contracts:
        try:
            text = c.read_text(errors="ignore")
        except OSError:
            continue
        names.extend(is_pattern.findall(text))
        if "function onRandomness" in text:
            fallback_names.extend(fallback_pattern.findall(text))
    return names or fallback_names


def _abi_for(target: str) -> Optional[List[Dict]]:
    proc = _run(["forge", "inspect", target, "abi", "--json"], cwd=SCRATCH, timeout=60)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def _type_string(inp: Dict) -> str:
    t = inp.get("type", "")
    if t.startswith("tuple"):
        suffix = t[len("tuple"):]  # e.g. "" or "[]" or "[3]"
        inner = ",".join(_type_string(c) for c in inp.get("components", []))
        return f"({inner}){suffix}"
    return t


def _signature(entry: Dict) -> str:
    types = ",".join(_type_string(i) for i in entry.get("inputs", []))
    return f"{entry.get('name')}({types})"


def check_interface_conformance(compile_result: Dict, contracts: List[Path], rubric: Dict) -> Dict:
    findings = []
    if not compile_result.get("ok"):
        return {
            "checked": False,
            "reason": "compilation failed or was skipped; interface conformance not verified statically",
            "findings": [],
        }

    candidates = _find_game_contract_candidates(contracts)
    if not candidates:
        return {
            "checked": False,
            "reason": "no contract found declaring `is ICasinoGameV2` or defining onRandomness",
            "findings": [],
        }

    ref_abi = _abi_for("src/_reference/ICasinoGameV2.sol:ICasinoGameV2")
    ref_sigs = {_signature(e): e.get("stateMutability") for e in (ref_abi or []) if e.get("type") == "function"}

    required = {r["signature"]: r["state_mutability"] for r in rubric["required_functions"]}

    best_target = None
    best_abi = None
    for name in candidates:
        for c in contracts:
            target = f"{c}:{name}"
            abi = None
            # try relative-to-scratch path form as well
            for t in (target, f"src/_submission/{c.name}:{name}"):
                abi = _abi_for(t)
                if abi:
                    best_target, best_abi = t, abi
                    break
            if best_abi:
                break
        if best_abi:
            break

    if not best_abi:
        return {
            "checked": False,
            "reason": f"found candidate contract(s) {candidates} but could not extract ABI via forge inspect",
            "findings": [],
        }

    sub_sigs = {_signature(e): e.get("stateMutability") for e in best_abi if e.get("type") == "function"}

    missing = [sig for sig in required if sig not in sub_sigs]
    if missing:
        findings.append({
            "rule_id": "IFACE-01",
            "severity": "critical",
            "title": "Missing required ICasinoGameV2 function(s)",
            "detail": f"Contract `{best_target}` is missing: {missing}",
        })

    bad_mutability = []
    for sig, expected in required.items():
        actual = sub_sigs.get(sig)
        if actual is not None and actual not in (expected, "pure"):
            bad_mutability.append((sig, actual, expected))
    if bad_mutability:
        findings.append({
            "rule_id": "IFACE-02",
            "severity": "critical",
            "title": "Required function(s) not declared view/pure",
            "detail": ", ".join(f"{sig}: is `{actual}`, expected `{expected}`" for sig, actual, expected in bad_mutability),
        })

    return {
        "checked": True,
        "contract_target": best_target,
        "reference_function_count": len(ref_sigs),
        "missing_functions": missing,
        "mutability_violations": bad_mutability,
        "findings": findings,
    }


def run_lint(contracts: List[Path]) -> Dict:
    _ensure_scratch()
    _reset_submission_copy(contracts)
    proc = _run(["forge", "lint", "--severity", "high", "med", "--", "src/_submission"], cwd=SCRATCH, timeout=60)
    return {"returncode": proc.returncode, "output": (proc.stdout + proc.stderr)[-6000:]}


SMALL_MODULUS_PATTERN = re.compile(r"randomness[^\n;]{0,80}%\s*(\d{1,2})\b|%\s*(\d{1,2})\b[^\n;]{0,40}randomness", re.IGNORECASE)
REJECTION_MARKER_PATTERN = re.compile(r"\b(252|reject)", re.IGNORECASE)


def check_unbiased_dice_pattern(contracts: List[Path]) -> List[Dict]:
    """Heuristic for SEC-09: a raw small-modulus op against `randomness` with
    no rejection-sampling marker anywhere in the file. Deliberately loose --
    real judgment (does this actually need rejection sampling, is the
    domain size right) is SEM-13's job in the AI stage; this is just a
    static tripwire to make sure that gets looked at."""
    findings = []
    for c in contracts:
        try:
            text = c.read_text(errors="ignore")
        except OSError:
            continue
        if REJECTION_MARKER_PATTERN.search(text):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if SMALL_MODULUS_PATTERN.search(line):
                findings.append({
                    "rule_id": "SEC-09",
                    "severity": "high",
                    "label": "possible modulo-biased randomness-to-small-range mapping (no rejection sampling marker found in file)",
                    "file": str(c),
                    "line": i,
                    "snippet": line.strip()[:200],
                })
    return findings


def grep_security_patterns(contracts: List[Path]) -> List[Dict]:
    findings = []
    for rule_id, severity, label, pattern in DANGEROUS_PATTERNS:
        regex = re.compile(pattern)
        for c in contracts:
            try:
                text = c.read_text(errors="ignore")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    findings.append({
                        "rule_id": rule_id,
                        "severity": severity,
                        "label": label,
                        "file": str(c),
                        "line": i,
                        "snippet": line.strip()[:200],
                    })
    return findings


def analyze(contracts: List[Path], rubric: Dict) -> Dict:
    if not contracts:
        return {"skipped_reason": "no .sol files found in submission", "compile": None}

    compile_result = compile_contracts(contracts)
    interface_result = check_interface_conformance(compile_result, contracts, rubric)
    lint_result = run_lint(contracts)
    security_findings = grep_security_patterns(contracts)
    security_findings.extend(check_unbiased_dice_pattern(contracts))

    return {
        "contracts_analyzed": [str(c) for c in contracts],
        "compile": compile_result,
        "interface_conformance": interface_result,
        "lint": lint_result,
        "security_pattern_findings": security_findings,
    }
