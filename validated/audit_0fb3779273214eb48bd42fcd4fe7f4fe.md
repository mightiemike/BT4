## Finding: `ChainMeta.MedianIndex` is computed against a stale (unfiltered) vote set while `LastAppliedChainHeight`/the on-chain oracle price is computed from a different (freshness-filtered) set [1](#0-0) 

### Title
Stale-basis median index in `ChainMeta` diverges from the actually-applied EVM gas-price median - (File: `x/uexecutor/keeper/chain_meta.go`)

### Summary
The reported `_dP2PDToken.athBalance()` bug is a "derived value computed against a stale reference dataset while the authoritative value moved on" class of bug. `x/uexecutor`'s chain-meta oracle has a structural analog: `entry.MedianIndex` is derived from the **full, unfiltered** `entry.Prices` array, while the value actually pushed to the EVM oracle (`medianPrice`) and used to advance `LastAppliedChainHeight` is derived from a **staleness-filtered** subset (`fresh`) of the same votes.

### Finding Description
In `VoteChainMeta` [2](#0-1) :

- `fresh` is built by dropping any vote whose `StoredAts[i]` is older than `chainMetaVoteStalenessSeconds` (300s), and the actually-applied `medianPrice`/`medianChainHeight` are computed only from this filtered `fresh` slice via `upperMedianUint64`.
- Separately, `entry.MedianIndex = uint64(computeMedianIndex(entry.Prices))` is computed over the **entire, unfiltered** `entry.Prices` slice (including stale entries), and stored alongside `entry.Prices` for querying.

These two computations use different populations whenever any signer's vote has aged past the staleness window but is still present in `Signers`/`Prices` (which are never pruned except by `PruneValidatorVotes` on UV removal). As a result, `entry.MedianIndex` can point to an index in `entry.Prices` whose value has nothing to do with the price actually pushed to the EVM `UniversalCore` oracle in that same call.

This is confirmed as a known, unaddressed quirk in the module's own test suite: `test/integration/uexecutor/vote_chain_meta_test.go` explicitly notes "MedianIndex on the stored entry reflects the full-slice median, so we must query the contract directly for the actually-applied value" [3](#0-2) .

The `Querier.GasPrice`/`AllGasPrices` gRPC endpoints (backed by `ChainMetas`, per `x/uexecutor/README.md`'s note that `ChainMetas` is now the live source) return this same `MedianIndex`+`Prices` pair to external callers [4](#0-3) . The `universalClient` node's `pushcore.Client.GetGasPrice` directly indexes into `resp.GasPrice.Prices[resp.GasPrice.MedianIndex]` (with only an out-of-bounds fallback to index 0, not a staleness check) to obtain "the median gas price for a specific chain from the on-chain oracle" [5](#0-4) .

### Impact Explanation
Any client (including `universalClient` worker code) that trusts the `GasPrice`/`AllGasPrices` query response instead of reading the live EVM `gasPriceByChainNamespace` value can be handed a price value that never was, and never will be, the value actually applied to the chain-meta oracle used by `x/uexecutor` for outbound gas/refund accounting (`GetGasPriceByChain`, `GetL1GasFeeByChain` in `x/uexecutor/keeper/evm.go`). This is the same failure mode as the Booster bug: a derived numeric artifact (`MedianIndex`) silently decouples from the actual authoritative state (`medianPrice` pushed on-chain) as votes age, without any explicit recomputation trigger, and there is no code path that "fixes" `MedianIndex` to match the fresh-only computation until the next vote happens to realign them.

### Likelihood Explanation
This triggers under entirely honest, ordinary operation — no attacker input is required. It happens naturally whenever a validator's most recent chain-meta vote ages past the 300-second staleness window (`chainMetaVoteStalenessSeconds`) while other validators keep voting, which is routine given `puniversald`'s periodic gas-price polling. The divergence window grows with the number of stale signers left in `Signers`/`Prices` (they are only pruned on UV removal via `PruneValidatorVotes`, not on staleness).

### Recommendation
Compute `MedianIndex` from the same `fresh` (staleness-filtered) population used for `medianPrice`/`medianChainHeight`, or drop the `MedianIndex`+`Prices` externally-queryable representation entirely in favor of only exposing `LastAppliedChainHeight` plus a directly-stored `LastAppliedPrice` field that mirrors exactly what was pushed to the EVM oracle in `CallUniversalCoreSetChainMeta`. This removes the need for any consumer to reconstruct "the currently applied price" via index arithmetic over a mixed fresh/stale array.

### Proof of Concept
1. 3 UVs vote chain-meta prices for chain `eip155:1` at time T: `[100, 300, 500]` → fresh median index into the *full* slice is `1` (value `300`); `MedianIndex=1`, applied EVM price `=300`.
2. Time passes >300s. Validator 1 (who voted `300`) does not re-vote; validators 0 and 2 re-vote `[100, 500]`.
3. `fresh = [100, 500]`, upper median (`len/2=1`) → applied `medianPrice = 500`, pushed to EVM and `LastAppliedChainHeight` updated accordingly.
4. But `entry.Prices` is still `[100, 300, 500]` (validator 1's stale vote is not pruned), so `computeMedianIndex(entry.Prices)` returns index `1`, i.e. `entry.MedianIndex = 1` → `entry.Prices[1] = 300`.
5. Any client calling `Querier.GasPrice`/`AllGasPrices` (e.g. `pushcore.Client.GetGasPrice`) reads `Prices[MedianIndex] = 300`, while the real, live, on-chain-applied oracle price is `500` — a mismatch of 40% reachable purely through normal, honest validator voting timing, matching the test's own confirmation that direct EVM query and `MedianIndex`-derived query diverge.

### Citations

**File:** x/uexecutor/keeper/chain_meta.go (L110-177)
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

**File:** test/integration/uexecutor/vote_chain_meta_test.go (L252-263)
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
	})
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
