from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "varies": 5}


def _all_static_findings(stage1: Dict):
    out = []
    sol = stage1.get("solidity") or {}
    out.extend(sol.get("security_pattern_findings", []))
    out.extend((sol.get("interface_conformance") or {}).get("findings", []))
    out.extend((stage1.get("manifest") or {}).get("findings", []))
    out.extend((stage1.get("ui") or {}).get("findings", []))
    return out


def build(submission_name: str, stage1: Dict, stage2: Dict) -> Dict:
    static_findings = _all_static_findings(stage1)
    ai_review = stage2.get("review") or {}
    ai_findings = ai_review.get("findings", []) if isinstance(ai_review, dict) else []

    critical_static = [f for f in static_findings if f.get("severity") == "critical" or f.get("rule_id", "").startswith("IFACE")]
    compile_ok = ((stage1.get("solidity") or {}).get("compile") or {}).get("ok")

    if compile_ok is False:
        recommendation = "reject_or_fix_required"
    elif critical_static:
        recommendation = "reject_or_fix_required"
    else:
        recommendation = ai_review.get("overall_recommendation", "manual_review_required")

    return {
        "submission": submission_name,
        "recommendation": recommendation,
        "stage1_static_analysis": stage1,
        "stage2_ai_review": stage2,
        "static_findings_count": len(static_findings),
        "ai_findings_count": len(ai_findings),
    }


def to_markdown(report: Dict) -> str:
    lines = [f"# Validation report: {report['submission']}", ""]
    lines.append(f"**Recommendation:** `{report['recommendation']}`")
    lines.append("")

    sol = report["stage1_static_analysis"].get("solidity") or {}
    compile_result = sol.get("compile") or {}
    lines.append("## Stage 1: Static analysis")
    lines.append(f"- Solidity compile: {'✅ OK' if compile_result.get('ok') else '❌ FAILED/SKIPPED'}")

    iface = sol.get("interface_conformance") or {}
    if iface.get("checked"):
        status = "✅ conforms" if not iface.get("missing_functions") and not iface.get("mutability_violations") else "❌ issues found"
        lines.append(f"- ICasinoGameV2 interface conformance: {status} (contract: `{iface.get('contract_target')}`)")
    else:
        lines.append(f"- ICasinoGameV2 interface conformance: ⚠️ not checked ({iface.get('reason')})")

    manifest = report["stage1_static_analysis"].get("manifest") or {}
    lines.append(f"- game.manifest.json: {'✅ valid' if manifest.get('checked') and not manifest.get('findings') else '❌ issues found' if manifest.get('checked') else '⚠️ not found'}")

    ui = report["stage1_static_analysis"].get("ui") or {}
    if ui.get("skipped_reason"):
        lines.append(f"- UI/bridge checks: ⚠️ skipped ({ui['skipped_reason']})")
    else:
        lines.append(f"- UI/bridge checks: {'✅ no issues' if not ui.get('findings') else '❌ issues found'}")

    all_static = _all_static_findings(report["stage1_static_analysis"])
    if all_static:
        lines.append("")
        lines.append("### Static findings")
        for f in sorted(all_static, key=lambda x: SEVERITY_ORDER.get(x.get("severity", "info"), 9)):
            title = f.get("title") or f.get("label", "finding")
            detail = f.get("detail") or f.get("snippet", "")
            lines.append(f"- **[{f.get('severity', '?')}] {f.get('rule_id')}** {title} -- {detail}")

    lint = sol.get("lint") or {}
    if lint.get("output", "").strip():
        lines.append("")
        lines.append("### forge lint output")
        lines.append("```")
        lines.append(lint["output"].strip())
        lines.append("```")

    lines.append("")
    lines.append("## Stage 2: AI-assisted review")
    stage2 = report["stage2_ai_review"]
    if stage2.get("error"):
        lines.append(f"⚠️ AI review did not complete: {stage2['error']}")
    else:
        review = stage2.get("review", {})
        if review.get("parse_error"):
            lines.append("⚠️ Could not parse AI review response as JSON. Raw output:")
            lines.append("```")
            lines.append(str(review.get("raw_result", ""))[:3000])
            lines.append("```")
        else:
            lines.append(f"**AI recommendation:** `{review.get('overall_recommendation', 'n/a')}`")
            lines.append("")
            lines.append(review.get("summary", ""))
            findings = review.get("findings", [])
            if findings:
                lines.append("")
                lines.append("### AI findings")
                for f in sorted(findings, key=lambda x: SEVERITY_ORDER.get(x.get("severity", "info"), 9)):
                    loc = f"{f.get('file')}:{f.get('line')}" if f.get("file") else ""
                    lines.append(f"- **[{f.get('severity', '?')}] {f.get('rule_id', 'OTHER')}** {f.get('title')} ({loc}, confidence: {f.get('confidence')})")
                    lines.append(f"  {f.get('explanation', '')}")
        cost = stage2.get("cost_usd")
        if cost is not None:
            lines.append("")
            lines.append(f"_AI review cost: ${cost:.4f}, {stage2.get('duration_ms')}ms_")

    lines.append("")
    lines.append("## Not automated in this pass")
    lines.append("- UI visual/functional walkthrough (spin up the game, click through flows) -- see README for the planned approach.")
    lines.append("- Live on-chain testing against the real CasinoGameFacet (whitelist-gated) -- only unit/mocked-level checks are possible pre-whitelisting.")
    lines.append("")

    return "\n".join(lines)


def write(out_dir: Path, submission_name: str, report: Dict) -> None:
    sub_out = out_dir / submission_name
    sub_out.mkdir(parents=True, exist_ok=True)
    (sub_out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (sub_out / "report.md").write_text(to_markdown(report))
