Found a genuine analog. This is the classic "two different rules computing what should be the same value" bug pattern from the Float128 report (encoding rule ≠ calculation rule), reproduced in Push Chain's chain-meta gas-price oracle.

### Title
Stale/mismatched `GasPrice.MedianIndex` returned by `GasPrice`/`AllGasPrices` queries diverges from the actual on-chain applied median price - (File: `x/uexecutor/keeper/chain_meta.go`)

### Summary
`VoteChainMeta` computes the price actually pushed to the EVM oracle (`CallUniversalCoreSetChainMeta`) using `upperMedianUint64` over only the **staleness-filtered** (`fresh`) vote set, but it computes `entry.MedianIndex` — the field exposed to external clients via the `GasPrice`/`AllGasPrices` gRPC queries (through `chainMetaToGasPrice`) — using `computeMedianIndex(entry.Prices)`, which runs over **all stored signer prices including stale ones**. These two computations use different input sets and different selection logic, exactly mirroring the Float128 report's core defect: one code path (the "authoritative" state mutation) and another code path (the "encoding"/exposed representation) apply divergent rules for what should be the same value.

### Finding Description
In `x/uexecutor/keeper/chain_meta.go`:
- `fresh` is built by filtering `entry.Signers`/`entry.Prices` to only those whose `StoredAts` is within `chainMetaVoteStalenessSeconds` (300s) of `now` [1](#0-0) .
- The actual gas price written on-chain via `CallUniversalCoreSetChainMeta` is `medianPrice := upperMedianUint64(fresh, ...)`, i.e., the median of only fresh votes [2](#0-1) .
- However, `entry.MedianIndex` — the only field that identifies "the median" in the stored/queried `GasPrice` shape — is computed as `computeMedianIndex(entry.Prices)`, over the *entire unfiltered* `entry.Prices` slice (including stale validator votes that were explicitly excluded from the real median calculation), with the comment "used for storage/querying only" acknowledging it does not drive consensus [3](#0-2) .
- `computeMedianIndex` in `x/uexecutor/keeper/gas_price.go` sorts the *entire* `Prices` array and returns the index of `len/2` — a different sample set than `upperMedianUint64` operates on [4](#0-3) .
- External consumers query this via `GasPrice`/`AllGasPrices`, which source from `ChainMetas` and convert through `chainMetaToGasPrice`, exposing `Prices` and the (mismatched) `MedianIndex` [5](#0-4) [6](#0-5) .
- `universalClient/pushcore/pushCore.go`'s `GetGasPrice` consumes exactly this response, indexing `resp.GasPrice.Prices[medianIdx]` and treating it as "the median gas price for a specific chain from the on-chain oracle" [7](#0-6) .

The result: `resp.GasPrice.Prices[medianIdx]` returned to the universalClient can be a **stale validator's price** or an entirely different value than the median actually applied on-chain via `CallUniversalCoreSetChainMeta`, whenever the fresh/stale composition and index ordering diverge between the two computations (e.g., a stale outlier sits at the `len/2` position of the full unfiltered array while it was excluded from the real median).

### Impact Explanation
The universalClient's `GetGasPrice` is used to size gas for outbound-transaction fee/gas estimation and fund-migration sweep math (`computeFundMigrationTransfer`, `GetFundMigrationSigningRequest`) which directly determine TSS-signed transfer amounts and gas budgeting for outbound EVM txs [8](#0-7) . If the queried "median" gas price diverges from the real applied on-chain price (because it was derived via a different, stale-inclusive rule), downstream consumers of this specific query path can act on a wrong price value. However, this is a read-path/query-shape inconsistency rather than a consensus divergence — it does not corrupt the ballot/UTX state machine itself, since `LastAppliedChainHeight`/EVM writes still use the correctly-filtered `fresh` median. The exploitable effect is bounded to a client-facing informational field feeding gas estimation, not fund theft or double-spend by itself, so this is a **Medium**-severity accounting/informational-integrity defect, analogous in nature (not in severity) to the referenced report.

### Likelihood Explanation
This is deterministically reachable by an unprivileged party: any external actor querying `GasPrice`/`AllGasPrices` while validator votes are partially stale (a routine, expected runtime condition given the 300s staleness window and no requirement that all UVs vote every block) will observe the divergence. No special validator collusion or privilege is needed — the mismatch is a normal consequence of ordinary vote timing on an honest-validator set.

### Recommendation
Compute `entry.MedianIndex` from the same filtered `fresh` set (and equivalent selection rule) used for `CallUniversalCoreSetChainMeta`, or better, store/report the actual `medianPrice`/`medianChainHeight` values directly instead of an index into the unfiltered array, so query responses can never diverge from the state actually applied on-chain.

### Proof of Concept
1. Chain has 3 UV signers for `observedChainId`; all vote and bootstrap (`LastAppliedChainHeight` set).
2. Validator A's vote goes stale (>300s old) while validators B and C continue re-voting.
3. On the next `VoteChainMeta` call, `fresh` contains only B and C's prices; `medianPrice` (applied on-chain) is computed from `{B,C}`.
4. `entry.MedianIndex = computeMedianIndex(entry.Prices)` is computed from `{A,B,C}` (all three, including stale A), landing on index `len/2 = 1` of the full sorted array — which may correspond to validator A's stale price, not the fresh median actually pushed on-chain.
5. A client calling `GasPrice`/`AllGasPrices` reads `Prices[MedianIndex]` and gets A's stale price, diverging from the real applied median, then feeds this into `universalClient` gas/fund-migration calculations.

Note: I was not able to fully trace every downstream consumer of `pushCore.GetGasPrice()` beyond `tx_builder.go`'s fund-migration path within the indexed context; a full audit of all call sites of this query would benefit from a live Devin session with complete repository access, as the index may not surface every usage.

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

**File:** x/uexecutor/keeper/query_server.go (L254-284)
```go
// GasPrice implements types.QueryServer.
// Sources data from ChainMetas (the new unified store) to maintain backward compatibility.
func (k Querier) GasPrice(goCtx context.Context, req *types.QueryGasPriceRequest) (*types.QueryGasPriceResponse, error) {
	if req == nil || req.ChainId == "" {
		return nil, status.Error(codes.InvalidArgument, "chain_id is required")
	}

	ctx := sdk.UnwrapSDKContext(goCtx)

	// Source from ChainMetas first (preferred post-upgrade storage)
	cm, err := k.ChainMetas.Get(ctx, req.ChainId)
	if err == nil {
		return &types.QueryGasPriceResponse{
			GasPrice: chainMetaToGasPrice(&cm),
		}, nil
	}
	if !errors.Is(err, collections.ErrNotFound) {
		return nil, status.Error(codes.Internal, err.Error())
	}

	// Fallback to legacy GasPrices store (pre-upgrade nodes)
	gasPrice, err := k.GasPrices.Get(ctx, req.ChainId)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			return nil, status.Errorf(codes.NotFound, "no gas price found for chain_id: %s", req.ChainId)
		}
		return nil, status.Error(codes.Internal, err.Error())
	}

	return &types.QueryGasPriceResponse{GasPrice: &gasPrice}, nil
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

**File:** universalClient/pushcore/pushCore.go (L214-244)
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
```

**File:** universalClient/chains/evm/tx_builder.go (L590-607)
```go
// computeFundMigrationTransfer returns the native amount to sweep from the old
// TSS address to the new one: balance - (gasPrice * gasLimit) - l1GasFee.
// The l1GasFee covers OP-stack sequencer data-availability charges (0 for
// non-L2 chains). All validators must compute the same value — any drift
// here breaks the TSS signing hash.
func computeFundMigrationTransfer(balance, gasPrice *big.Int, gasLimit uint64, l1GasFee *big.Int) (*big.Int, error) {
	gasCost := new(big.Int).Mul(gasPrice, new(big.Int).SetUint64(gasLimit))
	totalFee := new(big.Int).Set(gasCost)
	if l1GasFee != nil && l1GasFee.Sign() > 0 {
		totalFee.Add(totalFee, l1GasFee)
	}
	maxTransfer := new(big.Int).Sub(balance, totalFee)
	if maxTransfer.Sign() <= 0 {
		return nil, fmt.Errorf("insufficient balance for gas: balance=%s gasCost=%s l1GasFee=%s",
			balance.String(), gasCost.String(), l1GasFeeString(l1GasFee))
	}
	return maxTransfer, nil
}
```
