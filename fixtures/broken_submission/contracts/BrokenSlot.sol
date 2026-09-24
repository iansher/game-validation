// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

// NOTE: this fixture is intentionally broken. It exists to prove the
// validation pipeline's static + AI review checks actually fire, and is
// used as a negative test case alongside the real `happydaze` submission
// (used as the positive/known-good smoke test). Every planted bug below is
// referenced in fixtures/broken_submission/README.md.

import {SessionContext, StepResult, SessionPhase} from "./ICasinoGameV2.sol";

// Deliberately does NOT declare `is ICasinoGameV2` -- a submitter who
// pattern-matched the function names/signatures without formally
// implementing the interface. This is exactly the case forge's own
// override-mutability compile check can't catch (see IFACE-02).
contract BrokenSlot {
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    // BUG (SEC-01): unrestricted selfdestruct backdoor, callable by anyone.
    function drain() external {
        selfdestruct(payable(msg.sender));
    }

    // BUG (SEC-03): tx.origin used for an auth check (phishing-vulnerable).
    function setOwner(address newOwner) external {
        require(tx.origin == owner, "not owner");
        owner = newOwner;
    }

    function quoteCaps(
        uint256 wager,
        bytes calldata gameData
    ) external pure returns (uint256 maxEscrowStake, uint256 maxReservedProfit) {
        // BUG (SEM-01): decodes gameData as a single uint256 here...
        uint256 topMultiplier = abi.decode(gameData, (uint256));
        maxEscrowStake = wager;
        maxReservedProfit = wager * topMultiplier;
    }

    function quoteRiskParams(
        uint256 wager,
        bytes calldata /* gameData */
    )
        external
        pure
        returns (
            uint256 maxPayout,
            uint256 probabilityWad,
            uint256 expectedPayout,
            uint256 subJackpotVarianceScaled
        )
    {
        // BUG (SEM-03/SEM-04): quotes a 2x cap and a flat 50% top-tier
        // probability regardless of the actual jackpot logic in
        // onRandomness below, which can pay out far more than 2x at much
        // lower real odds than 50%.
        maxPayout = wager * 2;
        probabilityWad = 5e17; // 50%, hardcoded, not derived from any paytable
        expectedPayout = wager * 96 / 100;
        subJackpotVarianceScaled = 0;
    }

    function onSessionStart(
        SessionContext calldata ctx
    ) external pure returns (StepResult memory stepResult) {
        stepResult.newGameState = "";
        stepResult.escrowDelta = 0;
        stepResult.reservedProfitDelta = int256(ctx.wagerBase * 2);
        stepResult.nextPhase = SessionPhase.WAITING_RANDOMNESS;
        stepResult.requestRandomnessNow = true;
        stepResult.payout = 0;
    }

    // BUG (IFACE-02): required by the interface to be `view`, but declared
    // with default (nonpayable) mutability here -- undetectable by solc's
    // own override check specifically because this contract never formally
    // `is ICasinoGameV2`/`override`s it.
    function onPlayerAction(
        SessionContext calldata,
        bytes calldata
    ) external returns (StepResult memory) {
        revert("no player action");
    }

    function onRandomness(
        SessionContext calldata ctx,
        bytes32 randomness
    ) external view returns (StepResult memory stepResult) {
        // BUG (SEC-05): mixes block.timestamp into the "random" outcome,
        // making it manipulable by a miner/validator within their
        // block-timestamp slack.
        uint256 r = uint256(randomness) ^ block.timestamp;

        // BUG (SEM-01 continued): decodes gameData with a DIFFERENT shape
        // than quoteCaps did above (two uint256s here vs. one there).
        (uint256 base, uint256 bonus) = abi.decode(ctx.gameData, (uint256, uint256));

        uint256 payout;
        if (r % 1000 == 0) {
            // BUG (SEM-03): jackpot branch pays up to 50x wager, but
            // quoteRiskParams above only ever quoted a 2x maxPayout cap.
            payout = ctx.wagerBase * 50;
        } else {
            payout = (ctx.wagerBase * base) / (bonus == 0 ? 1 : bonus);
        }

        stepResult.newGameState = abi.encode(payout);
        stepResult.escrowDelta = 0;
        stepResult.reservedProfitDelta = -int256(ctx.reservedProfit);
        stepResult.nextPhase = SessionPhase.SETTLED;
        stepResult.requestRandomnessNow = false;
        stepResult.payout = payout;
    }

    // BUG (IFACE-01): quoteForfeitPayout is entirely missing, even though
    // it's a required part of the interface.
}
