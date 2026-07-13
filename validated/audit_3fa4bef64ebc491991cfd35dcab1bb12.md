### Title
ExitUnlockAt timer advances during circuit-breaker pause, permanently truncating bonus rewards and blocking redelegate — (`x/tieredrewards/keeper/msg_validate.go`, `x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

The `x/tieredrewards` module uses a wall-clock `ExitUnlockAt` timestamp as a hard deadline for two irreversible consequences: (1) `MsgTierRedelegate` is permanently blocked once the deadline elapses, and (2) bonus reward accrual is hard-capped at that timestamp. Neither the redelegate gate nor the bonus cap accounts for the duration of a circuit-breaker pause. When governance disables tieredrewards message types via the Cosmos SDK `x/circuit` module (a legitimate emergency response), the `ExitUnlockAt` timer continues to advance while users cannot call `MsgClearPosition` to cancel their exit. When the pause lifts, users whose `ExitUnlockAt` elapsed during the pause permanently lose bonus rewards for the entire post-deadline period and temporarily lose the ability to redelegate.

---

### Finding Description

**Entry path.** The Cosmos SDK `x/circuit` module is registered in `ChainApp` and listed in the module manager's EndBlocker order. [1](#0-0) 

Governance (or an authorized address) can trip the circuit for any message type URL, causing all transactions containing that message to be rejected. This is the protocol-level pause analog.

**Timer set at trigger.** When a user calls `MsgTriggerExitFromTier`, the keeper sets `ExitUnlockAt = blockTime + tier.ExitDuration`. [2](#0-1) 

This timestamp is a wall-clock value. It advances regardless of whether the chain is paused via the circuit breaker.

**Consequence 1 — redelegate permanently blocked.** `validateRedelegatePosition` returns `ErrExitLockDurationElapsed` the moment `ExitUnlockAt` elapses: [3](#0-2) 

There is no mechanism to extend `ExitUnlockAt` by the pause duration. Once the deadline passes, `MsgTierRedelegate` is permanently rejected for that position until the user calls `MsgClearPosition` to reset the exit state — but `MsgClearPosition` itself may also be circuit-tripped.

**Consequence 2 — bonus rewards hard-capped at ExitUnlockAt.** `computeSegmentBonus` clips `segmentEnd` to `ExitUnlockAt` when the exit commitment has elapsed: [4](#0-3) 

`applyBonusAccrualCheckpoint` also advances `LastBonusAccrual` only up to `ExitUnlockAt` when the exit is complete: [5](#0-4) 

Any bonus that would have accrued between `ExitUnlockAt` and the moment the user can finally call `MsgClearPosition` (after the pause lifts) is permanently lost. The rewards pool retains those tokens; the user cannot claim them.

**Concrete scenario.**
1. User triggers exit on a 1-year tier; `ExitUnlockAt = T + 1 year`.
2. At `T + 11 months`, governance trips the circuit for `MsgClearPosition` and `MsgTierRedelegate` (emergency response to a discovered bug).
3. The governance voting period + fix + second proposal takes 2 months.
4. At `T + 13 months`, the circuit is reset. `ExitUnlockAt` elapsed 1 month ago.
5. User calls `MsgClearPosition` to reset exit state — succeeds, but bonus rewards for the 1-month post-`ExitUnlockAt` window are permanently lost.
6. Until `ClearPosition` is called, `MsgTierRedelegate` is also blocked.

---

### Impact Explanation

Users who have triggered exit and whose `ExitUnlockAt` elapses during a circuit-breaker pause suffer a direct, unrecoverable loss of bonus rewards proportional to the pause duration. The rewards pool retains the unclaimed tokens; no redistribution occurs. Additionally, `MsgTierRedelegate` is blocked until the user calls `MsgClearPosition`, preventing validator risk management during the pause window. Both consequences are outside the user's control.

---

### Likelihood Explanation

The `x/circuit` module is a production feature of the chain. Governance-triggered circuit trips are a realistic emergency response (e.g., a bug discovered in the tieredrewards module). Governance voting periods are typically days to weeks, and a second proposal to lift the circuit adds further delay. The BendDAO judge's reasoning applies directly: "pause durations are intended to be small, they can be arbitrarily long and put users at risk on unpausing."

---

### Recommendation

Store the cumulative paused duration for each position (or globally) and extend `ExitUnlockAt` by that duration when the circuit is reset. Alternatively, record a global `pauseStart` / `pauseEnd` timestamp pair and subtract the overlap from `ExitUnlockAt` comparisons in `validateRedelegatePosition` and `computeSegmentBonus`. The ADR-006 design already tracks `LastBonusAccrual` as a checkpoint; the same pattern can be applied to pause-adjusted deadlines.

---

### Proof of Concept

```
// Pseudocode illustrating the loss

// Before pause: ExitUnlockAt = T+1yr, bonus accrues normally
// Circuit tripped at T+11mo: MsgClearPosition disabled
// ExitUnlockAt elapses at T+12mo (during pause)
// Circuit reset at T+13mo

// At claim time (T+13mo):
segmentEnd = min(blockTime, pos.ExitUnlockAt)
           = min(T+13mo, T+12mo)
           = T+12mo   // 1 month of bonus silently dropped

// validateRedelegatePosition at T+13mo (before ClearPosition):
pos.HasTriggeredExit() && pos.CompletedExitLockDuration(blockTime)
=> true && true => ErrExitLockDurationElapsed  // redelegate blocked
```

The corrupted value is the user's accrued bonus reward entitlement in the `RewardsPool` module account: tokens the user earned but cannot claim because `ExitUnlockAt` was not extended to account for the pause duration. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** app/app.go (L664-666)
```go
		consensusparamtypes.ModuleName,
		circuittypes.ModuleName,
	)
```

**File:** x/tieredrewards/keeper/msg_server.go (L365-368)
```go

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

```

**File:** x/tieredrewards/keeper/msg_validate.go (L80-83)
```go
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if pos.HasTriggeredExit() && pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationElapsed
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L15-21)
```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
	accrualEnd := blockTime
	if pos.CompletedExitLockDuration(blockTime) {
		accrualEnd = pos.ExitUnlockAt
	}
	pos.UpdateLastBonusAccrual(accrualEnd)
}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L25-32)
```go
func (k Keeper) computeSegmentBonus(pos types.PositionState, tier types.Tier, segmentStart, segmentEnd time.Time, tokensPerShare math.LegacyDec) math.Int {
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
		segmentEnd = pos.ExitUnlockAt
	}

	if !segmentEnd.After(segmentStart) {
		return math.ZeroInt()
	}
```
