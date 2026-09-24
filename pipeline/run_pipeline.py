#!/usr/bin/env python3
"""CLI entrypoint: run the static-analysis + AI-review pipeline over one
submission directory.

Usage:
    python3 pipeline/run_pipeline.py <submission_dir> [options]

Options:
    --contract PATH     Explicit path to the game contract (skip auto-discovery)
    --manifest PATH     Explicit path to game.manifest.json
    --ui-dir PATH       Explicit directory to search for guest UI/bridge code
    --out DIR           Where to write reports (default: ./out)
    --skip-ai           Run only stage 1 (static analysis), skip the AI review
    --model MODEL       Model to use for AI review (default: claude-sonnet-5)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import submission as submission_mod
from pipeline import stage1_solidity, stage1_manifest, stage1_ui, stage2_ai_review, report as report_mod

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_rubric() -> dict:
    return json.loads((PROJECT_ROOT / "rubric" / "rubric.json").read_text())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("submission_dir", type=Path)
    ap.add_argument("--contract", type=Path, default=None)
    ap.add_argument("--manifest", type=Path, default=None)
    ap.add_argument("--ui-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "out")
    ap.add_argument("--skip-ai", action="store_true")
    ap.add_argument("--model", default=stage2_ai_review.DEFAULT_MODEL)
    ap.add_argument("--ai-timeout", type=int, default=420,
                     help="seconds to allow the AI review call (default 420 -- larger submissions with more code/comments take longer)")
    args = ap.parse_args()

    if not args.submission_dir.exists():
        print(f"error: {args.submission_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    rubric = load_rubric()
    sub = submission_mod.discover(args.submission_dir, args.contract, args.manifest, args.ui_dir)

    print(f"== {sub.name} ==")
    print(f"contracts:   {[str(c) for c in sub.contracts] or '(none found)'}")
    print(f"manifest:    {sub.manifest or '(none found)'}")
    print(f"ui files:    {len(sub.ui_files)} file(s)")
    print()

    t0 = time.time()
    print("[stage 1] running Solidity static analysis (forge build/inspect/lint)...")
    solidity_result = stage1_solidity.analyze(sub.contracts, rubric)
    print(f"[stage 1] running manifest validation...")
    manifest_result = stage1_manifest.validate(sub.manifest)
    print(f"[stage 1] running UI/bridge static checks...")
    ui_result = stage1_ui.analyze(sub.ui_files, sub.package_json)
    stage1 = {"solidity": solidity_result, "manifest": manifest_result, "ui": ui_result}
    print(f"[stage 1] done in {time.time() - t0:.1f}s")
    print()

    if args.skip_ai:
        stage2 = {"error": "skipped via --skip-ai"}
    else:
        print(f"[stage 2] running AI-assisted review (model={args.model})...")
        t1 = time.time()
        stage2 = stage2_ai_review.run(
            sub.name, sub.contracts, sub.manifest, sub.ui_files, stage1, rubric, model=args.model,
            timeout=args.ai_timeout,
        )
        print(f"[stage 2] done in {time.time() - t1:.1f}s"
              + (f", cost ${stage2['cost_usd']:.4f}" if stage2.get("cost_usd") else ""))
        if stage2.get("error"):
            print(f"[stage 2] WARNING: {stage2['error']}")
        print()

    report = report_mod.build(sub.name, stage1, stage2)
    report_mod.write(args.out, sub.name, report)

    print(f"Recommendation: {report['recommendation']}")
    print(f"Report written to: {args.out / sub.name / 'report.md'}")


if __name__ == "__main__":
    main()
