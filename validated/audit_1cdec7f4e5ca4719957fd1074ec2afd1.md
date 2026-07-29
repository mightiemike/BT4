### Title
Single failing outbound in a multi-outbound EVM receipt discards all outbounds, permanently stranding already-burned/withdrawn funds - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
`BuildOutboundsFromReceipt` loops over every `UniversalTxOutbound` log emitted by a single Push Chain EVM transaction and builds an `OutboundTx` per log. This is the exact structural analog of the reported Vault pattern: a single transaction can settle multiple "operations" (here, multiple destination-chain outbounds derived from one receipt, e.g. a multi-hop swap or batched withdrawal), and the settlement is not isolated per-operation.

### Finding Description
`BuildOutboundsFromReceipt` iterates `receipt.Logs` and, for each `UniversalTxOutbound` event, calls `k.uregistryKeeper.IsChainOutboundEnabled` and `k.uregistryKeeper.GetTokenConfigByPRC20`: [1](#0-0) 
If any single log in the batch fails one of these registry lookups (unsupported/disabled destination chain, unknown PRC20-to-external-token mapping, or a decode error), the function returns `nil, err` immediately, discarding the entire `outbounds` slice built so far — including outbounds for logs that were processed successfully before the failing one: [2](#0-1) 

This function is called from `AttachOutboundsToExistingUniversalTx`, which is invoked after a UEA payload execution has already succeeded and the EVM state has been committed: [3](#0-2) 

The call site in `execute_inbound_funds_and_payload.go` treats an attach failure as non-fatal to the overall UTX: the `PCTx` for payload execution is already recorded as `SUCCESS` (because the EVM call already committed via `writeCache()` in `ExecutePayloadV2`), and the only trace left behind is a `RevertError` string on the UTX — no outbound is retried or recreated, and no compensating action (re-mint, revert) is taken for the outbound legs that were dropped: [4](#0-3) 

The same pattern repeats in `execute_inbound_gas_and_payload.go`: [5](#0-4) 

Because Push Chain's `x/uexecutor` README explicitly documents that "one inbound's payload can spawn multiple outbounds (e.g. a multi-hop cross-chain swap or a batched refund)", multi-outbound receipts are a supported, ordinary user code path — not a hypothetical: [6](#0-5) 

### Impact Explanation
The EVM-side effects of a `UniversalTxOutbound` event (e.g. PRC20 burn/lock performed by `UniversalGatewayPC` inside the already-committed EVM transaction) are final once `writeCache()` runs. If `BuildOutboundsFromReceipt` fails partway through a multi-log receipt, the Push Chain ledger never records any `OutboundTx` for that receipt (not even the ones that would have succeeded), and `PendingOutbounds` never gets an entry for them. No relayer/TSS flow is ever triggered to deliver funds to the destination chain(s). The user's PRC20 is effectively burned/withdrawn on Push Chain with no corresponding outbound record and no automatic recovery — this is a permanent loss of user funds reachable by an ordinary `MsgExecutePayload` submission (or an inbound payload) that triggers a multi-leg outbound where at least one leg targets a disabled chain or an unmapped PRC20↔external-token pair.

### Likelihood Explanation
Likelihood is moderate: it requires (a) a payload/contract that emits more than one `UniversalTxOutbound` log in a single receipt (a supported, documented pattern — multi-hop swaps, batched refunds), and (b) at least one of those legs referencing a chain that is outbound-disabled or a PRC20 address without a registered token config. Both conditions are plausible for ordinary users interacting with third-party contracts/integrations built on top of the UEA/CEA execution path, without requiring any privileged actor, malicious validator, or malicious relayer.

### Recommendation
Process each `UniversalTxOutbound` log independently (analogous to the reported try/catch settlement pattern): wrap the per-log registry lookups in error isolation so a single bad leg does not discard already-validated outbounds from the same receipt. For a leg that fails registry validation, either (a) still record it as an `OutboundTx` in an `ABORTED`/needs-attention state carrying the error, or (b) trigger the same revert/re-mint compensation path used elsewhere (`handleFailedOutbound`/`buildRevertOutbound`) for that specific leg only, while allowing the remaining legs to be attached and proceed normally. At minimum, `AttachOutboundsToExistingUniversalTx`/`CreateUniversalTxFromReceiptIfOutbound` should never let an error in one log silently drop the entire batch when the underlying EVM burn/withdraw effects have already been committed.

### Proof of Concept
1. A UEA executes a payload that calls a contract performing two withdrawals in a single EVM tx, both emitting `UniversalTxOutbound` on `UniversalGatewayPC`: leg A to chain X (outbound-enabled, token mapped) and leg B to chain Y (outbound-disabled, or referencing a PRC20 with no `TokenConfig` entry for that chain).
2. `ExecutePayloadV2` commits the EVM state (`writeCache()`), so both PRC20 burns for legs A and B are final on Push Chain.
3. `AttachOutboundsToExistingUniversalTx` → `BuildOutboundsFromReceipt` processes log A successfully, appends outbound A to the local slice, then hits log B and returns `nil, err` from `IsChainOutboundEnabled`/`GetTokenConfigByPRC20`, discarding outbound A as well.
4. `attachOutboundsToUtx` is never called; no `OutboundTx` entries exist in the UTX; `PendingOutbounds` has nothing for either leg; the UTX only records `RevertError` as a string.
5. Funds burned for both legs A and B are permanently unrecoverable — no relayer ever picks up leg A even though it was individually valid, and no revert/re-mint occurs for either leg.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L27-67)
```go
	for _, lg := range receipt.Logs {
		if lg.Removed {
			continue
		}

		if strings.ToLower(lg.Address) != universalGatewayPC {
			continue
		}

		if len(lg.Topics) == 0 {
			continue
		}

		if strings.ToLower(lg.Topics[0]) != strings.ToLower(types.UniversalTxOutboundEventSig) {
			continue
		}

		event, err := types.DecodeUniversalTxOutboundFromLog(lg)
		if err != nil {
			return nil, fmt.Errorf("failed to decode UniversalTxWithdraw: %w", err)
		}

		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L141-155)
```go
// AttachOutboundsToExistingUniversalTx
// Used when UniversalTx already exists (e.g. inbound execution)
// It attaches outbounds extracted from receipt to the existing utx.
func (k Keeper) AttachOutboundsToExistingUniversalTx(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	utx types.UniversalTx,
) error {
	outbounds, err := k.BuildOutboundsFromReceipt(ctx, utx.Id, receipt)
	if err != nil {
		return err
	}

	return k.attachOutboundsToUtx(ctx, utx.Id, outbounds, "")
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L317-334)
```go
	} else if receipt != nil {
		k.Logger().Info("payload executed successfully (gas+payload)",
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
	}
```

**File:** x/uexecutor/README.md (L82-85)
```markdown
#### 3. `OutboundTx` — outbounds spawned by Push Chain execution

A list, because one inbound's payload can fan out into multiple destination-chain transactions (e.g. a multi-hop cross-chain swap or a batched refund).

```
