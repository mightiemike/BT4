### Title
Stale-vote `MedianIndex` desync between queried `GasPrice`/`ChainMeta` and the actually-applied on-chain median price - (File: x/uexecutor/keeper/chain_meta.go)

### Summary
`VoteChainMeta` computes the value that is actually written to the `UniversalCore` on-chain oracle (`medianPrice`) from a **staleness-filtered** subset of votes ("fresh"), but it separately recomputes `entry.MedianIndex` from the **entire, unfiltered** `entry.Prices` slice. Any consumer that reads the `GasPrice`/`ChainMeta` query and indexes into `Prices[MedianIndex]` (as `universalClient/pushcore/pushCore.go`'s `GetGasPrice` does) can therefore read a stale/incorrect price that differs from the price actually applied on-chain via `CallUniversalCoreSetChainMeta`.

### Finding Description
In `x/uexecutor/keeper/chain_meta.go`, `VoteChainMeta` (lines 62-189) maintains parallel arrays `Signers/Prices/ChainHeights/StoredAts` for every validator that has ever voted on a given `observedChainId`. On each vote it builds a `fresh` list containing only votes whose `StoredAt` is within `chainMetaVoteStalenessSeconds` (300s) of the current block time (lines 110-127), and computes the value that is actually pushed to the EVM oracle from that filtered list:

```go
medianPrice := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.price })
...
entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))   // uses the FULL, unfiltered slice
...
k.CallUniversalCoreSetChainMeta(sdkCtx, observedChainId, priceBig, chainHeightBig)
``` [1](#0-0) 

`computeMedianIndex` (in `x/uexecutor/keeper/gas_price.go`) operates over the entire `entry.Prices` array, including stale entries that were explicitly excluded from `fresh`: [2](#0-1) 

The stored `MedianIndex` is exposed via the `GasPrice`/`AllGasPrices`/`ChainMeta` gRPC queries through `chainMetaToGasPrice`, which copies `MedianIndex` verbatim: [3](#0-2) 

`universalClient/pushcore/pushCore.go`'s `GetGasPrice` consumes this response by indexing directly into `Prices[MedianIndex]`, trusting that this index reflects the value actually enacted on-chain: [4](#0-3) 

This is structurally the same class of bug as the reported issue: a value that is computed correctly over one specific (filtered/relevant) subset — here, the on-chain applied median price computed from `fresh` votes — is reported/exposed to downstream consumers via a different, aggregate/unfiltered index (`MedianIndex` over the *full* `Prices` array), causing the consumer-facing value to diverge from the value actually acted upon by the protocol. The test suite explicitly documents this divergence: [5](#0-4) 

### Impact Explanation
`puniversald` (the Universal Validator client) uses `GetGasPrice` to determine gas pricing for outbound transaction construction/signing on external chains. When stale validator votes remain in `entry.Prices` alongside fresh ones (a normal, non-privileged, honest-operation condition once votes go stale after `chainMetaVoteStalenessSeconds`), `MedianIndex` can point at a stale price that no longer matches the price actually pushed to the `UniversalCore` contract via `CallUniversalCoreSetChainMeta`. Any downstream calculation, budget check, or fee decision built on `pushCore.GetGasPrice()` rather than reading the authoritative on-chain value can be based on a wrong gas price, causing outbound gas-fee mis-estimation (over/under-funding, or failed transactions) relative to the actually-enacted on-chain gas price. This does not, on its own, meet the bar of directly draining or freezing user/protocol funds, forging ballots, or corrupting UTX/PRC20 accounting on-chain — it is a data-consistency defect in an off-chain-facing query surface, not a state-transition or fund-custody bug reachable purely by an unprivileged external attacker. The divergence itself is not attacker-triggered (it requires no malicious action, simply passage of time and honest validator vote churn), which puts it partly outside the specified in-scope impact categories (no unauthorized mint/burn/freeze, no forged ballot, no wrong UTX/PRC20 state).

### Likelihood Explanation
The divergence occurs naturally whenever votes become stale between EVM writes (a common, unprivileged, timing-dependent condition, not requiring an attacker), as already demonstrated in the repository's own test (`stale votes excluded from median`). However, this is a query/read-path inconsistency confined to `MedianIndex`/`Prices` bookkeeping and `puniversald`'s convenience helper — it is not shown to feed into any on-chain fund-moving, mint/burn, ballot-finalization, or authorization decision within the scoped `x/` modules or precompiles.

### Recommendation
Either (a) stop maintaining/exposing `MedianIndex` over the full `entry.Prices` slice and instead compute/store the index (or the value directly) from the same `fresh` filtered set used to derive the value pushed on-chain, or (b) have `GasPrice`/`ChainMeta` queries return the actually-applied value (e.g., derived from `LastAppliedChainHeight`/a stored "last applied price" field) rather than an index into the raw, potentially-stale `Prices` array. Downstream consumers such as `pushcore.GetGasPrice` should prefer reading the authoritative on-chain oracle value (`GetGasPriceByChain`/`gasPriceByChainNamespace`) over reconstructing it from `MedianIndex`.

### Proof of Concept
This mirrors the exact scenario already captured in `TestVoteChainMetaIntegration/"stale votes excluded from median"`: [6](#0-5) 
1. Three validators vote prices `[100, 300, 200]` at block time T; the upper median over the full (fresh) set is `200`, applied on-chain.
2. Time advances 301s past `chainMetaVoteStalenessSeconds`, making all prior votes stale.
3. Only one validator re-votes with price `900`; `fresh = [900]`, so `medianPrice = 900` is written on-chain via `CallUniversalCoreSetChainMeta`.
4. `entry.MedianIndex` is recomputed from the full `entry.Prices = [100, 300, 200_updated_to_900_for_that_validator...]` (the unfiltered array), which the test itself notes "reflects the full-slice median" and therefore does **not** equal `900` — the test has to bypass the query and call the EVM contract directly to observe the true applied value, confirming that `GasPrice.Prices[MedianIndex]` returned via the gRPC query would give an incorrect answer relative to the on-chain oracle.

**Note:** Because of index size limits, I could not fully trace every downstream consumer of `pushCore.GetGasPrice()` inside `universalClient/` to confirm whether any fund-custody or signing decision depends on it in a way that would elevate this into an in-scope fund-impact finding. A Devin session with full repository access would be needed to trace all callers of `GetGasPrice` across `universalClient/chains/*` and `universalClient/tss/*` to determine whether this data-consistency bug can propagate into an actual fund-impacting decision.

### Citations

**File:** x/uexecutor/keeper/chain_meta.go (L156-175)
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

**File:** x/uexecutor/keeper/query_server.go (L469-479)
```go
// chainMetaToGasPrice converts a ChainMeta into the legacy GasPrice shape
// so that existing API consumers see no breaking change.
func chainMetaToGasPrice(cm *types.ChainMeta) *types.GasPrice {
	return &types.GasPrice{
		ObservedChainId: cm.ObservedChainId,
		Signers:         cm.Signers,
		BlockNums:       cm.ChainHeights,
		Prices:          cm.Prices,
		MedianIndex:     cm.MedianIndex,
	}
}
```

**File:** universalClient/pushcore/pushCore.go (L214-249)
```go
// GetGasPrice retrieves the median gas price for a specific chain from the on-chain oracle.
func (c *Client) GetGasPrice(ctx context.Context, chainID string) (*big.Int, error) {
	if chainID == "" {
		return nil, errors.New("pushcore: chainID is required")
	}

	return retryWithRoundRobin(
		len(c.uexecutorClients),
		&c.rr,
		func(idx int) (*big.Int, error) {
			resp, err := c.uexecutorClients[idx].GasPrice(ctx, &uexecutortypes.QueryGasPriceRequest{
				ChainId: chainID,
			})
			if err != nil {
				return nil, err
			}
			if resp.GasPrice == nil {
				return nil, errors.New("pushcore: GasPrice response is nil")
			}

			if len(resp.GasPrice.Prices) == 0 {
				return nil, fmt.Errorf("pushcore: no gas prices available for chain %s", chainID)
			}

			medianIdx := resp.GasPrice.MedianIndex
			if medianIdx >= uint64(len(resp.GasPrice.Prices)) {
				medianIdx = 0
			}

			medianPrice := resp.GasPrice.Prices[medianIdx]
			return new(big.Int).SetUint64(medianPrice), nil
		},
		"GetGasPrice",
		c.logger,
	)
}
```

**File:** test/integration/uexecutor/vote_chain_meta_test.go (L221-263)
```go
	t.Run("stale votes excluded from median", func(t *testing.T) {
		testApp, ctx, uvals, vals := setupVoteChainMetaTest(t, 3)

		coreAccs := make([]string, 3)
		for i := range vals {
			coreVal, _ := sdk.ValAddressFromBech32(vals[i].OperatorAddress)
			coreAccs[i] = sdk.AccAddress(coreVal).String()
		}

		// All 3 validators vote at T. Heights 1, 2, 3 to pass lastApplied checks.
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[0], coreAccs[0], chainId, 100, 1))
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[1], coreAccs[1], chainId, 300, 2))
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[2], coreAccs[2], chainId, 200, 3))

		// After all 3 votes at T: sorted prices [100,200,300], upper median=200. lastApplied = median height = 2.
		stored, _, _ := testApp.UexecutorKeeper.GetChainMeta(ctx, chainId)
		require.Equal(t, uint64(2), stored.LastAppliedChainHeight)

		// Advance block time by 301 seconds — old votes become stale.
		ctx = ctx.WithBlockTime(ctx.BlockTime().Add(301 * time.Second))

		// val0 re-votes with price=900, height=3 (> lastApplied=2).
		// Only this fresh vote contributes to the new median.
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[0], coreAccs[0], chainId, 900, 3))

		stored, found, err := testApp.UexecutorKeeper.GetChainMeta(ctx, chainId)
		require.NoError(t, err)
		require.True(t, found)
		// LastAppliedChainHeight should now be 3 (only fresh vote: height=3)
		require.Equal(t, uint64(3), stored.LastAppliedChainHeight)

		// Verify the applied price via EVM contract — only val0's fresh vote (900) should have been used,
		// not the stale votes (100, 300). MedianIndex on the stored entry reflects the full-slice
		// median, so we must query the contract directly for the actually-applied value.
		universalCoreAddr := utils.GetDefaultAddresses().HandlerAddr
		ucABI, err := uexecutortypes.ParseUniversalCoreABI()
		require.NoError(t, err)
		caller, _ := testApp.UexecutorKeeper.GetUeModuleAddress(ctx)
		res, err := testApp.EVMKeeper.CallEVM(ctx, ucABI, caller, universalCoreAddr, false, nil, "gasPriceByChainNamespace", chainId)
		require.NoError(t, err)
		appliedPrice := new(big.Int).SetBytes(res.Ret)
		require.Equal(t, new(big.Int).SetUint64(900), appliedPrice, "stale votes must not influence the applied median price")
	})
```
