# fixtures/broken_submission

A deliberately-buggy "submission" used to prove the pipeline's checks
actually fire, since no real hackathon submissions exist yet. Paired with
the real `happydaze` repo (used unmodified as the known-good/positive case).

Planted bugs, by rule id (see `rubric/rubric.json` for what each id means):

| Rule | Where | What |
|---|---|---|
| IFACE-01 | `BrokenSlot.sol` | `quoteForfeitPayout` entirely missing |
| IFACE-02 | `BrokenSlot.sol` | `onPlayerAction` missing `view` (contract doesn't formally `is ICasinoGameV2`, so solc's own override check can't catch this) |
| SEC-01 | `BrokenSlot.sol` | unrestricted `selfdestruct` backdoor in `drain()` |
| SEC-03 | `BrokenSlot.sol` | `tx.origin` used for an auth check |
| SEC-05 | `BrokenSlot.sol` | `block.timestamp` mixed into the "random" outcome |
| SEM-01 | `BrokenSlot.sol` | `gameData` decoded as one `uint256` in `quoteCaps`, two in `onRandomness` |
| SEM-03 | `BrokenSlot.sol` | jackpot branch pays `wager * 50`; `quoteRiskParams` only ever quotes a 2x cap |
| SEM-04 | `BrokenSlot.sol` | `probabilityWad` hardcoded to 50%, real jackpot odds are ~0.1% |
| SEM-05 | `BrokenSlot.sol` | `expectedPayout` is a flat 96%-of-wager constant, unrelated to the actual paytable |
| SEM-08 | `guest-bridge.js` | UI renders a payout computed from `window.__debugMultiplier` instead of host-pushed state |
| MANIFEST-06 | `game.manifest.json` | `presentation.mode: "popup"` (invalid) |
| MANIFEST-08 | `game.manifest.json` | `hostPanels` missing the required `status` key |
| MANIFEST-09 | `game.manifest.json` | `capabilities.openSession: false` (must be `true`) |
| UI-01 | `guest-bridge.js` | guest code calls `walletClient.sendTransaction(...)` directly instead of going through `hostApi` |
| UI-02 | `guest-bridge.js` | `allowedOrigins` hardcoded to `'*'` |

Run it:

```sh
python3 ../../pipeline/run_pipeline.py . --skip-ai   # stage 1 only
python3 ../../pipeline/run_pipeline.py .              # + AI review
```
