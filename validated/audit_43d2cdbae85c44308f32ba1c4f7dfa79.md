### Title
Destination-chain outbound-disabled check runs *after* payload state is committed, permanently stranding funds with no outbound/refund record - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
`x/uexecutor` deliberately performs its inbound-side "is this chain enabled" gate (`IsChainInboundEnabled`) *before* any state mutation in `VoteInbound` [1](#0-0) . The outbound-side equivalent, `IsChainOutboundEnabled`, is checked inside `BuildOutboundsFromReceipt`, which only runs *after* `ExecutePayloadV2` has already executed the user's UEA payload and the EVM receipt has been committed [2](#0-1) . This is the structural analog of the reported `whenNotPaused` receiver-failure bug: a gating flag causes a hard failure for a message whose underlying value-moving side effect has already landed, with no automatic recovery path.

### Finding Description
In `ExecuteInboundFundsAndPayload` and `ExecuteInboundGasAndPayload`, once the UEA payload executes successfully (`ExecutePayloadV2` returns a receipt), the code calls `AttachOutboundsToExistingUniversalTx` to parse `UniversalTxOutbound` events from that receipt and create the corresponding `OutboundTx` records [3](#0-2) .

`AttachOutboundsToExistingUniversalTx` delegates to `BuildOutboundsFromReceipt`, which, for every decoded outbound event, checks `IsChainOutboundEnabled` for the destination chain and returns a hard error if it's disabled — discarding the event entirely instead of creating an `OutboundTx`: [2](#0-1) 

```go
outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
...
if !outboundEnabled {
    k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
    return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
}
```

By the time this check runs, `ExecutePayloadV2`'s EVM transaction has already been committed — the UEA's payload already ran (e.g., a call into `UniversalGatewayPC` that logically locked/burned the PRC20 and emitted the `UniversalTxOutbound` event for withdrawal). When `BuildOutboundsFromReceipt` errors, the caller only stores the error string on `UniversalTx.RevertError` and returns `nil` — it does **not** roll back the payload execution and does **not** create any `OutboundTx` record: [4](#0-3) 

Because no `OutboundTx` is created, the outbound never enters `PendingOutbounds`, never gets a TSS signing session, and is never delivered to the destination chain. Contrast this with the `x/uexecutor` README's own documented invariant for outbounds that *do* get created but stall: those keep a `PendingOutbounds` audit trail explicitly for operator investigation and possible manual resolution [5](#0-4) . Here, the outbound is never created at all — only a free-text `RevertError` field is left on the UTX, and there is no equivalent to the admin `RevertStuckInbound` escape hatch (which only covers stuck *inbound* ballots, not this outbound-creation failure) [6](#0-5) .

This mirrors the external report precisely: a "chain paused/disabled" gate causes a receiver-side (here, outbound-creation-side) hard failure for a message whose value-moving effect has already been committed, with no automatic or even documented manual retry mechanism for this specific failure mode.

### Impact Explanation
Funds that the user's payload already moved into the gateway/vault contract on Push Chain (with intent to withdraw to an external chain) become permanently unrecoverable through the normal flow: no `OutboundTx`, no `PendingOutbounds` entry, no TSS signing request, and no refund/revert path is ever generated. The only trace is a `RevertError` string on the UTX. This matches the "In scope" category of permanent freezing/loss of user funds in universal execution/outbound-creation flows.

### Likelihood Explanation
Triggering this requires `IsChainOutboundEnabled` to be `false` for the destination chain at the exact moment a user's in-flight `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` inbound payload executes and produces an outbound event. This state is set via `x/uregistry` admin/governance chain-config updates (analogous to a contract owner calling `pause()`), which is a routine operational action (e.g., maintenance, incident response) rather than "admin abuse" — the same framing that made the original Cyfrin-verified report valid despite pause being an admin-controlled toggle. Any user whose cross-chain payload happens to execute during that window is affected, with no attacker action required beyond normal transaction submission.

### Recommendation
Move the `IsChainOutboundEnabled` check earlier so it can gate payload execution *before* any state-mutating EVM call runs, or — if the payload has already executed and the outbound event decoded — persist the extracted outbound event (with a distinguishing status, e.g. `ABORTED`/`BLOCKED`) into `PendingOutbounds`/`UniversalTx.OutboundTx` instead of discarding it via a bare error. Provide an explicit admin/governance recovery path (symmetric to `MsgRevertStuckInbound`) that can re-attach or refund an outbound that failed to be created solely because the destination chain was disabled at execution time.

### Proof of Concept
1. Registry admin disables outbound for chain `eip155:X` via `x/uregistry` (`ChainConfig.Enabled.IsOutboundEnabled = false`) for routine maintenance, while inbound remains enabled.
2. A user submits a normal `FUNDS_AND_PAYLOAD` inbound (via UV votes) whose `UniversalPayload` calls `UniversalGatewayPC` to withdraw funds to chain `eip155:X`.
3. UV quorum is reached; `ExecuteInboundFundsAndPayload` runs, `ExecutePayloadV2` succeeds and commits the EVM state (funds locked/burned, `UniversalTxOutbound` event emitted) — see `x/uexecutor/keeper/execute_inbound_funds_and_payload.go:309-325`.
4. `AttachOutboundsToExistingUniversalTx` → `BuildOutboundsFromReceipt` sees `IsChainOutboundEnabled` is `false` and returns an error (`x/uexecutor/keeper/create_outbound.go:49-57`); no `OutboundTx` is created.
5. The error is stored only as `UniversalTx.RevertError`; the UTX has an empty `OutboundTx` slice permanently. The user's funds, already committed by the payload, are unrecoverable through the protocol's normal or admin-escape-hatch flows (unlike the analogous inbound-ballot-expiry case, which has `MsgRevertStuckInbound`).

Note: I could not fully trace whether any later off-chain/operator process (outside the indexed repository, e.g., manual governance proposal or `RESCUE_FUNDS` tx type usage mentioned in `x/uexecutor/README.md` and `test/integration/uexecutor/rescue_funds_test.go`) is intended to cover this exact gap — the index did not show a direct wiring from this specific failure into a `RESCUE_FUNDS` outbound. A Devin session with full repository access could verify whether `RESCUE_FUNDS` is actually reachable for this scenario.

### Citations

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L31-39)
```go
	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}
```

**File:** x/uexecutor/keeper/create_outbound.go (L49-57)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L309-325)
```go
	} else if receipt != nil {
		k.Logger().Info("payload executed successfully",
			"utx_key", universalTxKey,
			"uea", ueaAddr.Hex(),
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
		payloadPcTx.Status = "SUCCESS"

		if attachErr := k.AttachOutboundsToExistingUniversalTx(sdkCtx, receipt, utx); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
```

**File:** x/uexecutor/README.md (L262-282)
```markdown
### `PendingOutbounds`

- **Created** by chain code at outbound creation in `create_outbound.go` —
  BEFORE any validator vote. The chain knows the outbound exists because it
  generated the destination-chain transaction itself; validators are tasked
  with observing whether/how it landed.
- **Keyed** by deterministic chain-derived `outbound_id`.
- **Variant-aware:** validator votes append `OutboundObservationVariant`s as
  they arrive (`RecordOutboundVote` inside `VoteOutbound`). Multiple variants
  per outbound indicate validator divergence on the destination-chain
  observation (different `success`/`tx_hash`/`error_msg`/`gas_fee_used`).
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```

**File:** x/uexecutor/keeper/admin_revert.go (L15-26)
```go
// RevertStuckInbound creates an INBOUND_REVERT outbound for an inbound whose
// ballot has expired without finalizing. The revert outbound enters the normal
// PendingOutbounds flow; UVs sign it via TSS and broadcast it to the source
// chain, refunding the user.
//
// Strict precondition: the ballot for the supplied inbound must be in EXPIRED
// state. Admin must run MsgRecomputeBallotQuorum first to drive a stuck ballot
// to EXPIRED if it isn't already (recompute auto-expires when no eligible
// voters remain).
//
// Returns the new UTX ID and revert outbound ID for telemetry.
func (k Keeper) RevertStuckInbound(ctx context.Context, inbound types.Inbound) (utxId, outboundId string, err error) {
```
