### Title
`AttachRescueOutboundFromReceipt` skips the outbound-enabled gate that `BuildOutboundsFromReceipt` enforces, permanently freezing rescue funds - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
The referenced Blend bug is a case where one execution path (flash loans) mutates protocol state without applying the same status/risk-gate checks enforced on the "normal" path (borrow), letting a frozen pool be drained anyway. Push Chain's `x/uexecutor` module has the same structural gap: normal outbound creation (`BuildOutboundsFromReceipt`) checks `uregistryKeeper.IsChainOutboundEnabled` before ever attaching an `OutboundTx`, but the "rescue funds" outbound-creation path (`AttachRescueOutboundFromReceipt`) never performs this check, letting outbounds for a chain whose outbound is administratively disabled get created and permanently stuck.

### Finding Description
Every normal outbound is created via `BuildOutboundsFromReceipt`, which explicitly gates on the destination chain's enabled flag before building the `OutboundTx`: [1](#0-0) 

This function is used by `AttachOutboundsToExistingUniversalTx` and `CreateUniversalTxFromReceiptIfOutbound`, both of which therefore inherit the outbound-enabled guard.

However, `AttachRescueOutboundFromReceipt` — the code path that lets a user recover funds stuck on a source chain after a failed CEA deposit or a reverted `INBOUND_REVERT` — builds and attaches its `OutboundTx` directly, without ever calling `BuildOutboundsFromReceipt` or `uregistryKeeper.IsChainOutboundEnabled`: [2](#0-1) 

The eligibility checks performed here only validate the UTX's inbound state (CEA-deposit-failed, or `INBOUND_REVERT` reverted) and duplicate-rescue prevention — never the chain's `Enabled.IsOutboundEnabled` flag: [3](#0-2) 

This function is reachable from ordinary user activity: it is invoked from the `MsgExecutePayload` handler flow (`msg_execute_payload.go`), which is a gasless message any account may submit, and is driven by the `RescueFundsOnSourceChain` event emitted from `UniversalGatewayPC` when a user calls the rescue entry point on the source chain / via their UEA. No admin privilege is required to trigger a rescue attempt on an eligible, already-stuck UTX.

The consequence of skipping the enabled check: an `OutboundTx` with `TxType_RESCUE_FUNDS`, `Status_PENDING`, is attached to the UTX and indexed in `PendingOutbounds` even when the destination (source) chain's outbound flag is disabled. On the off-chain side, Universal Validators' `EventProcessor` explicitly refuses to process outbound events for disabled chains: [4](#0-3) 

So the rescue outbound can never be signed/broadcast by honest, unprivileged-assumption UVs while the chain remains outbound-disabled. Worse, the duplicate-rescue guard in `AttachRescueOutboundFromReceipt` blocks any further rescue attempt for that UTX while a `PENDING`/`OBSERVED` rescue outbound exists: [5](#0-4) 

This means the user's already-stuck funds (the very funds the rescue mechanism exists to recover) become permanently unrecoverable through the on-chain escape hatch: a PENDING rescue outbound sits forever unfulfilled, and no retry is possible because the guard treats the stale PENDING entry as "already has an active rescue outbound."

### Impact Explanation
This is a state-invariant violation reachable by an ordinary unprivileged user (the rescue is initiated by the affected user themselves, not an admin), causing permanent freezing of that user's funds — one of the explicitly in-scope impacts. It also creates a divergence between what the executor module records as canonical UTX/outbound state (a "PENDING" rescue outbound) and what is operationally achievable (the outbound can never be observed while the chain stays disabled), corrupting the meaning of `PendingOutbounds`/`OutboundTx.outbound_status` for that UTX.

### Likelihood Explanation
Moderate-to-high: outbound-disabling is an operator action taken in response to source-chain risk (compromised bridge, contract issue, chain halt) — exactly the scenario in which stuck funds and rescue attempts are most likely to occur. Any user whose CEA deposit failed or whose auto-revert reverted on such a disabled chain will hit this path when trying to use the rescue mechanism, permanently losing their one retry.

### Recommendation
Add the same `uregistryKeeper.IsChainOutboundEnabled(ctx, originalUtx.InboundTx.SourceChain)` check inside `AttachRescueOutboundFromReceipt` before attaching the `RESCUE_FUNDS` outbound (mirroring `BuildOutboundsFromReceipt`), and fail the rescue attempt (without consuming the "one active rescue" slot) when the destination chain's outbound is disabled, so the user can retry once outbound is re-enabled.

### Proof of Concept
1. Admin/registry sets `ChainConfig.Enabled.IsOutboundEnabled = false` for a chain (via `IsChainOutboundEnabled`, verified disabled by `TestIsChainOutboundEnabled` / `chain_enabled_test.go` semantics).
2. A user's CEA inbound on that chain fails (`PcTx[0].Status == "FAILED"`), or their non-CEA inbound's `INBOUND_REVERT` outbound reaches `REVERTED` — both are normal, unprivileged failure conditions reachable via existing tests, e.g. `TestRescueFunds` setup in `test/integration/uexecutor/rescue_funds_test.go`.
3. User triggers the rescue path (`RescueFundsOnSourceChain` event via `MsgExecutePayload`); `AttachRescueOutboundFromReceipt` runs and, since it never calls `IsChainOutboundEnabled`, successfully attaches a `PENDING` `RESCUE_FUNDS` outbound and indexes it in `PendingOutbounds`, exactly as shown in the passing `TestRescueFunds` test flow — `AttachRescueOutboundFromReceipt` succeeds with no outbound-enabled precondition anywhere in the call chain.
4. Because `event_processor.go` skips processing outbound events for `outboundEnabled == false` chains, no Universal Validator ever votes to move this rescue outbound out of `PENDING`.
5. Any subsequent rescue attempt for the same UTX is rejected by the "already has an active rescue outbound" guard, permanently freezing the funds until manual/governance intervention rewrites state — an outcome the normal (`BuildOutboundsFromReceipt`) path would have prevented up front by rejecting the outbound creation instead of leaving it stuck.

### Citations

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

**File:** x/uexecutor/keeper/create_outbound.go (L193-337)
```go
func (k Keeper) AttachRescueOutboundFromReceipt(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	pcTx types.PCTx,
) error {
	universalGatewayPC := strings.ToLower(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address)

	evmChainID, err := utils.ExtractEvmChainID(ctx.ChainID())
	if err != nil {
		return fmt.Errorf("rescue: failed to extract EVM chain ID: %w", err)
	}
	pushChainCaip := fmt.Sprintf("eip155:%s", evmChainID)

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
		if strings.ToLower(lg.Topics[0]) != strings.ToLower(types.RescueFundsOnSourceChainEventSig) {
			continue
		}

		event, err := types.DecodeRescueFundsOnSourceChainFromLog(lg)
		if err != nil {
			return fmt.Errorf("failed to decode RescueFundsOnSourceChain: %w", err)
		}

		// The universalTxId in the event is a 0x-prefixed bytes32 matching our UTX key.
		originalUtxId := strings.TrimPrefix(event.UniversalTxId, "0x")

		originalUtx, found, err := k.GetUniversalTx(ctx, originalUtxId)
		if err != nil {
			return fmt.Errorf("rescue: failed to fetch UTX %s: %w", originalUtxId, err)
		}
		if !found {
			return fmt.Errorf("rescue: original UTX %s not found", originalUtxId)
		}
		if originalUtx.InboundTx == nil {
			return fmt.Errorf("rescue: UTX %s has no inbound tx", originalUtxId)
		}

		// Rescue eligibility differs by inbound type:
		//
		//  CEA inbounds: the deposit (first PCTx) must have failed, meaning the funds
		//  never arrived on Push Chain and are still locked on the source chain.
		//
		//  Non-CEA inbounds: the auto-generated INBOUND_REVERT outbound must exist and
		//  have reached REVERTED status, meaning TSS could not return the funds to the
		//  source chain and they are stuck (held by the gateway contract or in escrow).
		if originalUtx.InboundTx.IsCEA {
			if len(originalUtx.PcTx) == 0 || originalUtx.PcTx[0] == nil || originalUtx.PcTx[0].Status != "FAILED" {
				return fmt.Errorf("rescue: UTX %s CEA deposit did not fail", originalUtxId)
			}
		} else {
			hasRevertedAutoRevert := false
			for _, ob := range originalUtx.OutboundTx {
				if ob != nil && ob.TxType == types.TxType_INBOUND_REVERT && ob.OutboundStatus == types.Status_REVERTED {
					hasRevertedAutoRevert = true
					break
				}
			}
			if !hasRevertedAutoRevert {
				return fmt.Errorf("rescue: UTX %s has no reverted inbound-revert outbound", originalUtxId)
			}
		}

		k.Logger().Info("rescue outbound detected",
			"original_utx_id", originalUtxId,
			"pc_tx_hash", receipt.Hash,
		)

		// Guard against duplicate rescue outbounds: reject if an active rescue
		// (PENDING or OBSERVED) already exists. A REVERTED rescue may be retried.
		for _, ob := range originalUtx.OutboundTx {
			if ob == nil || ob.TxType != types.TxType_RESCUE_FUNDS {
				continue
			}
			if ob.OutboundStatus == types.Status_PENDING || ob.OutboundStatus == types.Status_OBSERVED {
				k.Logger().Warn("rescue outbound rejected: active rescue already exists",
					"original_utx_id", originalUtxId,
					"existing_outbound_id", ob.Id,
				)
				return fmt.Errorf("rescue: UTX %s already has an active rescue outbound (%s)", originalUtxId, ob.Id)
			}
		}

		// Resolve external asset address from PRC20 → token config for the source chain.
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			originalUtx.InboundTx.SourceChain,
			event.PRC20,
		)
		if err != nil {
			return fmt.Errorf("rescue: token config not found for PRC20 %s on %s: %w",
				event.PRC20, originalUtx.InboundTx.SourceChain, err)
		}

		// Rescued funds go to the original revert recipient (or the sender as fallback).
		recipient := originalUtx.InboundTx.Sender
		if originalUtx.InboundTx.RevertInstructions != nil &&
			originalUtx.InboundTx.RevertInstructions.FundRecipient != "" {
			recipient = originalUtx.InboundTx.RevertInstructions.FundRecipient
		}

		logIndex := fmt.Sprintf("%d", lg.Index)
		outbound := &types.OutboundTx{
			Id:                types.GetRescueFundsOutboundId(pushChainCaip, receipt.Hash, logIndex),
			DestinationChain:  originalUtx.InboundTx.SourceChain,
			Recipient:         recipient,
			Amount:            originalUtx.InboundTx.Amount,
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.PRC20,
			Sender:            event.Sender,
			GasFee:            event.GasFee.String(),
			GasPrice:          event.GasPrice.String(),
			GasLimit:          event.GasLimit.String(),
			TxType:            types.TxType_RESCUE_FUNDS,
			OutboundStatus:    types.Status_PENDING,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: logIndex,
			},
		}

		// Record the rescue call as a PCTx on the original UTX so the full
		// PC-side history is visible (deposit FAILED → rescue call → outbound).
		if err := k.UpdateUniversalTx(ctx, originalUtxId, func(utx *types.UniversalTx) error {
			utx.PcTx = append(utx.PcTx, &pcTx)
			return nil
		}); err != nil {
			return fmt.Errorf("rescue: failed to record PCTx on UTX %s: %w", originalUtxId, err)
		}

		if err := k.attachOutboundsToUtx(ctx, originalUtxId, []*types.OutboundTx{outbound}, ""); err != nil {
			return fmt.Errorf("rescue: failed to attach outbound to UTX %s: %w", originalUtxId, err)
		}
	}

	return nil
}
```

**File:** universalClient/chains/common/event_processor.go (L134-138)
```go
		} else if event.Type == store.EventTypeOutbound {
			if !ep.outboundEnabled {
				ep.logger.Warn().Str("event_id", event.EventID).Msg("outbound disabled, skipping outbound event processing")
				continue
			}
```
