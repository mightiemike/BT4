### Title
Unauthorized cross-user triggering of RESCUE_FUNDS outbounds with attacker-controlled token binding - (`x/uexecutor/keeper/create_outbound.go`)

### Summary
`AttachRescueOutboundFromReceipt` accepts any `RescueFundsOnSourceChain` event emitted by `UNIVERSAL_GATEWAY_PC` and attaches a `RESCUE_FUNDS` outbound to the UTX identified by the event's `UniversalTxId`, without ever checking that `event.Sender` corresponds to the original inbound sender/owner of that UTX.

### Finding Description
In [1](#0-0) , the function decodes the event and immediately loads `originalUtx` purely from `event.UniversalTxId` — an attacker-supplied, publicly-known value (UTX ids are derived deterministically from prior on-chain PC/receipt data, not secret). There is no comparison of `event.Sender` against `originalUtx.InboundTx.Sender` or any owner/authorization check binding the caller to the UTX before proceeding.

The only gating logic is *eligibility of the UTX itself* (CEA deposit `FAILED`, or non-CEA `INBOUND_REVERT` reverted) at [2](#0-1) , and a duplicate-active-rescue guard at [3](#0-2) . Neither check restricts *who* may call rescue for a given UTX.

Critically, the resulting outbound mixes attacker-controlled and victim-bound fields: the amount is taken from the original UTX (`originalUtx.InboundTx.Amount`), but the token/asset binding is derived from the attacker-supplied `event.PRC20`: [4](#0-3) 

Since `event.PRC20` only needs to resolve to *some* valid PRC20 registered for `originalUtx.InboundTx.SourceChain` (not necessarily the token that was actually deposited/stuck for that UTX), an unprivileged caller can force a `RESCUE_FUNDS` outbound that pairs the original deposit's raw `Amount` with an unrelated token's `ExternalAssetAddr`. This corrupts the canonical `UniversalTx`/outbound state that honest UVs/TSS subsequently sign and execute against, i.e., token-mapping/asset-accounting corruption in a user-reachable flow, which is explicitly an in-scope impact category (PRC20/native asset accounting and token mapping corruption).

### Impact Explanation
An unprivileged actor who merely knows a victim's eligible (already-failed/-reverted) `UniversalTxId` can:
- Force-attach a rescue outbound to that UTX at a time of their choosing (griefing/ordering control over stuck funds), and
- Manipulate which PRC20/external asset is bound to the outbound while the amount remains bound to the original deposit, producing a mismatched (amount, asset) pair in accepted canonical outbound state that downstream TSS signing/execution relies on.

This does not let the attacker redirect funds to themselves (recipient is always `originalUtx.InboundTx.Sender` or its configured `RevertInstructions.FundRecipient`), but it does let them corrupt the recorded outbound's token semantics for someone else's UTX, which can cause incorrect asset release amounts/types to be signed by TSS against the wrong token's escrow — an accounting/state-machine correctness violation reachable purely through ordinary user-facing rescue submission.

### Likelihood Explanation
Any address can call the gateway's rescue path with a valid, previously-observed `UniversalTxId` (these values are visible in prior receipts/UTX records) and an arbitrary but registered PRC20 for the correct source chain. No privileged role, validator, or TSS collusion is required — only knowledge of the target's UTX id, which is not secret.

### Recommendation
In `AttachRescueOutboundFromReceipt`, before attaching the outbound:
- Verify `event.Sender` is authorized to trigger a rescue for `originalUtxId` (e.g., equals `originalUtx.InboundTx.Sender`, or its owning UEA, or an explicitly configured rescue-authorized address).
- Verify `event.PRC20` matches the PRC20 actually associated with the original inbound/deposit for that UTX rather than accepting any registered token for the source chain.

### Proof of Concept
1. Observe an existing `UniversalTx` (victim's) that satisfies rescue eligibility: CEA inbound with `PcTx[0].Status == "FAILED"`, or non-CEA inbound with a `REVERTED` `INBOUND_REVERT` outbound — this state is publicly queryable.
2. As an unrelated unprivileged caller, invoke the Push-Chain-side gateway rescue entrypoint that emits `RescueFundsOnSourceChain`, setting `universalTxId = <victim's UTX id>` and `prc20 = <a different, but registered, PRC20 for that source chain>`.
3. `AttachRescueOutboundFromReceipt` in [5](#0-4)  processes the event: it passes eligibility/duplicate checks (based only on the victim UTX's own state), resolves `tokenCfg` from the attacker-chosen `event.PRC20`, and attaches a `RESCUE_FUNDS` outbound to the victim's UTX pairing the victim's original `Amount` with the attacker-chosen asset's `ExternalAssetAddr` — with no check that `event.Sender` is the victim or that `event.PRC20` matches the originally deposited token.

### Citations

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
