## Title
Stale-vote median divergence in `x/uexecutor` chain-meta oracle — stored `MedianIndex` does not correspond to the price actually applied on-chain (File: `x/uexecutor/keeper/chain_meta.go`)

### Summary
The external report's root cause is that two functions computed the tree route/index for the *same conceptual quantity* differently (`treeTick(highTick)-1` vs `treeTick(highTick)`), so the value written to node state and the value actually applied to the pool diverged. The same pattern exists in `VoteChainMeta`: the persisted `ChainMeta.MedianIndex` field and the price value actually pushed to the on-chain gas oracle are computed from two different underlying data sets, causing the queryable index to reference a value that does not match what was actually applied.

### Finding Description
In `x/uexecutor/keeper/chain_meta.go`, `VoteChainMeta` maintains a `ChainMeta` entry with parallel arrays `Signers`, `Prices`, `ChainHeights`, `StoredAts`. On each vote:

1. It builds a filtered `fresh` slice containing only votes whose `StoredAt` is within `chainMetaVoteStalenessSeconds` [1](#0-0) .
2. The actual price/height that gets written to the EVM oracle (`CallUniversalCoreSetChainMeta`) and stored as `LastAppliedChainHeight` is `upperMedianUint64(fresh, ...)` — the median over the **fresh-only** subset [2](#0-1) .
3. But the `entry.MedianIndex` field, which is persisted and returned by the `GasPrice`/`ChainMeta` queries as "the index of the median price in `Prices`", is computed separately over the **full, unfiltered** `entry.Prices` array (including stale signer entries that were excluded from the "fresh" computation): `entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))` [3](#0-2) .

`computeMedianIndex` independently sorts and returns the median position of the whole `values` slice [4](#0-3) , which is a materially different computation than `upperMedianUint64(fresh, ...)`. Whenever any signer's stored vote is stale (a routine, expected condition under normal validator liveness gaps — not an attack), `Prices[MedianIndex]` can differ from the `medianPrice` value that was actually committed to the EVM chain-meta oracle.

This stored index is consumed off-chain: `universalClient/pushcore/pushCore.go`'s `GetGasPrice` reads `resp.GasPrice.Prices[medianIdx]` directly as "the" gas price for a chain [5](#0-4) , with only an out-of-bounds fallback to index 0, not a staleness- or consistency-aware fallback.

### Impact Explanation
This is a mismatch between the "node information" (persisted `ChainMeta.MedianIndex`/`Prices`) and the "actual applied state" (the value written to the on-chain UniversalCore gas oracle), directly analogous to the H-3 pattern of two divergent index computations for what should be one canonical value. Concretely:
- Query consumers of `GasPrice`/`ChainMeta` (including `puniversald`'s own `GetGasPrice` client) can read a stale/incorrect price that does not match what the EVM-side oracle actually has, because the two code paths (`upperMedianUint64` over `fresh` vs `computeMedianIndex` over the full `Prices`) are not kept in sync.
- I could not confirm, within the remaining exploration budget, that this specific value feeds directly into a fund-moving computation (gas fee deduction or refund amount) — `GetOutboundTxGasAndFees`/`applyGasRefund` in `x/uexecutor/keeper/gas_fee.go` and `outbound.go` call `UniversalCore.getOutboundTxGasAndFees` directly on the EVM side rather than reading `ChainMeta.MedianIndex` from the Cosmos module [6](#0-5) . That EVM-side call presumably reads the value actually written via `setChainMeta` (the `fresh`-median value), not the divergent `MedianIndex` field. Because of this, I cannot confirm a direct path from this divergence to loss/mint/burn/freeze of user or protocol funds under the "Allowed Impact Gate," so this should be treated as a state-consistency/reporting bug rather than a confirmed funds-impact vulnerability.

### Likelihood Explanation
High likelihood of the divergence occurring during ordinary, honest operation (no attacker needed): any time a validator's previously-stored vote ages out of the staleness window while other validators keep voting, `fresh` becomes a strict subset of `Signers`/`Prices`, and `computeMedianIndex(entry.Prices)` no longer tracks `upperMedianUint64(fresh, ...)`. This is demonstrated implicitly by the project's own median-recompute test showing the median value changing after staleness filtering [7](#0-6) , though that test does not assert `MedianIndex` correctness against the applied median.

### Recommendation
Compute `MedianIndex` from the same `fresh` set used to derive the applied `medianPrice`/`medianChainHeight` (or store the applied median price directly rather than an index into the raw `Prices` array), so that `Prices[MedianIndex]` is guaranteed to equal the value actually written to the on-chain oracle via `CallUniversalCoreSetChainMeta`. Apply the same fix to `PruneValidatorVotes` (`x/uexecutor/keeper/gas_price.go`), which also recomputes `MedianIndex` via `computeMedianIndex` over the full (non-staleness-filtered) `Prices` array after removing a validator.

### Proof of Concept
1. Three UVs vote chain-meta for chain `X` at heights 1, 2, 3 with prices 100, 300, 500 (bootstraps oracle; `Prices=[100,300,500]`, `MedianIndex` computed over all three → index 1 → 300, matching applied median 300).
2. Advance block time past `chainMetaVoteStalenessSeconds` so all three votes go stale.
3. Only validators 0 and 2 re-vote with prices 100 and 500 at heights 4/5 (validator 1's stale price 300 stays in `Prices` un-removed).
4. `fresh = [100, 500]`; `upperMedianUint64` → applied median = 500 (index 1 of `fresh`), which is written on-chain and used for `LastAppliedChainHeight`.
5. `entry.MedianIndex = computeMedianIndex(entry.Prices)` where `Prices = [100, 300, 500]` (validator 1's stale 300 is still present) → sorted `[100,300,500]`, median index → position of 300, i.e. index 1 of the *original* array → `Prices[1] = 300`.
6. A client calling `GetGasPrice` reads `Prices[MedianIndex] = 300`, while the value actually pushed to the on-chain gas oracle for chain `X` is `500` — a confirmed divergence between the reported chain-meta price and the actually-applied on-chain price, reachable purely through honest validator vote timing with no attacker action required.

### Citations

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

**File:** x/uexecutor/keeper/chain_meta.go (L156-177)
```go
	// Compute independent upper medians (len/2) for price and chain height.
	medianPrice := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.price })
	medianChainHeight := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.chainHeight })

	k.Logger().Debug("chain meta medians computed",
		"chain_id", observedChainId,
		"fresh_votes", len(fresh),
		"median_price", medianPrice,
		"median_chain_height", medianChainHeight,
	)

	// Update MedianIndex to reflect the price median position in the full slice
	// (best-effort; used for storage/querying only).
	entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))

	priceBig := math.NewUint(medianPrice).BigInt()
	chainHeightBig := math.NewUint(medianChainHeight).BigInt()
	if _, evmErr := k.CallUniversalCoreSetChainMeta(sdkCtx, observedChainId, priceBig, chainHeightBig); evmErr != nil {
		return sdkerrors.Wrap(evmErr, "failed to call EVM setChainMeta")
	}

	entry.LastAppliedChainHeight = medianChainHeight
```

**File:** x/uexecutor/keeper/gas_price.go (L52-64)
```go
// computeMedianIndex returns index of the median element
func computeMedianIndex(values []uint64) int {
	type idxVal struct {
		Idx int
		Val uint64
	}
	arr := make([]idxVal, len(values))
	for i, v := range values {
		arr[i] = idxVal{Idx: i, Val: v}
	}
	sort.SliceStable(arr, func(i, j int) bool { return arr[i].Val < arr[j].Val })
	return arr[len(arr)/2].Idx
}
```

**File:** universalClient/pushcore/pushCore.go (L238-244)
```go
			medianIdx := resp.GasPrice.MedianIndex
			if medianIdx >= uint64(len(resp.GasPrice.Prices)) {
				medianIdx = 0
			}

			medianPrice := resp.GasPrice.Prices[medianIdx]
			return new(big.Int).SetUint64(medianPrice), nil
```

**File:** x/uexecutor/keeper/gas_fee.go (L26-46)
```go
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

```

**File:** test/integration/uexecutor/validator_pruning_test.go (L157-193)
```go
	t.Run("median recomputes correctly when a validator with middle value is stale", func(t *testing.T) {
		testApp, ctx, uvals, vals := setupValidatorPruningTest(t, 3)

		coreAccs := make([]string, len(vals))
		for i := range vals {
			coreVal, _ := sdk.ValAddressFromBech32(vals[i].OperatorAddress)
			coreAccs[i] = sdk.AccAddress(coreVal).String()
		}

		// 3 validators vote different prices:
		// val[0]=100, val[1]=300 (middle), val[2]=500
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[0], coreAccs[0], chainId, 100, 1))
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[1], coreAccs[1], chainId, 300, 2))
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[2], coreAccs[2], chainId, 500, 3))

		// Initial median: sorted [100, 300, 500] -> upper median index 1 -> 300
		stored, _, _ := testApp.UexecutorKeeper.GetChainMeta(ctx, chainId)
		initialMedianPrice := stored.Prices[stored.MedianIndex]
		require.Equal(t, uint64(300), initialMedianPrice, "initial median should be 300")

		// Advance time so all current votes become stale
		ctx = ctx.WithBlockTime(ctx.BlockTime().Add(301 * time.Second))

		// Only val[0] and val[2] re-vote (skipping the middle validator val[1])
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[0], coreAccs[0], chainId, 100, 4))
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[2], coreAccs[2], chainId, 500, 5))

		// Now only 2 fresh votes: [100, 500]
		// Upper median at index len/2 = 1 -> 500
		stored, found, err := testApp.UexecutorKeeper.GetChainMeta(ctx, chainId)
		require.NoError(t, err)
		require.True(t, found)

		// Verify the median was recomputed without the stale middle vote
		require.Equal(t, uint64(5), stored.LastAppliedChainHeight,
			"LastAppliedChainHeight should update to fresh median")
	})
```
