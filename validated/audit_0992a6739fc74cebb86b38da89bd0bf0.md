### Title
Unbounded Validator Event List in `processEventsAndClaimBonus` Can Permanently Lock Tier Position Funds - (File: `x/tieredrewards/keeper/claim_rewards.go`)

### Summary

`processEventsAndClaimBonus` loads all pending `ValidatorEvent` entries for a position into memory in a single call with no upper bound. If a position has not claimed rewards for an extended period on a validator that has experienced many lifecycle events (slashes, jailings, unjailings), the event list can grow large enough that processing it in a single transaction exceeds the block gas limit. Because every exit path for a delegated position requires reward settlement first, the position becomes permanently unexitable and the locked funds are irrecoverable.

### Finding Description

`getValidatorEventsSince` walks the entire `ValidatorEvents` store range for a validator from `pos.LastEventSeq` forward, appending every entry into an unbounded in-memory slice: [1](#0-0) 

This slice is then iterated in full inside `processEventsAndClaimBonus`, performing a store read, arithmetic, and a store write (ref-count decrement) per event: [2](#0-1) 

Events accumulate because the reference-counting GC only removes an event when **all** positions on the validator have processed it. A single dormant position prevents GC of every event since its `LastEventSeq`: [3](#0-2) 

Events are appended by three staking hooks — `BeforeValidatorSlashed`, `AfterValidatorBeginUnbonding`, `AfterValidatorBonded` — each time the validator has at least one delegated position: [4](#0-3) 

There is no cap on how many events can accumulate for a single validator, and no partial-processing path exists. `processEventsAndClaimBonus` must consume the entire pending list in one transaction.

### Impact Explanation

Every delegated-position exit path calls `claimRewards` (which calls `processEventsAndClaimBonus`) before performing the state mutation:

- `MsgTierUndelegate` — calls `claimRewards` before unbonding [5](#0-4) 
- `MsgTierRedelegate` — calls `claimRewards` before redelegating [6](#0-5) 
- `MsgExitTierWithDelegation` — calls `claimRewards` before transferring delegation [7](#0-6) 
- `MsgAddToTierPosition` — calls `claimRewards` before adding [8](#0-7) 
- `MsgClearPosition` — calls `claimRewards` before clearing exit [9](#0-8) 

`MsgWithdrawFromTier` requires the position to already be undelegated, which requires `MsgTierUndelegate` to have succeeded first. `MsgTriggerExitFromTier` does not settle rewards but does not release funds either.

If `processEventsAndClaimBonus` exceeds the block gas limit, **all** of the above messages fail permanently for that position. The locked tokens — transferred to the per-position delegator address at creation — cannot be recovered. The corrupted invariant is: `position.DelegatorAddress` holds funds that can never be returned to `position.Owner`.

### Likelihood Explanation

A position created on a validator that is subsequently slashed and jailed/unjailed repeatedly over months or years, without the owner ever claiming rewards, will accumulate one event per lifecycle transition. Cosmos chains with active slashing (double-sign, downtime) can produce dozens to hundreds of events per year per validator. A position dormant for several years on an active validator can accumulate enough events that a single `processEventsAndClaimBonus` call exceeds the block gas limit. No privileged role is required; the accumulation happens through normal chain operation. The entry path is any `MsgLockTier` or `MsgCommitDelegationToTier` followed by inactivity. [10](#0-9) 

### Recommendation

Introduce a maximum number of events processed per `processEventsAndClaimBonus` call (e.g., a module parameter `MaxEventsPerClaim`). If the pending event list exceeds this cap, process only up to the cap, advance `LastEventSeq` and `LastBonusAccrual` to the last processed event, and return the partial bonus. The caller can invoke again in a subsequent transaction to continue. This mirrors the two-step approach suggested in M-01: break the unbounded single-call operation into bounded incremental steps.

Additionally, consider enforcing a per-validator event cap at append time (e.g., reject new events if the unprocessed backlog exceeds a threshold), or expose a dedicated `MsgPartialClaimBonus` message that processes at most N events per call.

### Proof of Concept

1. Alice calls `MsgLockTier` on validator V, creating position P with `LastEventSeq = 0`.
2. Over the next several years, validator V is slashed 500 times and jailed/unjailed 500 times. Each event appends one entry to `ValidatorEvents[(V, seq)]` with `ReferenceCount >= 1` (Alice's position keeps the count non-zero, preventing GC).
3. Alice attempts `MsgTierUndelegate` (or any other exit message). The handler calls `claimRewards` → `processEventsAndClaimBonus` → `getValidatorEventsSince(ctx, V, 0)`, which loads all 1000+ events into memory and iterates them.
4. The transaction runs out of gas (or exceeds the block gas limit). The message fails.
5. Every subsequent exit attempt fails identically. Alice's funds are permanently locked in the per-position delegator address. [1](#0-0) [11](#0-10)

### Citations

**File:** x/tieredrewards/keeper/validator_events.go (L67-81)
```go
func (k Keeper) getValidatorEventsSince(ctx context.Context, valAddr sdk.ValAddress, startSeq uint64) ([]EventEntry, error) {
	// Range from (valAddr, startSeq+1) to end of valAddr prefix.
	rng := collections.NewPrefixedPairRange[sdk.ValAddress, uint64](valAddr).
		StartExclusive(startSeq)

	var entries []EventEntry
	err := k.ValidatorEvents.Walk(ctx, rng, func(key collections.Pair[sdk.ValAddress, uint64], event types.ValidatorEvent) (bool, error) {
		entries = append(entries, EventEntry{Seq: key.K2(), Event: event})
		return false, nil
	})
	if err != nil {
		return nil, err
	}
	return entries, nil
}
```

**File:** x/tieredrewards/keeper/validator_events.go (L83-101)
```go
// decrementEventRefCount decrements the reference count of a validator event.
// If the reference count reaches zero, the event is deleted.
func (k Keeper) decrementEventRefCount(ctx context.Context, valAddr sdk.ValAddress, seq uint64) error {
	key := collections.Join(valAddr, seq)
	event, err := k.ValidatorEvents.Get(ctx, key)
	if errors.Is(err, collections.ErrNotFound) {
		return nil // already cleaned up
	}
	if err != nil {
		return err
	}

	if event.ReferenceCount <= 1 {
		return k.ValidatorEvents.Remove(ctx, key)
	}

	event.ReferenceCount--
	return k.ValidatorEvents.Set(ctx, key, event)
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-199)
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
```

**File:** x/tieredrewards/keeper/hooks.go (L100-123)
```go
func (h Hooks) BeforeValidatorSlashed(ctx context.Context, valAddr sdk.ValAddress, fraction sdkmath.LegacyDec) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}

	tokensPerShare, err := h.k.getTokensPerShare(ctx, valAddr)
	if err != nil {
		return err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L166-169)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L229-232)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L314-317)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L406-409)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L532-535)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```
