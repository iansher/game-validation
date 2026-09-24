# Chain game-jam submission validation -- POC

Automates the first pass of reviewing hackathon game submissions (Solidity
contract implementing `ICasinoGameV2` + a web UI/host-bridge) before a human
looks at them. This is a proof of concept, scoped to two of the four things
Ian asked about:

1. **Static analysis** -- compilation, SDK interface conformance, manifest
   schema validation, and pattern-based security/UI checks. Deterministic,
   fast, free.
2. **AI-assisted code review** -- an LLM review pass over what static
   analysis structurally can't check: cross-function consistency, payout/RTP
   correctness, phase-machine logic, UI-contract integration, general
   "does this look like it's trying to cheat the risk engine" review.

**Not built yet** (see "Not automated in this pass" in each report, and
"Next steps" below): spinning up the game and clicking through the UI. Ian's
own framing already assumes a human does the final UI review, so stage 3 is
scoped as "produce a recording/trace to make that human review faster," not
"replace it."

## Why this is grounded, not generic

This isn't written against an imagined SDK. `rubric/rubric.json` was built
by reading:
- The actual SDK docs bundle (`casino-games-docs`: `CONTRACT_CONSTRAINTS.md`,
  `SLOTS_RISK_AND_RESERVES.md`, `VISUAL_AND_UX.md`, `REPO_STRUCTURE.md`,
  `CHAIN_WTF_CASINO_GAMES.md`, `games-sdk/`).
- The protocol contracts repo (`chain-contracts-main`).
- A real, working game integration (`happydaze`) used here as the
  known-good smoke-test fixture.

**Important finding from that process: the SDK interface itself has version
drift.** Three local copies of `ICasinoGameV2.sol` disagree with each other:

| Source | has `outcome` field | has `quoteRiskParams` | has `quoteForfeitPayout` |
|---|---|---|---|
| `chain-contracts-main` (protocol repo) | yes | **no** | **no** |
| SDK docs bundle | yes | yes | **no** |
| `happydaze` (real shipped integration) | **no** | yes | yes |

`rubric/ICasinoGameV2.sol` uses happydaze's version (the superset) as the
reference for interface-conformance checks, since it's the most complete and
belongs to a real working integration. **This needs a real answer from
whoever owns the protocol repo before this pipeline is trusted for real
submissions** -- if the wrong interface version is authoritative, the
interface-conformance stage will produce false positives or negatives
against real entries. Flagging this rather than guessing was more useful
than silently picking one.

## Layout

```
rubric/
  ICasinoGameV2.sol   -- reference interface (see version-drift note above)
  rubric.json          -- every check, tagged static (deterministic,
                          run in stage 1) or ai (semantic, run in stage 2),
                          each with a rule id, category, severity, and a
                          description traceable back to the SDK docs
pipeline/
  submission.py         -- heuristic discovery of contracts/manifest/UI
                            files in a submission dir (no fixed layout is
                            required per REPO_STRUCTURE.md, so this is glob
                            + filename/pattern based, overridable via flags)
  stage1_solidity.py     -- forge build / forge inspect (ABI+selectors) /
                            forge lint + regex security-pattern checks
  stage1_manifest.py     -- game.manifest.json schema validation
  stage1_ui.py           -- guest JS/TS pattern checks + npm audit
  stage2_ai_review.py    -- builds the review prompt from rubric.json's
                            `ai` rules + stage 1 findings + source, calls
                            the AI, parses structured JSON back out
  report.py              -- merges both stages into report.json + report.md
  run_pipeline.py         -- CLI entrypoint
fixtures/
  broken_submission/     -- a deliberately-buggy fixture (10+ planted bugs,
                            see its own README) used to prove the checks
                            actually fire, since no real submissions exist
                            yet
out/
  <submission>/report.{json,md}
```

## Usage

```sh
python3 pipeline/run_pipeline.py <path-to-submission-repo>
python3 pipeline/run_pipeline.py <path> --skip-ai        # stage 1 only, fast/free
python3 pipeline/run_pipeline.py <path> --model claude-sonnet-5
python3 pipeline/run_pipeline.py <path> --contract src/MyGame.sol --manifest src/game.manifest.json --ui-dir src/guest
```

No dependencies to install: pure Python 3 stdlib, shelling out to `forge`
(already on this machine), `npm`, and the `claude` CLI. Nothing needs `pip`.

## Validated against

- **`happydaze`** (`/home/ian/chain/happydaze`, real working integration) --
  run as the positive case. Stage 1: compiles clean, interface conforms,
  manifest valid, no UI flags. Stage 2 AI review: no false-positive
  critical/high findings on real production code (it explicitly confirmed
  the payout-never-exceeds-cap and probability-targets-top-tier checks
  passed), but it did surface five genuine, non-obvious, low/medium-severity
  issues worth a human's attention: **a real off-by-one in the weighted-draw
  boundary condition** (`r <= cumulative` vs. `r < cumulative`, slightly
  under-weighting the jackpot tier vs. its quoted `probabilityWad`), a
  `?debugForce=` payout bypass shipped live in the production UI bridge (not
  gated to a dev build), `subJackpotVarianceScaled` hardcoded to 0 despite
  this being a textbook heavy-tail jackpot game, no forfeit/cancel path if a
  session gets stuck awaiting randomness, and the `'*'`-origin Penpal
  fallback the SDK docs themselves flag as dev-only. This is a much better
  signal than "found nothing" -- it shows the review engages with the
  specific code rather than reciting the rubric.
- **`fixtures/broken_submission`** -- 10+ deliberately planted bugs spanning
  every rule category. Stage 1 caught all 10 statically-checkable ones
  (missing interface function, wrong mutability, `selfdestruct`, `tx.origin`,
  `block.timestamp`-as-randomness, bad manifest fields, wallet-signing in
  guest code, `'*'` postMessage origin). Stage 2 AI review caught every
  planted semantic bug it was supposed to (payout can exceed quoted
  maxPayout, `probabilityWad` hardcoded and unrelated to real odds,
  inconsistent `gameData` decoding across functions, client-trusted payout
  display) **plus one I hadn't scripted**: no forfeit/cancel path exists if
  a session gets stuck in `WAITING_RANDOMNESS`, since `quoteForfeitPayout`
  is missing and the manifest disables both recovery capabilities. See
  `out/broken_submission/report.md` for the full transcript.

## Design decisions and known limitations (read before relying on this)

- **AI review uses the local `claude` CLI (`claude -p --output-format
  json`), not a raw Anthropic API key.** No `ANTHROPIC_API_KEY` is set in
  this environment, and the CLI is already authenticated, so this is what
  let me actually run and validate the AI stage today rather than just
  describe it. For a production pipeline running unattended against ~50+
  submissions on a schedule, **switch `stage2_ai_review.py` to the Anthropic
  Messages API directly** (Python/TS SDK): it's cheaper per call (no CLI
  agent system-prompt/session overhead on top of the actual review -- the
  two real reviews run here cost $0.59-0.67 and took 2-5 minutes each,
  scaling with submission size), supports concurrent calls so 50 submissions
  don't run one-at-a-time, and lets you enforce the response schema with
  tool-use instead of asking nicely and parsing text out of a CLI envelope.
- **Interface conformance depends on `forge build` succeeding.** A
  submission using a dependency not vendored in its repo (e.g. imports
  `@openzeppelin/contracts` without `node_modules` committed, no
  `foundry.toml`/remappings) will fail to compile here, and the pipeline
  reports that explicitly rather than guessing -- it does not fall back to
  a regex-based signature check. Given no real submissions exist yet to
  test this against, treat it as a known gap: worth adding a best-effort
  regex fallback once real submission repo shapes are visible.
- **Submission discovery is heuristic**, per REPO_STRUCTURE.md's "no
  required folder layout." It globs for `*.sol` / `game.manifest.json` /
  JS-TS files matching bridge-ish filenames, skipping `node_modules`, `lib`,
  `out`, etc. Works well on `happydaze`'s and the fixture's layout; real
  submissions may need `--contract`/`--manifest`/`--ui-dir` overrides, or a
  submission form that just asks for these three paths directly (cheaper
  than smarter discovery).
- **`quoteRiskParams`'s runtime-correct values (`probabilityWad` ≤ 1e18,
  payout-never-exceeds-cap across every path) are not exhaustively verified
  by anything here** -- the AI review reasons about them from reading the
  code, which is exactly what caught the planted bugs above, but it's
  pattern-matching/reasoning, not exhaustive. A real fuzz/property test
  harness (Foundry `forge test --fuzz`, generated per-submission from
  `quoteRiskParams`'s own claims) would catch what a code-reading pass
  might miss, at the cost of someone having to write or generate
  submission-specific test scaffolding. Worth a follow-up POC.
- **This can't test against the real `CasinoGameFacet`** -- the docs are
  explicit that only whitelisted contract addresses can be invoked by the
  facet at all, so nothing here does a live `openSession` call. Everything
  is source-level (static + AI).

## Not automated in this pass: the UI walkthrough

Ian's message already assumes a human reviews the actual game UI, so the
ambition here is "make that review fast and evidenced," not "replace it."
The shape I'd build next, once stage 1+2 are stable:

1. Serve the submission's guest build statically + a minimal mock host
   (there's a real local simulator already -- `casino-sdk`'s
   `LOCAL_SIMULATOR.md`/`npm start` -- reuse it rather than building a
   second one).
2. Drive it with an agentic browser tool (e.g. Playwright driven by an
   LLM, or Claude's own computer-use-style browser control) that opens a
   session, places a bet, and clicks through whatever flow the manifest's
   `capabilities` declare (submitAction, forfeit, etc.), recording video.
3. Treat this as evidence generation, not pass/fail: attach the recording
   + a short written trace of what it did and observed to the report for
   the human reviewer, flag anything that looks like a stall/error, but
   don't gate approval on it succeeding autonomously -- arbitrary
   third-party UIs are too varied for a scripted flow to reliably drive
   without false negatives.

This is explicitly the hardest and least certain part of the whole ask (see
the earlier scoping discussion) and deserves its own POC pass rather than
being bolted onto this one.

## Open questions for whoever owns the protocol/SDK repos

1. **Which `ICasinoGameV2.sol` is actually current** -- see the version-drift
   table above. This blocks trusting the interface-conformance check for
   real submissions.
2. Is there (or should there be) a fixed **submission format** (a few
   required paths/fields) rather than free-form repo discovery? Even just
   "contract path, manifest path, guest build URL" declared explicitly in a
   submission form would remove the biggest heuristic-guessing risk in this
   pipeline.
3. Do you want a **hard RTP/house-edge bound** enforced, or just internal
   consistency (quoted vs. simulated)? SLOTS_RISK_AND_RESERVES.md doesn't
   state a mandated range, so `rubric.json` only checks consistency --
   confirm that's intentional before this becomes a gate.
