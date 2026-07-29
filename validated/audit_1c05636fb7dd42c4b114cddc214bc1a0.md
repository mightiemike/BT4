## Finding

### Title
Stale-vote-inclusive `MedianIndex` desynchronizes queried gas price from the actually-applied oracle value - (File: `x/uexecutor/keeper/chain_meta.go`)

### Summary
`x/uexecutor`'s `VoteChainMeta` computes two different "median" values from two different data sets on every vote: the value it actually pushes to the EVM gas-price oracle (`medianPrice`, computed only from staleness-filtered `fresh` votes) and the value exposed to on-chain/off-chain readers via `entry.MedianIndex` (computed over the *full*, stale-inclusive `entry.Prices` slice). These two values silently diverge whenever any stored vote has gone stale, exactly mirroring the Liquity `SortedTroves` bug class: two representations of "the same" quantity are derived from different underlying state depending on per-validator history (fresh vs. stale), breaking an invariant (`Prices[MedianIndex]` == actually-applied price) that downstream consumers rely on.

### Finding Description
In `VoteChainMeta` (`x/uexecutor/keeper/chain_meta.go:110-189`):

- `fresh` is built by filtering `entry.Signers/Prices/ChainHeights/StoredAts` down to only entries with `age <= chainMetaVoteStalenessSeconds` [1](#0-0) .
- `medianPrice`/`medianChainHeight` are computed from that filtered `fresh` set and are what's actually written to the EVM oracle via `CallUniversalCoreSetChainMeta` [2](#0-1) .
- But `entry.MedianIndex`, which is persisted and exposed through both the `ChainMeta`/legacy `GasPrice` query responses, is computed with `computeMedianIndex(entry.Prices)` — over the **entire, unfiltered** `Prices` slice, including stale entries [3](#0-2) , [4](#0-3) .

The project's own integration test acknowledges this divergence explicitly: after a stale-vote scenario, it states "`MedianIndex` on the stored entry reflects the full-slice median, so we must query the contract directly for the actually-applied value," then queries the EVM contract instead of the stored struct to get the real value [5](#0-4) .

This queried, potentially-stale `GasPrice.Prices[MedianIndex]` value is exactly what `puniversald` (the off-chain Universal Client) consumes as "the median gas price for a specific chain from the on-chain oracle" via `GetGasPrice` [6](#0-5) . That client-side gas price feeds gas-cost/fund-migration-sweep math (`computeFundMigrationTransfer` = `balance - gasPrice*gasLimit - l1Fee`) whose correctness across all validators is explicitly required for a matching TSS signing hash, as the accompanying test comments state ("All validators must compute the same value — any drift breaks the TSS hash") [7](#0-6) .

### Impact Explanation
This is a state-consistency bug reachable purely through the honest, ordinary operation of Universal Validators voting `MsgVoteChainMeta` over time (votes naturally go stale as new ones supersede old ones) — no malicious actor is required, matching the "honest validators and honest nodes" scope. The on-chain queryable `GasPrice`/`ChainMeta` record can present a median price/index that does not correspond to the value actually pushed to the gas-price oracle used by protocol logic. Any off-chain or on-chain consumer that trusts `Prices[MedianIndex]` as ground truth (rather than re-deriving it) computes with a wrong external-chain gas price, corrupting gas-fee/refund accounting for gas top-ups and, per the test's own hash-sensitivity note, potentially causing UV gas-price disagreement that breaks TSS fund-migration signing consensus.

### Likelihood Explanation
Low-to-medium. It requires the natural passage of time (>300s staleness window, `chainMetaVoteStalenessSeconds`) between validator votes on a given chain — a routine occurrence, not an attack. However, exploiting it for concrete fund loss additionally requires some consumer to actually read and trust the stored `MedianIndex` field rather than the live EVM oracle value; the repository's own code (`CallUniversalCoreSetChainMeta`/`gasPriceByChainNamespace`) is the authoritative path, and the off-chain `puniversald.GetGasPrice` reads the on-chain gRPC query, so severity depends on how widely that queried value is consumed downstream (fund migration gas math is one confirmed consumer).

### Recommendation
Make `MedianIndex` (and the exposed `Prices`/`Signers`/`ChainHeights` arrays) always reflect the same staleness-filtered set used to compute `medianPrice`/`medianChainHeight`, e.g. by storing/returning the filtered `fresh` slice (or an index into it) instead of indexing into the full, stale-inclusive `entry.Prices`. Alternatively, drop `MedianIndex` entirely from the public API and always compute/query the value straight from the EVM oracle (`gasPriceByChainNamespace`), which is already the single source of truth used internally.

### Proof of Concept
1. 3 UVs vote `MsgVoteChainMeta` with prices `[100, 300, 500]` at time `T`. `entry.MedianIndex` and the applied oracle value both correctly point to `300`.
2. Advance time past `chainMetaVoteStalenessSeconds` (300s) so all 3 votes become stale.
3. Only validators 0 and 2 re-vote with `[100, 500]`. The oracle is updated with the new fresh median (`500`, upper median of `[100,500]`), but `entry.Prices` still contains the old middle value `300` from the now-stale vote of validator 1, and `entry.MedianIndex = computeMedianIndex(entry.Prices)` is recomputed over the full 3-element (partially stale) array — the value exposed via `QueryGasPriceRequest`/`Prices[MedianIndex]` no longer matches the actually-applied oracle price of `500`, exactly as the repository's own `TestVoteChainMetaIntegration/"stale votes excluded from median"` test demonstrates by having to bypass the stored struct and query the EVM contract directly for the true value [5](#0-4) .

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

**File:** x/uexecutor/keeper/gas_price.go (L52-63)
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
```

**File:** test/integration/uexecutor/vote_chain_meta_test.go (L221-262)
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
```

**File:** universalClient/pushcore/pushCore.go (L214-248)
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
```

**File:** universalClient/chains/evm/tx_builder_test.go (L1060-1062)
```go
// TestComputeFundMigrationTransfer covers the sweep-amount formula
// balance - (gasPrice * gasLimit) - l1GasFee for both L1 and L2-style chains.
// All validators must compute the same value — any drift breaks the TSS hash.
```
