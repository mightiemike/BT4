## Title
Chain-meta oracle's `LastAppliedChainHeight`/median gas price can move backward via vote staleness, breaking the intended high-water-mark invariant - (File: `x/uexecutor/keeper/chain_meta.go`)

### Summary
`VoteChainMeta` treats `entry.LastAppliedChainHeight` as a monotonic high-water mark: any incoming vote must individually exceed it [1](#0-0) , but the value written back after each vote is the **unconditional** median of the currently "fresh" (non-stale) votes, with no check that the new median is `>=` the previous one [2](#0-1) . Because stale votes silently drop out of the median pool purely due to elapsed time [3](#0-2) , the order statistic ("upper median" at index `len/2`) can shift down when a high-valued vote ages out, producing a `LastAppliedChainHeight` / gas price that is *lower* than the value already recorded and previously published on-chain — the same "lowered high-water-mark" pattern described in the source report.

### Finding Description
The per-vote gate only checks the *incoming* vote against the stored high-water mark:

```go
if bootstrapped && blockNumber <= entry.LastAppliedChainHeight {
    return fmt.Errorf(...)
}
``` [4](#0-3) 

It never re-validates the *other* validators' stored (but currently fresh) entries. Each validator's own stored value is only checked against the global high-water mark at the moment *that validator* votes; once accepted it sits in `entry.Prices` / `entry.ChainHeights` unchanged until that validator votes again, and is included or excluded from the median purely by `StoredAts` age vs. `chainMetaVoteStalenessSeconds` (300s) [5](#0-4) [3](#0-2) .

The upper-median function picks `sorted[len/2]` [6](#0-5) . When the current largest fresh entry (which determined the current high-water mark) times out and is dropped from the fresh pool, the set shrinks (e.g. from 4 to 3 elements) and the *same order-statistic index* now resolves to a strictly smaller stored value — even though every remaining vote is completely honest and was legitimately accepted at the time it was cast. The result is written unconditionally:

```go
entry.LastAppliedChainHeight = medianChainHeight
``` [7](#0-6) 

with the corresponding `priceBig`/`chainHeightBig` pushed to the on-chain `UniversalCore` oracle via `CallUniversalCoreSetChainMeta`, unconditionally overwriting the previously higher published values [8](#0-7) .

### Impact Explanation
This corrupts the gas-price/chain-height oracle that feeds `getOutboundTxGasAndFees`, `gasPriceByChainNamespace`, and the excess-gas refund accounting in `applyGasRefund` [9](#0-8) . A regression of the published gas price/chain height:
- Also reopens the `blockNumber <= LastAppliedChainHeight` staleness gate to values that were previously rejected as stale, allowing re-acceptance of block heights that had already been superseded.
- Can misroute gas-fee accounting for outbound relaying (an in-scope "corruption of ... gas fee accounting, ... chain config use" impact) since fee/gas-price quoting downstream of the oracle can silently regress to a stale, lower number after being briefly higher.

This is a genuine analog of the report's core flaw — a value meant to act as a monotonic high-water mark is overwritten unconditionally by a different code path (here, staleness-driven exclusion in the median recompute) rather than being guarded to only move forward.

### Likelihood Explanation
The trigger requires no malicious validator: it happens whenever a validator whose vote is currently pinning the median simply pauses voting for longer than `chainMetaVoteStalenessSeconds` (300s) while other validators continue voting normally with values below the now-expired one — a completely ordinary operational condition (e.g., an RPC hiccup, restart, or slow block-height movement making a re-vote unnecessary for a UV that dedupes on-value). Because this is purely UV-vote/timing-driven rather than a message an ordinary unprivileged user can directly submit, likelihood is **Medium**: it is not attacker-controllable on demand, but it is reachable purely through honest, unprivileged (non-malicious) validator behavior, matching the scope's "honest validators and honest nodes" framing.

### Recommendation
In `VoteChainMeta`, after computing `medianChainHeight` (and similarly `medianPrice` if the oracle price is meant to be monotonic non-decreasing too), only update `entry.LastAppliedChainHeight` and push the EVM write when `medianChainHeight > entry.LastAppliedChainHeight` (or explicitly document/enforce that regression is intentional and safe for gas price semantics). At minimum, avoid excluding a still-recorded-but-stale high value from the "floor" used for the height gate, e.g., track a separate monotonic max alongside the vote-freshness pool, or require the new median to be `>=` the previous applied height before writing to the EVM oracle.

### Proof of Concept
Sequence (single observed chain, 4 validators V1–V4, `chainMetaVoteStalenessSeconds = 300`):
1. `t=0`: V1 votes height=100, V2 votes height=105, V3 votes height=110 → bootstrap (3 votes), sorted `[100,105,110]`, `idx1=105`... (adjust to 4-voter case below for clean numbers) — using 4 voters directly:
2. `t=0`: V1=100, V2=105, V3=110, V4=1,000,000 all vote (assume bootstrap threshold already met or exercised via repeated votes). `fresh=[100,105,110,1000000]`, `n=4`, `idx=len/2=2` → sorted `[100,105,110,1000000]` → `LastAppliedChainHeight=110`. This is pushed to the EVM oracle.
3. No one re-votes for `>300s`. At `t=305`, V4's vote (the only one holding a value above 110) becomes stale by the `chainMetaVoteStalenessSeconds` cutoff [10](#0-9) , while V1/V2/V3's earlier entries are still within the window (or have been legitimately refreshed with values still below 110, e.g. V1 re-votes at `t=305` with height=111, satisfying `111 > 110`).
4. Recompute: `fresh = [111(V1), 105(V2), 110(V3)]` (V4 excluded as stale). `n=3`, `idx=len/2=1` → sorted `[105,110,111]` → new median = `110`... To force a strict drop, use V1's forced re-vote value just above 110 but V2/V3 unchanged below: `fresh=[111,105,110]` sorted `[105,110,111]`, idx1=`110`, equal to old value in this instance; by tuning the concrete numbers (e.g., V2=101, V3=104, and the forced V1 revote =111) the resulting median (`104`) is strictly less than the prior `LastAppliedChainHeight` of `110`, which is then written unconditionally via `entry.LastAppliedChainHeight = medianChainHeight` [11](#0-10)  and pushed on-chain via `CallUniversalCoreSetChainMeta` [12](#0-11) , demonstrating the high-water mark moving backward purely from stale-vote exclusion.

**Uncertainty**: I could not verify from the index whether the Solidity `UniversalCore.setChainMeta` function itself enforces any additional monotonicity check on-chain (the ABI signature was located, but its implementation is in the external `push-chain-core-contracts` repo, not in this repo's index) — if that contract independently rejects height/price regressions, this finding would be mitigated at the EVM layer even though the Cosmos-side keeper has no such guard. I recommend verifying this in a Devin session with full repo/contract access before treating this as fully exploitable end-to-end.

### Citations

**File:** x/uexecutor/keeper/chain_meta.go (L16-19)
```go
const (
	// chainMetaVoteStalenessSeconds is the maximum age (in seconds) of a stored vote
	// that is still eligible to be included in the median calculation.
	chainMetaVoteStalenessSeconds uint64 = 300
```

**File:** x/uexecutor/keeper/chain_meta.go (L70-85)
```go
	bootstrapped := entry.LastAppliedChainHeight > 0

	// Stale-height check applies only after bootstrap. During cold-start there
	// is no committed reference height yet, so any positive vote is acceptable.
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

**File:** x/uexecutor/keeper/chain_meta.go (L156-180)
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
	if err := k.SetChainMeta(ctx, observedChainId, entry); err != nil {
		return sdkerrors.Wrap(err, "failed to set updated chain meta entry")
	}
```

**File:** x/uexecutor/keeper/chain_meta.go (L191-201)
```go
// upperMedianUint64 sorts the slice by the extracted key and returns the value at index len/2
// (upper median for even-length slices).
func upperMedianUint64[T any](items []T, key func(T) uint64) uint64 {
	type kv struct{ k uint64; v T }
	arr := make([]kv, len(items))
	for i, item := range items {
		arr[i] = kv{k: key(item), v: item}
	}
	sort.SliceStable(arr, func(i, j int) bool { return arr[i].k < arr[j].k })
	return arr[len(arr)/2].k
}
```

**File:** x/uexecutor/keeper/outbound.go (L174-257)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
			swapFallbackReason = fmt.Sprintf("swap refund failed: %s", err.Error())
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
	if err != nil {
		refundPcTx.Status = "FAILED"
		refundPcTx.ErrorMsg = err.Error()
	} else {
		refundPcTx.TxHash = resp.Hash
		refundPcTx.GasUsed = resp.GasUsed
		refundPcTx.Status = "SUCCESS"
	}

	outbound.PcRefundExecution = refundPcTx
	outbound.RefundSwapError = swapFallbackReason
}
```
