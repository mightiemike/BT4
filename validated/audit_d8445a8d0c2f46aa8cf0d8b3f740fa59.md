## Title
Rescue-funds outbound trusts the attacker-controlled `event.PRC20` in the on-chain `RescueFundsOnSourceChain` log to select the released asset, without validating it matches the original inbound's deposited asset - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
`AttachRescueOutboundFromReceipt` builds a `RESCUE_FUNDS` outbound whose `Amount` is copied from the *original* stuck inbound (`originalUtx.InboundTx.Amount`), but whose `ExternalAssetAddr`/`Prc20AssetAddr` are derived from `event.PRC20` — a field read straight out of the EVM log emitted by whatever transaction triggered the rescue call — via `GetTokenConfigByPRC20`. There is no check anywhere in this function that `event.PRC20` corresponds to the same asset as `originalUtx.InboundTx.AssetAddr` (the token that was actually deposited/locked on the source chain). This mirrors the reported NFT bug class: a withdrawal/release path resolves "what to hand back" purely by an attacker/caller-suppliable identifier, with no ownership/binding check to what was actually deposited. [1](#0-0) 

### Finding Description
The rescue flow is meant to let stuck funds (deposit failed for CEA, or an INBOUND_REVERT outbound reverted) be manually recovered by calling a UniversalCore/Gateway contract function that emits `RescueFundsOnSourceChainEvent{UniversalTxId, PRC20, Sender, GasFee, GasPrice, GasLimit, ...}`. `AttachRescueOutboundFromReceipt` then:
1. Loads `originalUtx` by `event.UniversalTxId`.
2. Verifies eligibility (deposit failed / revert reverted) — this check is against the *UTX*, not against the token being released.
3. Resolves `tokenCfg` via `k.uregistryKeeper.GetTokenConfigByPRC20(ctx, originalUtx.InboundTx.SourceChain, event.PRC20)` — i.e. it accepts **any PRC20 address registered on that source chain** supplied in the event, not necessarily the one tied to `originalUtx.InboundTx.AssetAddr`.
4. Builds the outbound with `Amount: originalUtx.InboundTx.Amount` (the original numeric amount, denominated in the *original* asset's decimals/semantics) but `ExternalAssetAddr: tokenCfg.Address` and `Prc20AssetAddr: event.PRC20` (whatever asset the caller specified in the rescue call). [2](#0-1) [1](#0-0) 

There is no assertion equivalent to "the NFT withdrawn belongs to this pool" — here, "the PRC20 rescued belongs to this stuck inbound's original asset." If a caller can trigger the rescue-funds contract call with an arbitrary `PRC20` parameter (any token registered for that chain, potentially of far higher value/lower decimals or different real-world value than what was actually locked), the resulting `OutboundTx` will instruct TSS/validators to release `originalUtx.InboundTx.Amount` units of a *different* asset than what was ever deposited for that UTX — a direct asset/amount cross-linking, analogous to withdrawing `nftY` after depositing `nftX`.

### Impact Explanation
If reachable by an unprivileged party (see Likelihood below), this allows minting an outbound instruction that releases an unrelated, attacker-chosen external asset in an amount computed from a different asset's original deposit — resulting in unauthorized release/drain of protocol- or vault-held funds on the destination/source chain once honest validators/TSS sign and broadcast it (the vote/finalization path does not re-derive or cross-check `ExternalAssetAddr` against the original inbound's `AssetAddr`). This falls squarely in the "unauthorized release of protocol-controlled funds" and "corruption of PRC20/native asset accounting, token mapping" impact categories.

### Likelihood Explanation
This is capped by uncertainty I could not resolve with the available tooling: the report indexer/search here does not surface the Solidity source of `UniversalGatewayPC`'s rescue-funds function, so I cannot confirm whether the on-chain function that emits `RescueFundsOnSourceChainEvent` restricts the caller (e.g., `onlyOwner`/governance) or restricts the `PRC20` parameter to match the UTX's original asset. My `grep_search` for `onlyOwner`/`onlyAdmin`/`rescueFunds` access-control patterns in the indexed contract sources returned no matches, meaning either the contract source isn't indexed here or access control exists but wasn't found in the index. **This must be verified in the actual `UniversalGatewayPC`/`UniversalCore` Solidity contract before treating this as an unprivileged-attacker-reachable bug** — if the rescue call is `onlyOwner`/governance-gated, this finding is out of scope per the allowed-impact rules (privileged actor). If it is caller-suppliable (e.g., any address can call rescue for a UTX they don't control, specifying an arbitrary `PRC20`), then it is a genuine unprivileged-trigger vulnerability matching the reported bug class.

### Recommendation
In `AttachRescueOutboundFromReceipt`, validate that `event.PRC20` matches the native PRC20 representation of `originalUtx.InboundTx.AssetAddr` for `originalUtx.InboundTx.SourceChain` (i.e., re-derive the expected PRC20 from the original inbound's token config and reject the event if `event.PRC20` doesn't match), before using it to build the outbound. Additionally, confirm/enforce that the on-chain rescue-funds function itself binds the `PRC20` parameter to the value stored for that `universalTxId` at deposit time (or is restricted to trusted/governance callers), rather than accepting an arbitrary caller-supplied token address.

### Proof of Concept
Not fully constructible from the indexed code alone: exploitation requires calling the on-chain rescue-funds function of `UniversalGatewayPC` with a `universalTxId` of a stuck UTX and an arbitrary `PRC20` value, which then reaches `AttachRescueOutboundFromReceipt`. I could not confirm from available search results whether that Solidity entrypoint gates the caller or the `PRC20` argument — this is the missing link required to convert this into a concrete, unprivileged PoC. A Devin session with full repository/contract access would be needed to inspect the Solidity source for `rescueFunds`/`RescueFundsOnSourceChain` emission and confirm caller/parameter constraints.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L220-262)
```go
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
