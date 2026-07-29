Confirmed: `EVMHooks.PostTxProcessing` in `x/uexecutor/keeper/evm_hooks.go` runs after **every** EVM transaction on Push Chain, and unconditionally calls `AttachRescueOutboundFromReceipt` on the receipt [1](#0-0) . The only gate in `AttachRescueOutboundFromReceipt` is that the log's contract address matches `UNIVERSAL_GATEWAY_PC` and the topic matches the `RescueFundsOnSourceChain` signature [2](#0-1) ; there is no check that `msg.sender` of the tx equals the original UTX's owner, and the `event.PRC20` field used to resolve `tokenCfg` is fully attacker-supplied (an indexed topic in the emitted event) rather than being cross-checked against the PRC20 originally associated with the stuck UTX [3](#0-2) .

### Title
Unauthenticated `RescueFundsOnSourceChain.PRC20` field lets any user attach a mismatched-asset rescue outbound to someone else's stuck UTX - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
This is the closest in-scope analog to "Governor can rug pull the escrow": instead of a privileged governor approving an arbitrary spender, an **unprivileged** Push Chain user can call the `UniversalGatewayPC` contract to emit a `RescueFundsOnSourceChain` event carrying an attacker-chosen `universalTxId` and `prc20` address. The node's post-tx hook, `EVMHooks.PostTxProcessing`, automatically consumes this event for *any* transaction and attaches a `RESCUE_FUNDS` outbound whose `Prc20AssetAddr`/`ExternalAssetAddr` are derived from the attacker-controlled `event.PRC20`, while `Amount` and `DestinationChain` are taken from the original (unrelated) UTX. This asset/amount mismatch is then signed and broadcast by TSS as a legitimate outbound instruction.

### Finding Description
`AttachRescueOutboundFromReceipt` is invoked by `EVMHooks.PostTxProcessing` after every EVM transaction, with no restriction on which account submitted the transaction [4](#0-3) . It scans receipt logs for a `RescueFundsOnSourceChain` event emitted from the `UNIVERSAL_GATEWAY_PC` system contract address [5](#0-4) .

The decoded event fields are: `UniversalTxId` (topic, fully attacker-chosen), `PRC20` (topic, fully attacker-chosen), `Sender`, `TxType`, `GasFee`, `GasPrice`, `GasLimit` [6](#0-5) . The keeper looks up the *original* UTX purely by `UniversalTxId` and checks only that it exists and is in an eligible "stuck" state (failed CEA deposit, or reverted auto-revert) [7](#0-6) . It never verifies that the caller of the rescue function is the original inbound's sender/owner, nor that `event.PRC20` matches the PRC20 that was actually associated with that original inbound's `AssetAddr`.

The resulting outbound is built with `Prc20AssetAddr: event.PRC20` and `ExternalAssetAddr: tokenCfg.Address` (both driven by the attacker-supplied PRC20), while `Amount: originalUtx.InboundTx.Amount` and `DestinationChain: originalUtx.InboundTx.SourceChain` are carried over unmodified from the victim's original, unrelated inbound [8](#0-7) . If token decimals/denominations differ between the original asset and the attacker-chosen PRC20 (both valid token configs for the same source chain), the outbound instructs TSS to release the wrong asset/amount pairing on the destination chain — decoupling the amount that was actually verified as "stuck" from the asset that gets released.

### Impact Explanation
This breaks the registry/accounting invariant described in the pivots ("token mapping ... must not misroute value or attach the wrong asset semantics"). An attacker can force creation of a `RESCUE_FUNDS` outbound that mismatches asset and amount for any UTX currently eligible for rescue (any user's stuck inbound), causing TSS to sign and broadcast a release instruction denominated in units of a different, unrelated PRC20/native asset than what was actually locked. Depending on decimal/value differences between the swapped-in PRC20 and the correct one, this can result in over-release of a different token pool held by the source-chain gateway (fund drain) or corrupted outbound state requiring manual intervention (`ABORTED`). Because the recipient field is still pinned to the legitimate `RevertInstructions.FundRecipient`/sender, the attacker cannot redirect funds to themselves, but they can trigger unauthorized asset/amount-mismatched releases against any other user's pending rescue-eligible UTX, and can grief legitimate rescues by attaching a bogus PENDING/OBSERVED rescue outbound first (the "second rescue rejected while first is PENDING/OBSERVED" guard means an attacker's malformed rescue blocks the legitimate one until it resolves) [9](#0-8) .

### Likelihood Explanation
Likelihood depends on whether the on-chain `UniversalGatewayPC.rescueFundsOnSourceChain`-style entrypoint (defined in the separate `push-chain-core-contracts` Solidity repo, not present here) restricts the caller to the original UTX's sender/owner. That contract source is not part of this repository and could not be verified in this session — this is a real gap in my analysis. If that Solidity function is permissionless (any address can call it supplying an arbitrary `universalTxId`/`prc20`), the node-side keeper code shown here has no secondary defense and the issue is directly and cheaply exploitable by any unprivileged user against any eligible stuck UTX. If the contract already restricts the caller/PRC20 to match the original inbound, this reduces to a defense-in-depth gap rather than an exploitable vulnerability.

### Recommendation
In `AttachRescueOutboundFromReceipt`, cross-validate `event.PRC20` against the PRC20 that was actually associated with `originalUtx.InboundTx.AssetAddr` (via `uregistryKeeper.GetTokenConfig`) rather than trusting the attacker-suppliable topic value directly for outbound asset resolution. Additionally verify (or require the on-chain contract to enforce and the node to double check) that the transaction sender is authorized for that UTX (original sender or a permissioned relayer), closing the gap between an unauthenticated on-chain event field and privileged accounting fields (`Amount`, `DestinationChain`) pulled from a different, already-verified source.

### Proof of Concept
1. Push Chain has an existing UTX `U` (belonging to victim) in a rescue-eligible state (CEA deposit `FAILED`, or non-CEA `INBOUND_REVERT` `REVERTED`), with `AssetAddr` = PRC20_A (e.g., 6-decimal USDC-equivalent) and `Amount` = `1_000_000`.
2. Attacker calls the `UniversalGatewayPC` rescue entrypoint on Push Chain from their own account with `universalTxId = U`, but supplies `prc20 = PRC20_B` (an unrelated, valid PRC20 config on the same source chain, e.g. an 8-decimal WBTC-equivalent).
3. `EVMHooks.PostTxProcessing` fires after the attacker's transaction, decodes the `RescueFundsOnSourceChain` log, and calls `AttachRescueOutboundFromReceipt` [1](#0-0) .
4. Because `U` satisfies the eligibility check regardless of who called the rescue function, a `RESCUE_FUNDS` outbound is attached with `Prc20AssetAddr/ExternalAssetAddr = PRC20_B`'s external address but `Amount = 1_000_000` (originally denominated for PRC20_A) [10](#0-9) .
5. TSS observes this `PENDING` outbound and eventually signs/broadcasts a release of `1_000_000` units of PRC20_B's external asset on the source chain — a value/asset mismatch relative to what was actually verified as stuck, and blocks the victim's legitimate rescue attempt in the meantime via the "active rescue outbound" guard.

### Citations

**File:** x/uexecutor/keeper/evm_hooks.go (L28-66)
```go
func (h EVMHooks) PostTxProcessing(
	ctx sdk.Context,
	sender common.Address,
	msg core.Message,
	receipt *ethtypes.Receipt,
) error {
	if receipt == nil || len(receipt.Logs) == 0 {
		return nil
	}

	h.k.Logger().Debug("evm hook post-tx processing",
		"tx_hash", receipt.TxHash.Hex(),
		"sender", sender.Hex(),
		"log_count", len(receipt.Logs),
		"gas_used", receipt.GasUsed,
	)

	protoReceipt := &evmtypes.MsgEthereumTxResponse{
		Hash:    receipt.TxHash.Hex(),
		GasUsed: receipt.GasUsed,
		Logs:    convertReceiptLogs(receipt.Logs),
	}

	// Build pcTx representation
	pcTx := types.PCTx{
		Sender:      sender.Hex(),
		TxHash:      protoReceipt.Hash,
		GasUsed:     protoReceipt.GasUsed,
		BlockHeight: uint64(ctx.BlockHeight()),
		Status:      "SUCCESS",
	}

	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}

	// Handle rescue outbounds (RescueFundsOnSourceChain events → attach to original UTX).
	return h.k.AttachRescueOutboundFromReceipt(ctx, protoReceipt, pcTx)
```

**File:** x/uexecutor/keeper/create_outbound.go (L198-222)
```go
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L225-262)
```go
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L269-282)
```go
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L284-320)
```go
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
```

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L101-112)
```go
// RescueFundsOnSourceChainEvent holds decoded data from the RescueFundsOnSourceChain
// event emitted by UniversalGatewayPC when a user initiates a rescue on the source chain.
type RescueFundsOnSourceChainEvent struct {
	UniversalTxId  string   // 0x-prefixed bytes32 — the original UTX whose funds are stuck
	PRC20          string   // 0x-prefixed address — PRC20 token whose counterpart is locked
	ChainNamespace string   // source chain namespace (e.g. "eip155")
	Sender         string   // 0x-prefixed address — user who initiated the rescue
	TxType         TxType   // always TxType_RESCUE_FUNDS
	GasFee         *big.Int // gas fee charged (in gas-token units)
	GasPrice       *big.Int // gas price on the source chain
	GasLimit       *big.Int // gas limit used for the rescue execution
}
```
