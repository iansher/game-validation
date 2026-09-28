// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

enum SessionPhase {
  NONE,
  WAITING_RANDOMNESS,
  WAITING_PLAYER_ACTION,
  SETTLED,
  FORFEITED,
  CANCELLED
}

struct SessionContext {
  uint256 sessionId;
  address player;
  address vault;
  uint256 wagerBase;
  uint256 escrowedStake;
  uint256 reservedProfit;
  uint32 step;
  bytes gameData;
  bytes gameState;
}

struct StepResult {
  bytes newGameState;
  int256 escrowDelta;
  int256 reservedProfitDelta;
  SessionPhase nextPhase;
  bool requestRandomnessNow;
  uint256 payout;
}

interface ICasinoGameV2 {
  function quoteCaps(
    uint256 wager,
    bytes calldata gameData
  ) external view returns (uint256 maxEscrowStake, uint256 maxReservedProfit);

  /// @notice Risk parameters for portfolio VaR. `probabilityWad` is win probability in WAD (1e18 = 100%) for the VaR binary / tail model.
  /// @notice `bodyVarianceScaled` is the body variance (variance of the round's payout with the
  ///         top tier removed), counted on every bet, in wei^2 x 1e18. 0 only if the top tier is
  ///         the sole winning outcome (renamed from `subJackpotVarianceScaled`; same position and
  ///         type, so the ABI encoding is unchanged). A multi-tier paytable whose top multiplier
  ///         exceeds the heavy-tail threshold cannot be whitelisted while quoting 0 here unless
  ///         the security council has registered a per-game sigma floor.
  function quoteRiskParams(
    uint256 wager,
    bytes calldata gameData
  )
    external
    view
    returns (
      uint256 maxPayout,
      uint256 probabilityWad,
      uint256 expectedPayout,
      uint256 bodyVarianceScaled
    );

  function onSessionStart(
    SessionContext calldata ctx
  ) external view returns (StepResult memory);

  function onPlayerAction(
    SessionContext calldata ctx,
    bytes calldata actionData
  ) external view returns (StepResult memory);

  function onRandomness(
    SessionContext calldata ctx,
    bytes32 randomness
  ) external view returns (StepResult memory);

  /// @notice Current cash-out value (stake + accrued winnings) of an in-progress session,
  ///         derived from `ctx.gameState`. Called by the host when forfeiting an abandoned
  ///         session so the player keeps most of their current winnings instead of losing
  ///         the whole stake. Return 0 when nothing is cashable mid-round.
  function quoteForfeitPayout(SessionContext calldata ctx) external view returns (uint256 cashoutValue);
}
