### Title
Bonus Reward Checkpoints Advance Without Payment on Insufficient Pool During Redelegation Slash — (`File: x/tieredrewards/keeper/slash.go`)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the position's reward checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) have already been mutated in memory via the `pos` pointer. The error is silently swallowed, and the position is then persisted with those advanced checkpoints. The user's accrued bonus for the entire period up to the slash is permanently forfeited with no recovery path.

### Finding Description

`processEventsAndClaimBonus` modifies the `*PositionState` pointer in-place — advancing `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` — **before** it checks whether the pool has sufficient balance: [1](#0-0) 

The checkpoint advancement at lines 215–217 happens unconditionally. The `sufficientBonusPoolBalance` guard at line 230 only fires afterward. If the pool is short, the function returns `ErrInsufficientBonusPool` — but the caller's `pos` already carries the advanced checkpoints.

In `slashRedelegationPosition`, this error is explicitly swallowed for chain-halt avoidance: [2](#0-1) 

Execution then falls through to `setPositionWithState`, which persists the position with the already-advanced checkpoints: [3](#0-2) 

On a **partial slash** (the common case), `ClearBonusCheckpoints` is never called, so the advanced `LastBonusAccrual` and `LastEventSeq` are written to state. The user can never reclaim the bonus that accrued between their last claim and the slash block, because the replay window has been permanently closed.

On a **full slash**, `ClearBonusCheckpoints()` is called at line 70, which resets the checkpoints — so the full-slash path does not exhibit this specific loss. The partial-slash path is the vulnerable one.

### Impact Explanation

The corrupted value is the position's `LastBonusAccrual` timestamp and `LastEventSeq` sequence number. After the partial-slash hook fires with an empty pool, these fields are written to state at a value that implies "bonus has been paid through block T", when in fact no payment was made. All bonus accrued from the previous claim up to block T is permanently destroyed. The user has no retry path because the replay window is closed.

The broken invariant stated in the ADR — *"Bonus cannot be double-claimed: `LastEventSeq` prevents event replay, `LastBonusAccrual` prevents segment replay"* — is exploited against the user: the same mechanism that prevents double-claiming also prevents the legitimate single claim from ever being retried. [4](#0-3) 

### Likelihood Explanation

Three independently realistic conditions must coincide:

1. **A position is in a pending redelegation** — any user who called `MsgTierRedelegate` and whose redelegation has not yet completed.
2. **The source validator is partially slashed** — routine protocol event; double-sign or downtime slashes are partial by default.
3. **The bonus pool is insufficient** — the `BeginBlocker` continuously drains the pool to top up base rewards; governance may not replenish it promptly. An empty pool is a documented, expected operational state. [5](#0-4) 

No privileged access is required. The slash is a normal consensus-layer event. The pool being empty is explicitly handled as a non-error condition in the BeginBlocker.

### Recommendation

Move the checkpoint advancement to **after** the successful payment, or — if the pool is insufficient — do not advance the checkpoints at all so the user can retry once the pool is replenished. Concretely, `applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` should only be called on the success path, not before the `sufficientBonusPoolBalance` guard. Alternatively, `slashRedelegationPosition` should revert the in-memory checkpoint changes on `ErrInsufficientBonusPool` before calling `setPositionWithState`.

### Proof of Concept

1. User A holds a tier position on validator V1 and calls `MsgTierRedelegate` to move to V2. The redelegation is pending (unbonding period not elapsed).
2. The bonus pool is drained to zero by the `BeginBlocker` top-up mechanism.
3. V1 is partially slashed (e.g., 5% downtime slash). The Cosmos SDK fires `BeforeRedelegationSlashed` with `unbondingId` and `sharesToUnbond < pos.Delegation.Shares`.
4. `slashRedelegationPosition` is called → `processEventsAndClaimBonus` is called on the position. It walks all pending events, computes `totalBonus > 0`, advances `pos.LastBonusAccrual` to the current block time and `pos.LastEventSeq` to the latest event, then returns `ErrInsufficientBonusPool`.
5. `slashRedelegationPosition` logs the error and continues. `pos.Delegation.Shares` is decremented. `setPositionWithState` persists the position with the advanced checkpoints.
6. The pool is later replenished. User A calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the advanced `LastBonusAccrual` and `LastEventSeq` — the entire pre-slash accrual period is gone. User A receives only bonus accrued after the slash block, permanently losing all prior accrual. [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-232)
```go
func (k Keeper) processEventsAndClaimBonus(ctx context.Context, pos *types.PositionState) (sdk.Coins, error) {
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()

	totalBonus := math.ZeroInt()
	// Use the persisted bonded state from the last replay, not a hardcoded default.
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
		}

		// Update bonded state based on event type.
		switch evt.EventType {
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
			bonded = false
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
			bonded = true
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH:
			// Slash doesn't change bonded state.
		}

		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
	}

	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return nil, err
	}
	// Defensive: validator bond status check
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}

	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return nil, err
	}

	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L19-77)
```go
func (k Keeper) slashRedelegationPosition(ctx context.Context, unbondingId uint64, sharesToUnbond math.LegacyDec) error {
	positionId, err := k.getRedelegationMapping(ctx, unbondingId)
	if errors.Is(err, collections.ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}

	pos, err := k.getPositionState(ctx, positionId)
	if errors.Is(err, types.ErrPositionNotFound) {
		k.logger(ctx).Error("position not found during redelegation slash",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"error", err.Error(),
		)
		return nil
	}
	if err != nil {
		return err
	}

	if !pos.IsDelegated() {
		// Defensive
		k.logger(ctx).Error("delegation missing during BeforeRedelegationSlashed",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"shares_to_unbond", sharesToUnbond.String(),
		)
		return nil
	}

	dstValStr := pos.Delegation.ValidatorAddress

	// Settle bonus against PRE-slash shares.
	if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
		// Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
		if errors.Is(err, types.ErrInsufficientBonusPool) {
			k.logger(ctx).Error("insufficient bonus pool during redelegation slash",
				"position_id", pos.Id,
				"error", err.Error(),
			)
		} else {
			return err
		}
	}

	fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)

	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
	// In-memory only: the persisted Position carries no share count, and the
	// live delegation will reflect the post-Unbond shares on the next read.
	// Update the local copy so any follow-up logic in this call sees consistent state.
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** doc/architecture/adr-006.md (L353-353)
```markdown
- Bonus cannot be double-claimed: `LastEventSeq` prevents event replay, `LastBonusAccrual` prevents segment replay, `LastKnownBonded` prevents unbonded gap overpay.
```

**File:** x/tieredrewards/keeper/abci.go (L96-111)
```go
	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
	topUpAmount := shortFallAmount
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}
```
