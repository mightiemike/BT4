### Title
All-or-nothing outbound extraction in `BuildOutboundsFromReceipt` discards valid outbounds and their tracking when any single log in the receipt fails validation - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`BuildOutboundsFromReceipt` iterates every log in an EVM receipt looking for `UniversalTxOutboundEvent` entries, and for each match performs two keeper lookups (`IsChainOutboundEnabled`, `GetTokenConfigByPRC20`) inside the loop. If any single iteration hits a disabled-chain or unmapped-token error, the function returns `nil, err` immediately — discarding every outbound already built from earlier, perfectly valid log entries in the same receipt. This is the on-chain analog of the C4 "calls inside a loop can DoS" pattern: the loop body performs calls that can fail on attacker-influenced input, and a single failure poisons the whole batch instead of being isolated to just the offending item.

### Finding Description [1](#0-0) 

```go
for _, lg := range receipt.Logs {
    ...
    event, err := types.DecodeUniversalTxOutboundFromLog(lg)
    ...
    outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
    if err != nil { return nil, fmt.Errorf(...) }
    if !outboundEnabled {
        return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
    }
    tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(ctx, event.ChainId, event.Token)
    if err != nil { return nil, err }
    outbound := &types.OutboundTx{...}
    outbounds = append(outbounds, outbound)
}
return outbounds, nil
```

This function is called from `AttachOutboundsToExistingUniversalTx` and `CreateUniversalTxFromReceiptIfOutbound`, both invoked *after* the EVM call that produced the receipt has already been committed (`writeCache()` in `ExecutePayloadV2`/`ExecuteInboundGasAndPayload`, etc.): [2](#0-1) 

A user-controlled `UniversalPayload` executes arbitrary EVM bytecode through the UEA (`CallUEAExecutePayload`). The `To` target of that payload can be an attacker-deployed contract that calls the Universal Gateway PC's withdraw entrypoint multiple times within one transaction, producing multiple `UniversalTxOutboundEvent` logs in a single receipt — some referencing perfectly valid, enabled chain/token pairs, and (deliberately or coincidentally, e.g. due to a chain being disabled mid-flight, or a token lacking a registry mapping) one referencing a disabled chain or unmapped PRC20. Because `BuildOutboundsFromReceipt` aborts and discards the whole `outbounds` slice on the first bad entry, none of the valid withdraw intents from that same receipt are ever attached to the `UniversalTx` or written to `PendingOutbounds`. The EVM-side effects (the PRC20 burn/lock that the gateway performed to emit those events) have already been committed by the time this function runs, since it's called post-`writeCache()`. The only trace left is a `RevertError` string on the UTX: [3](#0-2) 

Per the module's own documented lifecycle, there is no automatic recovery: `PendingOutbounds` entries are never auto-refunded or auto-retried because "there is no safe automatic resolution" — resolution is governance-driven only: [4](#0-3) 

Since these outbounds were never even created (the function returned before `attachOutboundsToUtx`/`PendingOutbounds.Set` ran), they are not even visible to the operator escape-hatch that inspects `PendingOutbounds` — the funds burned by the gateway simply vanish from tracked state.

### Impact Explanation
This breaks the "UniversalTx state is append-only and complete" invariant and can produce permanent loss of user-controlled funds: PRC20/native assets that were already withdrawn/burned on the Push-Chain EVM side (evidenced by the successfully-emitted `UniversalTxOutboundEvent` logs) have no corresponding `OutboundTx` record, no `PendingOutbounds` entry, and thus never get signed/broadcast by TSS nor refunded through the revert path. This falls under "permanent loss ... of user or protocol-controlled funds" and "corruption of ... canonical UniversalTx state" in the allowed-impact list.

### Likelihood Explanation
The trigger is reachable by any unprivileged user submitting a `MsgExecutePayload` / crosschain inbound whose target contract makes multiple gateway withdraw calls in one transaction. No privileged role is required. The scenario where one of several batched withdrawals lands on a disabled chain is also plausible without malicious intent (e.g., governance disables a chain, or a PRC20→external mapping is stale, between payload construction and execution), meaning this can occur accidentally, not only via deliberate attack. However, because the loss is generally confined to the payload's own withdraw intents (a self-inflicted grief in the common case unless a contract is executing withdrawals on behalf of multiple third parties in one transaction), the practical blast radius depends on how gateway contracts batch withdrawals; I could not fully verify from the indexed code whether the Gateway PC contract supports/normally performs multi-recipient batched withdrawals in a single transaction versus one-withdrawal-per-tx, which would affect real-world exploitability. That verification requires access to the Solidity gateway contract, which is out of this repo's index.

### Recommendation
Change `BuildOutboundsFromReceipt` to process each log independently: on a per-log validation failure (disabled chain, missing token config), skip and record that specific outbound as failed/needing-manual-review rather than discarding the entire batch, and still attach all other successfully validated outbounds from the same receipt. Alternatively, decouple outbound extraction from the EVM commit boundary (validate all outbound targets first in a dry-run before allowing `writeCache()` to commit the underlying burn) so a single invalid withdraw cannot render other, already-valid withdrawals untrackable.

### Proof of Concept
1. Attacker's UEA payload `To` targets a contract the attacker controls that, in a single EVM call, invokes the Universal Gateway PC withdraw entrypoint twice: once for `chainA` (currently `IsOutboundEnabled = true`, valid token mapping) and once for `chainB` (currently `IsOutboundEnabled = false`).
2. `ExecutePayloadV2`/`CallUEAExecutePayload` executes successfully; both `UniversalTxOutboundEvent` logs are emitted; `writeCache()` commits the EVM state, including whatever token burn/lock the gateway performed for both withdrawals.
3. `AttachOutboundsToExistingUniversalTx` → `BuildOutboundsFromReceipt` iterates the two logs; the `chainA` outbound is built successfully and appended to `outbounds`, then the `chainB` log fails `IsChainOutboundEnabled` and the function returns `nil, err` — discarding the already-built valid `chainA` outbound.
4. The caller only records `utx.RevertError = err.Error()`; no `OutboundTx` is appended, and `PendingOutbounds.Set` is never called for either withdrawal.
5. The funds associated with the valid `chainA` withdrawal are now burned/committed on Push Chain but have no outbound record, no TSS signing event, and no refund path — permanently stuck.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L16-67)
```go
func (k Keeper) BuildOutboundsFromReceipt(
	ctx context.Context,
	utxId string,
	receipt *evmtypes.MsgEthereumTxResponse,
) ([]*types.OutboundTx, error) {

	outbounds := []*types.OutboundTx{}
	universalGatewayPC := strings.ToLower(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address)

	k.Logger().Debug("building outbounds from receipt", "utx_id", utxId, "tx_hash", receipt.Hash, "log_count", len(receipt.Logs))

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L317-333)
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
```

**File:** x/uexecutor/README.md (L273-282)
```markdown
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
