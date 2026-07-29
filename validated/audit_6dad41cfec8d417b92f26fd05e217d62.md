Confirmed: there is no per-transaction cap on the number of `OutboundTx` entries a single EVM execution can create, and zero-amount inbound/outbound flows are an explicitly supported code path (`inbound_zero_amount_test.go`), meaning dust-value outbounds are not rejected.

### Title
Unbounded outbound fan-out from a single gasless payload execution lets an attacker force costly TSS-signing and broadcast obligations onto Universal Validators for dust-value transfers - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
The Across bug lets an attacker cheaply batch many dust deposits on a low-cost chain and force a costly-to-execute refund leaf onto a public-good executor on an expensive chain. Push Chain's analog is `BuildOutboundsFromReceipt`, which scans **every** log in a single EVM receipt for `UniversalTxOutboundEvent` and turns each one into a new `OutboundTx`, with no limit on count and no minimum-value check. A single gasless `MsgExecutePayload` (or a single inbound execution) whose target contract loops calls into `UniversalGatewayPC`'s outbound-emitting function can therefore mint an arbitrary number of pending outbounds in one Cosmos transaction, each one requiring a full DKLS TSS signing session and broadcast by Universal Validators — the same "cheap batching, expensive execution" asymmetry described in the report, just moved from an L2/L1 relationship to a Push-Chain/external-chain relationship.

### Finding Description
`ExecuteInboundFundsAndPayload` / `ExecuteInboundGasAndPayload` and `ExecutePayload` (direct `MsgExecutePayload`) all funnel through `AttachOutboundsToExistingUniversalTx` / `CreateUniversalTxFromReceiptIfOutbound`, which call: [1](#0-0) 

This loop has no upper bound on `len(outbounds)`. Each matched log becomes an `OutboundTx` appended via `attachOutboundsToUtx`, which unconditionally writes a new `PendingOutbounds` entry and emits an `OutboundCreatedEvent` for every single one: [2](#0-1) 

Because `MsgExecutePayload` is in the gasless allowlist, the Cosmos-level transaction fee is skipped entirely: [3](#0-2) 

The only cost the attacker pays is the UEA-side EVM gas for `executeUniversalTx`, which is bounded by `universal_payload.GasLimit` supplied by the attacker themselves in the payload: [4](#0-3) 

A contract deployed as (or called by) the UEA can loop calling `UniversalGatewayPC`'s withdraw/outbound method with a minimal (even zero) amount per iteration — `inbound_zero_amount_test.go` confirms zero-amount flows are accepted rather than rejected. Within one EVM gas budget, dozens of such calls can each emit a `UniversalTxOutboundEvent`, and `BuildOutboundsFromReceipt` will turn every one into a separate pending outbound targeting whatever destination chain the attacker chooses (e.g. an expensive L1). Each of these then requires the off-chain Universal Client to run a full DKLS TSS signing session and broadcast a transaction: [5](#0-4) 

This reproduces the report's core asymmetry: cheap, attacker-controlled batching (one gasless Push Chain execution, gas paid by the attacker's own bounded budget) produces a multiplied, expensive obligation on a different party — here Universal Validators' TSS coordinators and the destination chain's gas market — rather than a single relayer/dataworker as in Across, but the "public good" strain and dust-value congestion are structurally identical.

### Impact Explanation
Falls under the allowed "denial of service ... reachable without privileged control" and touches the explicitly named "TSS coordination" and "outbound creation" audit pivots. Impact is availability degradation of the Universal Validator TSS/broadcast pipeline and pollution of `PendingOutbounds`/UTX state with spam entries, potentially delaying or starving legitimate outbound signing sessions and inflating validator operating costs on the destination chain (gas for many broadcasts). It does not directly cause loss/mint/burn of protocol funds, so it is a state-growth / cost-griefing DoS rather than a fund-safety bug.

### Likelihood Explanation
Requires only an ordinary user: deploy a contract behind their own UEA (or as the payload target), submit a single gasless `MsgExecutePayload` (or trigger via a `FUNDS_AND_PAYLOAD` inbound) whose payload loops calls into `UniversalGatewayPC`, and pay for the bounded EVM gas of that one call. No validator or admin cooperation is needed, and the message type is gasless at the Cosmos layer, lowering the cost floor to just the EVM execution gas the attacker themselves funds.

### Recommendation
- Cap the number of outbounds `BuildOutboundsFromReceipt` will attach per receipt/UTX (e.g. a module param `MaxOutboundsPerExecution`), rejecting or truncating excess with a clear error.
- Enforce a minimum outbound amount (or minimum gas-fee-to-relayer ratio) per `OutboundTx`, similar to a dust threshold, so dust-value spam outbounds cannot be created cheaply.
- Consider rate-limiting outbound creation per UEA/sender within a block or time window at the `x/uexecutor` keeper level, independent of the EVM gas limit.

### Proof of Concept
Conceptual (not executed against a live node — requires deploying an EVM contract and wiring it as a UEA target, which is outside static analysis reach here):
1. Deploy a "spammer" contract whose function loops N times, each iteration calling `UniversalGatewayPC`'s withdraw/outbound-emitting method with `amount = 0` (or 1 wei) and `destinationChain = eip155:1` (Ethereum).
2. Set this contract as the UEA's `To` target in a `UniversalPayload` with `GasLimit` large enough to fit N iterations.
3. Submit `MsgExecutePayload` (gasless) referencing this payload; `ExecutePayloadV2` → `CallUEAExecutePayload` executes it, producing a receipt with N `UniversalTxOutboundEvent` logs.
4. `CreateUniversalTxFromReceiptIfOutbound` → `BuildOutboundsFromReceipt` creates N `OutboundTx` entries in one UTX, each indexed into `PendingOutbounds`, forcing N TSS signing sessions and N broadcasts by Universal Validators to Ethereum from a single attacker-paid, gasless Cosmos transaction.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L16-47)
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L339-371)
```go
func (k Keeper) attachOutboundsToUtx(
	ctx sdk.Context,
	utxId string,
	outbounds []*types.OutboundTx,
	revertMsg string, // revert msg if the outbound is for a inbound revert
) error {

	if len(outbounds) == 0 {
		return nil
	}
	return k.UpdateUniversalTx(ctx, utxId, func(utx *types.UniversalTx) error {

		for _, outbound := range outbounds {

			utx.OutboundTx = append(utx.OutboundTx, outbound)

			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```

**File:** app/txpolicy/gasless.go (L17-25)
```go
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
```

**File:** x/uexecutor/keeper/evm.go (L172-193)
```go
	gasLimit := new(big.Int)
	gasLimit, ok := gasLimit.SetString(universal_payload.GasLimit, 10)
	if !ok {
		return nil, fmt.Errorf("invalid gas limit: %s", universal_payload.GasLimit)
	}

	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		from,
		ueaAddr,
		big.NewInt(0),
		gasLimit,
		true,  // commit = true (real tx, not simulation)
		false, // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		false, // not a module sender
		nil,
		"executeUniversalTx",
		abiUniversalPayload,
		verificationData,
	)
}
```

**File:** universalClient/README.md (L86-112)
```markdown
#### Outbound Flow

```
Push Chain                        Universal Validators               External Chain
      |                                   |                               |
      |  Pending outbound created         |                               |
      |---------------------------------->|                               |
      |                                   |  Coordinator assigns nonce    |
      |                                   |  Selects threshold subset     |
      |                                   |  DKLS signing session         |
      |                                   |                               |
      |                                   |  Broadcast signed tx          |
      |                                   |------------------------------>|
      |                                   |                               |
      |                                   |  Monitor for confirmation     |
      |                                   |<------------------------------|
      |                                   |                               |
      |                    Vote result    |                               |
      |<----------------------------------|                               |
```

1. The Push Chain listener picks up the pending outbound
2. A rotating coordinator assigns a nonce, selects a threshold subset of participants, and creates a DKLS signing session
3. Each participant independently verifies the signing request against their own RPC view of the destination chain, then collaborates in the distributed signing protocol
4. Every participating validator broadcasts the identical signed transaction; the first to land wins, the rest are idempotent (same nonce, same signature, same tx hash)
5. The resolver monitors the destination chain for confirmation
6. On success, the event is marked complete. On failure (reverted or not found after retries), validators vote failure on Push Chain, which triggers a refund to the user
```
