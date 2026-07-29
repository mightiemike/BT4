## Title
Rescue-outbound path uses an unauthenticated, attacker-suppliable `universalTxId` reference to attach rescue outbounds to arbitrary victim UTXs - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
`AttachRescueOutboundFromReceipt` is the closest structural analog to the reported `ReadProposalFlag` path-traversal bug: both take a caller-influenced "reference" (a filesystem path there, a `universalTxId` here) and use it verbatim to look up and mutate a resource, without verifying that the reference belongs to, or was legitimately produced for, the calling context.

### Finding Description
`AttachRescueOutboundFromReceipt` [1](#0-0)  decodes a `RescueFundsOnSourceChain` event emitted by the `UNIVERSAL_GATEWAY_PC` system contract and takes `event.UniversalTxId` directly as the key to fetch an **existing** `UniversalTx` record via `k.GetUniversalTx(ctx, originalUtxId)` [2](#0-1) . This ID is never re-derived from data the keeper independently controls (e.g. the caller's own inbound identity) — it is trusted verbatim from the EVM log, which in turn is only as trustworthy as whatever value the caller supplied as an argument to the contract's rescue entry point.

The function then gates on state of the *referenced* UTX (CEA-deposit-FAILED, or a REVERTED `INBOUND_REVERT` outbound) [3](#0-2) , and if that state matches, it mutates that UTX by appending a PCTx and a new `RESCUE_FUNDS` outbound whose recipient is taken from the *referenced* UTX's own `RevertInstructions`/`Sender` [4](#0-3) .

This is precisely the class of bug described in the analog report: the code reads/writes a resource identified by an externally supplied "path" (here, a UTX id) without verifying that the invoking party actually owns or is authorized to reference that resource. Whether this is exploitable end-to-end depends on whether the `UniversalGatewayPC.rescueFundsOnSourceChain` (or equivalent) Solidity entry point lets *any* caller pass an arbitrary `universalTxId` argument, or whether it cryptographically/contextually binds the id to `msg.sender`'s own stuck transaction. That Solidity source is not present in this repository (it lives in `push-chain-core-contracts`), so I could not verify from this codebase alone whether the id is caller-supplied or protocol-derived at the EVM layer — this is the same posture the `ReadProposalFlag` report describes: the Go/keeper layer performs no independent verification of the "path" it is handed, and defers the entire access-control decision to a layer whose behavior is not asserted here.

### Impact Explanation
If the EVM-side rescue entry point does not itself bind `universalTxId` to a value the caller cannot forge (e.g. if it is a plain function argument rather than something computed from the caller's own escrowed funds), any user could invoke rescue with a victim's already-`REVERTED`/failed UTX id. The keeper would then unconditionally attach a duplicate or premature `RESCUE_FUNDS` outbound to that victim's UTX — csusing double-processing of a stuck-funds recovery flow the victim did not initiate, additional unauthorized outbound entries in `PendingOutbounds` for UVs to sign/broadcast, and potential redundant fund movement/refund attempts against the victim's UTX (recipient is fixed to the *victim's* `RevertInstructions.FundRecipient`, but the trigger itself, its timing, and its side effects on `PcTx`/`OutboundTx` state are attacker-controlled). This is state corruption / unauthorized state-transition territory (UTX mutation triggered by an unrelated party), matching the "unauthorized module-originated EVM execution / unauthorized state transitions in universal execution flows" impact category.

### Likelihood Explanation
Medium-to-low confidence, matching the report's own "Medium" severity and "Acknowledged" (not obviously exploitable end-to-end) framing. The keeper-side code shows no defense-in-depth: no signer/owner check ties `originalUtx.InboundTx.Sender` (or its EVM-derived address) to `event.Sender`/the actual caller of the rescue function before mutating the referenced UTX. The guard against duplicates (`active rescue already exists`) [5](#0-4)  limits repeat abuse of the exact same UTX, but does not prevent a first unauthorized rescue attachment. Whether this is reachable in practice hinges entirely on the Solidity contract's own access control for `universalTxId`, which is out of this repository's scope and could not be verified here.

### Recommendation
At the keeper layer, add an explicit authorization check binding the rescue trigger to the referenced UTX's own inbound sender/owner (e.g. require `event.Sender` to match `originalUtx.InboundTx.Sender`'s derived EVM address, or require the on-chain call to have originated from the UEA/owner of that UTX) before trusting `event.UniversalTxId` to select which UTX gets mutated — do not rely solely on the EVM contract's own argument validation. This mirrors the general principle in the report: don't trust an externally supplied reference to select a resource without validating the caller's right to act on it.

### Proof of Concept
Not directly demonstrable from this repository alone: reproducing the exploit requires the `UniversalGatewayPC.rescueFundsOnSourceChain` Solidity implementation (in `push-chain-core-contracts`, not present here) to confirm whether `universalTxId` is a free-form caller-supplied argument. Conceptually: Attacker calls the gateway's rescue function with `universalTxId` = a known victim UTX id whose CEA deposit already `FAILED` (or whose `INBOUND_REVERT` outbound already `REVERTED`); the emitted event flows into `AttachRescueOutboundFromReceipt`, which finds the eligibility conditions already satisfied for the victim's UTX and attaches a new `RESCUE_FUNDS` outbound to it, without any check that the attacker's call has any relationship to that specific UTX.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L193-237)
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
```

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
