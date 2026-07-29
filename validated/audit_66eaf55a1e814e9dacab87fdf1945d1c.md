### Title
Single reverting `UniversalCore.setChainMeta` EVM call permanently discards validator votes and DoSes the chain-meta oracle for a chain - (File: `x/uexecutor/keeper/chain_meta.go`)

### Summary
`VoteChainMeta` records a Universal Validator's observed gas price / block height, and once enough fresh votes exist, pushes the computed median to the EVM `UniversalCore` contract via `CallUniversalCoreSetChainMeta`. If that single external EVM call reverts, the function returns an error *before* persisting the updated vote entry, so the Cosmos SDK message-level state branch is discarded in its entirety — the newly recorded vote is silently lost, not just the EVM write. Since votes always carry roughly the same real-world value (derived from the actual state of the external chain), a value that trips the destination contract's revert condition will make every subsequent vote for that chain fail identically, permanently freezing the chain-meta oracle for that chain. This mirrors the Notional M-7 pattern: one external protocol's revert converts an otherwise-recoverable step into a durable denial of service, because there is no isolation between the "record the vote" step and the "propagate to the external market/contract" step.

### Finding Description
`VoteChainMeta` (`x/uexecutor/keeper/chain_meta.go:62-189`) does the following on the write path (once bootstrapped or once the bootstrap quorum is reached): [1](#0-0) 
appends the new vote into `entry` in memory, then computes medians, and only afterward calls the EVM contract: [2](#0-1) 

If `CallUniversalCoreSetChainMeta` returns an error, `VoteChainMeta` returns immediately at line 174 without ever calling `k.SetChainMeta(ctx, ...)` to persist `entry` (unlike the two earlier early-return branches at lines 139 and 150, which do persist). Because Cosmos SDK message handling runs each `Msg` inside its own branched store that is discarded on error, returning a non-nil error here rolls back *all* state mutations attempted during this call — including the in-memory vote append that was never flushed to the store. The validator's vote is not partially recorded; it is entirely lost.

This differs from every other DerivedEVMCall call site in the module, which all catch the EVM error and record a `FAILED` `PCTx`/log-and-continue rather than aborting the whole message (e.g. `execute_inbound_gas.go:210-211` explicitly documents "Never return execErr, only nil"; `buildRevertOutbound` degrades gracefully on lookup/EVM failures). `VoteChainMeta` is the one path in `x/uexecutor` that still propagates a raw EVM failure as a hard message failure.

Because chain-meta values (price, block height) reflect the real, observed state of the external chain, honest Universal Validators voting the same real-world data will trigger the same underlying revert on `UniversalCore.setChainMeta` every time enough fresh votes accumulate. If the destination contract has any bound/validation that a legitimately-occurring external-chain value can trip (e.g. surge gas price, extreme block height delta, or any interface/implementation change on `UniversalCore`), the oracle write for that chain becomes permanently stuck: every fresh vote is discarded on arrival, so quorum can never durably form and the chain never re-bootstraps.

### Impact Explanation
`ChainMeta` gas price/height is consumed to compute outbound gas fees and refund amounts (`GetGasFeeInfoForRevertOutbound`, `GetGasPriceByChain`, refund/outbound-fee accounting throughout `x/uexecutor`). If the oracle write for a given source chain becomes permanently unwritable:
- Gas price/height data for that chain becomes stale and frozen at whatever was last successfully applied (or never applied if hit before bootstrap).
- Outbound gas-fee and refund computations for that chain rely on stale/incorrect chain-meta data, corrupting fee/refund accounting for all future inbounds/outbounds tied to that chain.
- No retry/backoff/skip mechanism exists — the only way to make progress is out-of-band intervention (contract upgrade or governance), which is a genuine DoS of a core accounting input reachable purely from ordinary chain activity (validators faithfully reporting real external-chain values), not from any privileged misbehavior.

This fits the "Registry and accounting path" and "State safety path" impact categories: gas-price/chain-meta use is corrupted for an entire chain with no unprivileged recovery path.

### Likelihood Explanation
Triggering requires the external chain to reach a state (e.g., an extreme but real gas price, or a chain-height jump) that the `UniversalCore.setChainMeta` implementation rejects — this is plausible during normal network conditions (gas spikes) without any attacker action, and is even more directly reachable if an attacker on the external chain can influence what value gets faithfully observed and voted by honest UVs (e.g., by causing a transaction with an unusually high gas price to be the relevant reference point). I could not inspect `CallUniversalCoreSetChainMeta`'s underlying Solidity implementation/bounds in this repository snapshot to confirm the exact revert condition, so likelihood is moderate rather than certain — but the architectural gap (loss of the vote and abort-on-first-failure with no isolation or retry) is verified directly in the Go code.

### Recommendation
Decouple vote-bookkeeping from the EVM propagation step: persist `entry` (including the newly recorded vote) unconditionally before or regardless of the `CallUniversalCoreSetChainMeta` outcome, and treat a failed EVM write the same way the rest of the module treats failed DerivedEVMCalls — log it, optionally retry on a later vote, but never let it roll back the vote itself. Consider retaining `LastAppliedChainHeight` unchanged on failure (so it doesn't silently update state that never made it on-chain) while still committing the vote tally, so that future votes with different/legitimate values can still reach quorum and eventually succeed instead of every vote being discarded identically forever.

### Proof of Concept
1. Bootstrap `ChainMeta` for a chain (3 honest UVs submit `MsgVoteChainMeta` with consistent real observed values) — oracle write succeeds normally.
2. External chain conditions change such that the next legitimately-observed price/blockNumber value, once written via `CallUniversalCoreSetChainMeta`, causes the `UniversalCore` contract call to revert (e.g., a bound check, or any transient issue in the external contract).
3. A UV submits `MsgVoteChainMeta` with this real value. `VoteChainMeta` appends the vote in memory, computes the median, calls `CallUniversalCoreSetChainMeta`, gets an error, and returns the error at `x/uexecutor/keeper/chain_meta.go:174` without ever calling `k.SetChainMeta`.
4. Because the Msg failed, Cosmos SDK discards the branch — the vote is not recorded at all.
5. Every subsequent UV vote reporting the same (or similar) real value hits the identical revert and is discarded the same way. The chain-meta oracle for this chain can never progress past this point without out-of-band intervention.

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

**File:** x/uexecutor/keeper/chain_meta.go (L171-180)
```go
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
