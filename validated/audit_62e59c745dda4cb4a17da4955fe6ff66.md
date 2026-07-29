### Title
`ChainMeta.MedianIndex` diverges from the actually-applied gas price, causing `Query/GasPrice` (and `universalClient.GetGasPrice`) to return a stale/wrong price - (File: `x/uexecutor/keeper/chain_meta.go`)

### Summary
This is the closest Push Chain analog to the Yeti `lastBuyBackPrice` bug: a persisted "price that was used" field is computed from the wrong data set, so it silently diverges from the value actually applied on-chain, and downstream consumers read the wrong number.

### Finding Description
`VoteChainMeta` maintains four parallel arrays per observed chain — `Signers`, `Prices`, `ChainHeights`, `StoredAts` [1](#0-0) . On every vote it filters out stale entries (older than `chainMetaVoteStalenessSeconds`) into a `fresh` slice, and computes the price/height that is actually written to the EVM oracle from that **filtered** set via `upperMedianUint64` [2](#0-1) .

However, the persisted `entry.MedianIndex` field — the value exposed through the `GasPrice`/`ChainMeta` queries — is computed against the **full, unfiltered** `entry.Prices` slice (including stale votes) via `computeMedianIndex(entry.Prices)`: [3](#0-2) 

These two computations use different input sets (`fresh` vs. full `Prices`), so `Prices[MedianIndex]` can point to an entirely different, stale value than the price actually pushed to `UniversalCore.setChainMeta`. The integration test suite explicitly documents this divergence and works around it by querying the EVM contract directly instead of trusting `MedianIndex`: [4](#0-3) 

The `GasPrice` query (legacy compatibility path) surfaces this same `MedianIndex`/`Prices` pair unchanged [5](#0-4) , and `universalClient`'s `pushcore.Client.GetGasPrice` consumes it verbatim, indexing into `Prices` with the untrusted `MedianIndex`: [6](#0-5) 

### Impact Explanation
Any off-chain component relying on `Query/GasPrice` (rather than reading `gasPriceByChainNamespace` from the EVM contract directly, as `x/uexecutor/keeper/gas_fee.go`'s `GetOutboundTxGasAndFees` correctly does) can be handed a stale gas price that does not match the price actually enforced on-chain. This falls under "corruption of ... gas fee accounting ... chain config use" in the allowed-impact list. It does not by itself constitute a direct fund-theft primitive in the paths I traced (outbound gas accounting reads `getOutboundTxGasAndFees` from the EVM contract directly, not this query), so the confirmed impact is a data-integrity/query-correctness defect in the gas-price oracle's exposed state rather than a demonstrated fund-loss path.

### Likelihood Explanation
This triggers under completely normal, honest-validator operation — no malicious actor is required. Any time some validators' votes go stale while others remain fresh (the exact scenario in the `stale votes excluded from median` test), the stored `MedianIndex` will point at a value from the full (including stale) array while the EVM oracle holds the freshly-computed median. This is a deterministic, always-reachable inconsistency, not a rare race.

### Recommendation
Compute `MedianIndex` (and expose it) against the same `fresh` (non-stale) snapshot that is actually used to compute `medianPrice`/`medianChainHeight`, or better, deprecate `MedianIndex` from the query surface entirely and have `Query/GasPrice` return the already-resolved `medianPrice`/`LastAppliedChainHeight` values directly instead of a raw array + index pair that callers must re-index themselves.

### Proof of Concept
1. Three UVs bootstrap `ChainMeta` for a chain with prices `[100, 300, 200]` at close-together timestamps → fresh median = 200, applied to EVM oracle; `entry.Prices = [100,300,200]`, `MedianIndex` computed over the full array also happens to reflect 200 in this case.
2. Advance block time past `chainMetaVoteStalenessSeconds` (300s) so `[100,300]` become stale.
3. Only one validator re-votes with a new price (e.g. 900) at a fresh height greater than `LastAppliedChainHeight`.
4. `fresh = [900]` → EVM oracle is updated to 900 (confirmed by the existing test reading `gasPriceByChainNamespace`).
5. `entry.MedianIndex = computeMedianIndex(entry.Prices)` is recomputed over the **full** `Prices` array `[100,300,900]` → index 0 (value 100), NOT 900.
6. A client calling `Query/GasPrice` (or `universalClient.pushcore.Client.GetGasPrice`) reads `Prices[MedianIndex] = 100`, which is stale and inconsistent with the 900 actually enforced on-chain.

### Citations

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

**File:** x/uexecutor/keeper/chain_meta.go (L110-158)
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

	// Cold-start gate: the first EVM write requires at least N fresh votes
	// so the oracle is never defined by a single validator. Once bootstrapped,
	// the existing fresh-votes-median path handles every subsequent vote.
	if !bootstrapped && len(fresh) < chainMetaMinVotesForFirstWrite {
		k.Logger().Info("chain meta vote recorded, awaiting bootstrap quorum",
			"chain_id", observedChainId,
			"validator", universalValidator.String(),
			"have_fresh_votes", len(fresh),
			"need_fresh_votes", chainMetaMinVotesForFirstWrite,
		)
		if err := k.SetChainMeta(ctx, observedChainId, entry); err != nil {
			return sdkerrors.Wrap(err, "failed to set chain meta entry during bootstrap")
		}
		return nil
	}

	if len(fresh) == 0 {
		k.Logger().Debug("chain meta vote recorded, no fresh votes for EVM update",
			"chain_id", observedChainId,
			"validator", universalValidator.String(),
		)
		if err := k.SetChainMeta(ctx, observedChainId, entry); err != nil {
			return sdkerrors.Wrap(err, "failed to set updated chain meta entry")
		}
		return nil
	}

	// Compute independent upper medians (len/2) for price and chain height.
	medianPrice := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.price })
	medianChainHeight := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.chainHeight })
```

**File:** x/uexecutor/keeper/chain_meta.go (L167-169)
```go
	// Update MedianIndex to reflect the price median position in the full slice
	// (best-effort; used for storage/querying only).
	entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))
```

**File:** test/integration/uexecutor/vote_chain_meta_test.go (L252-262)
```go
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

**File:** test/integration/uexecutor/query_chain_meta_test.go (L92-116)
```go
// TestQueryGasPriceFromChainMeta ensures the legacy GasPrice query routes through ChainMetas
func TestQueryGasPriceFromChainMeta(t *testing.T) {
	chainId := "eip155:11155111"

	t.Run("gas price query reads from chain metas", func(t *testing.T) {
		testApp, ctx, _, _ := utils.SetAppWithMultipleValidators(t, 1)

		require.NoError(t, testApp.UexecutorKeeper.SetChainMeta(ctx, chainId, uexecutortypes.ChainMeta{
			ObservedChainId: chainId,
			Signers:         []string{"cosmos1abc", "cosmos1def"},
			Prices:          []uint64{100_000_000_000, 200_000_000_000},
			ChainHeights:    []uint64{12345, 12346},
			MedianIndex:     1,
		}))

		querier := uexecutorkeeper.NewQuerier(testApp.UexecutorKeeper)
		resp, err := querier.GasPrice(ctx, &uexecutortypes.QueryGasPriceRequest{ChainId: chainId})
		require.NoError(t, err)
		require.NotNil(t, resp.GasPrice)
		require.Equal(t, chainId, resp.GasPrice.ObservedChainId)
		require.Equal(t, []uint64{100_000_000_000, 200_000_000_000}, resp.GasPrice.Prices)
		// ChainHeights should be mapped back to BlockNums for backward compat
		require.Equal(t, []uint64{12345, 12346}, resp.GasPrice.BlockNums)
		require.Equal(t, uint64(1), resp.GasPrice.MedianIndex)
	})
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
