// Fixture: intentionally broken guest/host bridge code, for pipeline testing.
// See ../README.md for the full list of planted bugs.

import { connectGameToHost } from "@chain-wtf/games-sdk/guest";

// BUG (UI-02): allowedOrigins hardcoded to '*' -- fine for local dev per
// VISUAL_AND_UX.md, but this submission ships it as-is with no
// production/stable-origin guard.
const allowedOrigins = "*";

let hostApi = null;

async function init() {
  const connection = connectGameToHost(
    {
      async setState(snapshot) {
        render(snapshot);
      },
    },
    { allowedOrigins }
  );
  hostApi = await connection.promise;
}

async function spin(wager, gameData) {
  const { sessionKey } = await hostApi.openSession({ wager, gameData, randomnessRequestData: "0x" });

  // BUG (UI-01): guest code directly signs/broadcasts instead of going
  // through hostApi -- e.g. a "claim bonus" side-transaction the submitter
  // bolted on outside the SDK flow.
  await walletClient.sendTransaction({
    to: BONUS_CONTRACT_ADDRESS,
    data: encodeClaimBonus(sessionKey),
  });

  return sessionKey;
}

function render(snapshot) {
  // BUG (SEM-08): computes and displays a payout locally from a
  // client-supplied multiplier instead of only ever rendering
  // host-pushed on-chain state -- a player could tamper with
  // `window.__debugMultiplier` to change what's displayed (and,
  // depending on how the rest of the UI trusts this value, what's claimed).
  const multiplier = window.__debugMultiplier || 1;
  const displayedPayout = (snapshot?.wallet?.lastWager || 0) * multiplier;
  document.getElementById("payout").innerText = displayedPayout;
}

init();
