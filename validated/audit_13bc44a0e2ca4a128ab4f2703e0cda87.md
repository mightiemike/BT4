## Title
Attacker-Controlled `prc20` Topic in `RescueFundsOnSourceChain` Lets an Unprivileged Caller Bind a Stuck UTX's Locked Amount to an Arbitrary/Mismatched Token Mapping - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
The external report flags rug-pull risk from owner-controlled "rescue" functions that can retrieve arbitrary ERC-20 tokens. The scoped analog in Push Chain is `Keeper.AttachRescueOutboundFromReceipt`, which processes `RescueFundsOnSourceChain` events emitted by `UniversalGatewayPC` and creates a `RESCUE_FUNDS` outbound instructing Universal Validators/TSS to release funds on the source chain. Unlike the reported bug, this path requires no privileged owner role — it is driven by an EVM event whose `prc20` field is an indexed, caller-supplied topic that the keeper trusts without cross-checking it against the original UTX's actual locked asset.

### Finding Description
`AttachRescueOutboundFromReceipt` decodes the `RescueFundsOnSourceChain` log and resolves the token configuration purely from the event's `PRC20` field: [1](#0-0) 

It never validates that `event.PRC20` corresponds to `originalUtx.InboundTx.AssetAddr` — the asset that was actually locked/failed to deposit for that specific UTX. The resulting outbound then combines the amount from the *original* UTX with the asset resolved from the *attacker-supplied* PRC20: [2](#0-1) 

`event.PRC20` and `event.Sender` are decoded straight from indexed log topics that any caller of the `UniversalGatewayPC.rescueFundsOnSourceChain` function chooses when constructing that transaction: [3](#0-2) 

The only gating checks performed are (a) that the UTX exists, and (b) that its deposit failed (CEA) or its auto-revert reverted (non-CEA) — neither of which restricts which token an unprivileged rescue-triggering account may specify: [4](#0-3) 

Because `ExternalAssetAddr` (`tokenCfg.Address`) is derived from the caller-chosen `event.PRC20` rather than from the token actually tied to `originalUtx.InboundTx.AssetAddr`, the numeric `Amount` (denominated in the *original* asset's smallest units) gets reattached to a *different* token's address/config when the outbound instruction is signed and broadcast by TSS/Universal Validators. If two tokens configured in `x/uregistry` have different decimals or market value, the same raw `Amount` figure represents wildly different economic value once bound to the substituted asset.

### Impact Explanation
This corrupts token-mapping and asset-accounting invariants for `RESCUE_FUNDS` outbounds (in scope: "corruption of ... token mapping ... revert instructions must not misroute value or attach the wrong asset semantics"). If the destination (source) chain's vault/gateway contract executes the signed rescue instruction using the `Prc20AssetAddr`/`ExternalAssetAddr` supplied by the outbound rather than independently re-deriving it from the original locked deposit, an attacker can cause TSS-signed release of a token/amount combination that never corresponds to real locked collateral — a fund-misrouting/drain vector reachable by an ordinary, unprivileged user who merely calls the public gateway rescue entry point with a manipulated `prc20` argument.

### Likelihood Explanation
Any account can call the `UniversalGatewayPC` rescue entry point on Push Chain (it is a normal EVM transaction, not privileged), and can freely choose the `prc20` topic value emitted in `RescueFundsOnSourceChain`. The only precondition is that the referenced UTX be in a rescuable state (deposit failed / auto-revert reverted), which is a state attackers can create themselves against their own inbound. No validator collusion, admin key, or governance action is required to reach this code path.

### Recommendation
In `AttachRescueOutboundFromReceipt`, validate that `event.PRC20` matches the PRC20 representation actually associated with `originalUtx.InboundTx.AssetAddr` on `originalUtx.InboundTx.SourceChain` (e.g., resolve the expected PRC20 from the inbound's asset via the registry and reject the rescue if it differs from `event.PRC20`), rather than trusting the caller-supplied token identifier to select the destination-chain asset mapping.

### Proof of Concept
1. Attacker submits a normal cross-chain deposit (CEA inbound) using token A, structured so the Push Chain-side deposit fails (e.g., malformed recipient causing `depositPRC20` to revert), producing a UTX eligible for rescue.
2. Attacker calls `UniversalGatewayPC.rescueFundsOnSourceChain(universalTxId, prc20=<Token B address>, ...)` directly on Push Chain, supplying the real `universalTxId` of their stuck UTX but a different `prc20` (Token B, with higher value or different decimals than Token A).
3. `AttachRescueOutboundFromReceipt` accepts the event, checks only that the original UTX's deposit failed, then resolves `tokenCfg` via `GetTokenConfigByPRC20(sourceChain, TokenB)`, producing an outbound with `Amount` = original Token A units but `ExternalAssetAddr`/`Prc20AssetAddr` bound to Token B.
4. Once Universal Validators reach quorum and TSS signs/broadcasts this outbound, the source-chain vault is instructed to release Token B in the raw amount originally denominated for Token A — a value mismatch attacker-triggered without any privileged role.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L239-262)
```go
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

**File:** x/uexecutor/keeper/create_outbound.go (L284-293)
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L295-320)
```go
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

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L124-136)
```go
func DecodeRescueFundsOnSourceChainFromLog(log *evmtypes.Log) (*RescueFundsOnSourceChainEvent, error) {
	if len(log.Topics) < 4 {
		return nil, fmt.Errorf("RescueFundsOnSourceChain: need 4 topics, got %d", len(log.Topics))
	}
	if strings.ToLower(log.Topics[0]) != strings.ToLower(RescueFundsOnSourceChainEventSig) {
		return nil, fmt.Errorf("not a RescueFundsOnSourceChain event")
	}

	event := &RescueFundsOnSourceChainEvent{
		UniversalTxId: log.Topics[1], // bytes32 as 0x-prefixed hex
		PRC20:         common.HexToAddress(log.Topics[2]).Hex(),
		Sender:        common.HexToAddress(log.Topics[3]).Hex(),
	}
```
