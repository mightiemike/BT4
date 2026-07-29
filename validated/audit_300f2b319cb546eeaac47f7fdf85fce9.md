### Title
Stale chain metadata votes persist in median calculation due to lack of removal mechanism - ([File: x/uexecutor/keeper/chain_meta.go])

### Summary
The Chain Meta Oracle in Push Chain aggregates gas prices and block heights from Universal Validators using a median calculation. However, the system only supports adding or updating votes. If a validator stops voting or their view of the external chain becomes outdated but still falls within the `chainMetaVoteStalenessSeconds` window (or if the window is large), their previous (potentially much higher or lower) values remain in the `entry.Prices` and `entry.ChainHeights` slices indefinitely. This can lead to permanent corruption of the median if a significant number of validators report values that later decrease or fluctuate, as the "accumulator" style storage of votes never removes stale entries, only ignores them if their `storedAt` timestamp is old.

### Finding Description
In `x/uexecutor/keeper/chain_meta.go`, the `VoteChainMeta` function manages the `ChainMeta` state. When a validator votes, their price and height are either updated in place or appended to the `Prices`, `ChainHeights`, and `StoredAts` slices [1](#0-0) .

While the median calculation correctly filters for "fresh" votes based on `chainMetaVoteStalenessSeconds` [2](#0-1) , the underlying slices in the `ChainMeta` object never shrink. If the oracle reports a decrease in gas prices, but old, higher votes from inactive or lagging validators are still within the staleness window, they continue to pull the median upward. More critically, the `MedianIndex` is computed against the *full* slice of prices [3](#0-2) , even though the actual value sent to the EVM is derived only from the `fresh` subset [4](#0-3) . This creates a permanent divergence between the `MedianIndex` stored on Push Chain and the actual value applied to the Universal Core contract on the EVM [5](#0-4) .

### Impact Explanation
The primary impact is the corruption of gas fee accounting and potential permanent divergence of the on-chain oracle state.
1. **Financial Loss:** If the median gas price is artificially inflated by stale votes, users are overcharged for cross-chain transactions (inbounds/outbounds).
2. **State Machine Divergence:** The `MedianIndex` stored in the Push Chain state will point to an incorrect value relative to what was actually committed to the external EVM chain, breaking the invariant that the Push Chain state is a canonical record of the oracle's output.
3. **Consensus Risk:** If the staleness window is long, a subset of validators could maintain "zombie" votes that prevent the oracle from reflecting rapid price decreases on the source chain.

### Likelihood Explanation
The likelihood is high because gas prices on chains like Ethereum and Solana are highly volatile. Validators may frequently experience temporary downtime or RPC lag. Since the code explicitly allows updates but provides no mechanism to prune or expire old validator entries from the `ChainMeta` slices, the accumulation of stale data is guaranteed over time.

### Recommendation
1. **Prune Stale Votes:** Modify `VoteChainMeta` to periodically remove entries from `Signers`, `Prices`, `ChainHeights`, and `StoredAts` where the `age` exceeds the staleness threshold.
2. **Align MedianIndex:** Ensure `MedianIndex` is calculated based on the same `fresh` pool used for the EVM call, or remove it if it no longer represents the applied value.
3. **Strict Monotonicity for Heights:** The existing check `blockNumber <= entry.LastAppliedChainHeight` [6](#0-5)  is good but only applies to individual validators. Consider enforcing that the *median* height must also be strictly increasing.

### Proof of Concept
1. Three validators (A, B, C) vote for a gas price of 1000 Gwei at height 100. The median is 1000.
2. The gas price on the external chain drops to 10 Gwei.
3. Validator A and B update their votes to 10 Gwei at height 110.
4. Validator C goes offline.
5. In the current implementation, the `fresh` pool contains [10, 10, 1000]. The median is 10.
6. However, the `entry.Prices` slice still contains [10, 10, 1000].
7. If Validator D joins and votes 500 Gwei, the `fresh` pool becomes [10, 10, 500, 1000]. The upper median (index 2) is 500 Gwei.
8. The oracle now applies 500 Gwei to the EVM, even though the majority of *active* validators (A and B) see 10 Gwei. The stale vote from C (1000) and the new vote from D (500) have disproportionately influenced the result because the slice only grows [7](#0-6) .

### Citations

**File:** x/uexecutor/keeper/chain_meta.go (L74-85)
```go
	if bootstrapped && blockNumber <= entry.LastAppliedChainHeight {
		k.Logger().Warn("chain meta vote rejected: stale block height",
			"chain_id", observedChainId,
			"validator", universalValidator.String(),
			"vote_height", blockNumber,
			"last_applied_height", entry.LastAppliedChainHeight,
		)
		return fmt.Errorf(
			"vote chain height %d is not greater than last applied chain height %d; re-vote with a newer block",
			blockNumber, entry.LastAppliedChainHeight,
		)
	}
```

**File:** x/uexecutor/keeper/chain_meta.go (L92-108)
```go
	// Update or insert vote for this validator.
	var updated bool
	for i, s := range entry.Signers {
		if s == universalValidator.String() {
			entry.Prices[i] = price
			entry.ChainHeights[i] = blockNumber
			entry.StoredAts[i] = now
			updated = true
			break
		}
	}
	if !updated {
		entry.Signers = append(entry.Signers, universalValidator.String())
		entry.Prices = append(entry.Prices, price)
		entry.ChainHeights = append(entry.ChainHeights, blockNumber)
		entry.StoredAts = append(entry.StoredAts, now)
	}
```

**File:** x/uexecutor/keeper/chain_meta.go (L110-127)
```go
	// Build a filtered pool: only votes stored within the staleness window.
	type voteSnapshot struct {
		price       uint64
		chainHeight uint64
	}
	var fresh []voteSnapshot
	for i := range entry.Signers {
		if entry.StoredAts[i] > now {
			continue // clock skew guard — skip future-stamped votes
		}
		age := now - entry.StoredAts[i]
		if age <= chainMetaVoteStalenessSeconds {
			fresh = append(fresh, voteSnapshot{
				price:       entry.Prices[i],
				chainHeight: entry.ChainHeights[i],
			})
		}
	}
```

**File:** x/uexecutor/keeper/chain_meta.go (L157-158)
```go
	medianPrice := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.price })
	medianChainHeight := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.chainHeight })
```

**File:** x/uexecutor/keeper/chain_meta.go (L169-169)
```go
	entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))
```

**File:** x/uexecutor/keeper/chain_meta.go (L173-177)
```go
	if _, evmErr := k.CallUniversalCoreSetChainMeta(sdkCtx, observedChainId, priceBig, chainHeightBig); evmErr != nil {
		return sdkerrors.Wrap(evmErr, "failed to call EVM setChainMeta")
	}

	entry.LastAppliedChainHeight = medianChainHeight
```
