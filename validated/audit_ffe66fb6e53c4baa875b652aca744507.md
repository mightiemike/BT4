Confirmed: there is no cross-check anywhere in `AttachRescueOutboundFromReceipt` (or downstream in outbound finalization) that binds the attacker-supplied `event.PRC20` to `originalUtx.InboundTx.AssetAddr`, unlike `buildRevertOutbound`, which derives `ExternalAssetAddr` strictly from `inbound.AssetAddr` [1](#0-0) . This confirms the analog is a genuine, reachable root cause.

### Title
Rescue outbound lets caller redirect vault fund release to an arbitrary registered PRC20 unrelated to the failed inbound's actual locked asset - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`AttachRescueOutboundFromReceipt` builds a `RESCUE_FUNDS` outbound whose `ExternalAssetAddr`/`Prc20AssetAddr` are taken entirely from the attacker-controlled `event.PRC20` field of the `RescueFundsOnSourceChain` log, while `Amount` is copied verbatim from the original failed `UniversalTx`'s inbound record. Nothing ties the rescued token to the token that was actually associated with the failed deposit, so a caller can pin a valuable, unrelated registered token to the rescue outbound while supplying an arbitrary `Amount` from their own cheap/failed inbound, causing the vault to release value it never actually held for that UTX.

### Finding Description
`AttachRescueOutboundFromReceipt` in [2](#0-1)  scans a Push Chain receipt for a `RescueFundsOnSourceChain` event emitted by `UNIVERSAL_GATEWAY_PC` and decodes it via `DecodeRescueFundsOnSourceChainFromLog`, which trusts the log's `prc20` topic verbatim as `event.PRC20` [3](#0-2) .

The function validates only that (a) the original UTX exists, (b) for CEA inbounds the deposit `PcTx[0]` is `FAILED`, or for non-CEA inbounds the auto `INBOUND_REVERT` outbound is `REVERTED`, and (c) no active rescue outbound already exists [4](#0-3) . It never checks that `event.PRC20` corresponds to `originalUtx.InboundTx.AssetAddr` — the token that was actually deposited/locked for that specific UTX.

It then resolves `tokenCfg` purely from the attacker-supplied `event.PRC20` and constructs the outbound using the *original* UTX's `Amount` but the *attacker-chosen* asset:

```go
tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(ctx, originalUtx.InboundTx.SourceChain, event.PRC20)
...
outbound := &types.OutboundTx{
    ...
    Amount:            originalUtx.InboundTx.Amount,   // pinned to the original (possibly worthless) asset's amount
    ExternalAssetAddr: tokenCfg.Address,                // derived from attacker-controlled event.PRC20
    Prc20AssetAddr:    event.PRC20,                     // attacker-controlled
    ...
}
``` [5](#0-4) 

This is the same bug class as the external report's `rescue()`: a fund-recovery code path fails to bind "what gets rescued" to "what was actually locked," letting the caller substitute the vault's real, shared-pool asset for the one legitimately tied to the failed/reverted transaction. In this case the substitution isn't blocked by any address-equality check at all (unlike the pool's `Pool__CannotRescuePoolToken` guard, which at least tries), because the code was never designed to compare `event.PRC20` against `originalUtx.InboundTx.AssetAddr`/its registered PRC20.

By contrast, the sibling function `buildRevertOutbound` for `INBOUND_REVERT` correctly derives `ExternalAssetAddr` from `inbound.AssetAddr` itself [1](#0-0) , confirming the missing binding is a real gap specific to the rescue path rather than a deliberate design choice.

### Impact Explanation
An unprivileged attacker can:
1. Submit/trigger a CEA inbound with `AssetAddr` set to an unregistered/worthless asset and an attacker-chosen `Amount` (e.g., a large integer), causing the PC-side deposit to fail (`PcTx[0].Status == "FAILED"`), making the UTX rescue-eligible per [6](#0-5) .
2. Call the on-chain rescue entry point on `UniversalGatewayPC` (Push Chain contract) specifying that failed UTX's ID and an arbitrary but registered, high-value `prc20` (e.g., a PRC20 backed by a real, valuable pooled asset on the vault).
3. `AttachRescueOutboundFromReceipt` attaches a `RESCUE_FUNDS` outbound with `Amount` from the attacker's cheap failed inbound but `ExternalAssetAddr`/`Prc20AssetAddr` pointing at the valuable token, with `Recipient` defaulting to the attacker's own address (since they are `originalUtx.InboundTx.Sender`) [7](#0-6) .
4. Once this outbound is finalized through the honest-validator TSS/outbound signing flow, the destination-chain vault releases `Amount` units of the attacker-chosen valuable asset — funds that were never associated with, or locked for, that UTX.

This is an unauthorized release of protocol/user-controlled funds from the shared vault, reachable entirely through ordinary unprivileged user actions (submitting a failing inbound plus calling a rescue function), matching the "unauthorized release ... of user or protocol-controlled funds" allowed-impact category.

### Likelihood Explanation
Moderate-to-high: it requires no privileged role, only (a) causing one's own inbound deposit to fail (trivial — send an unregistered/junk `AssetAddr`) and (b) invoking the gateway's rescue function with a chosen `prc20`. No cryptographic break or validator collusion is needed; honest validators/TSS would sign and execute the outbound exactly as constructed because the keeper itself produced the mismatched asset/amount pairing.

### Recommendation
Bind the rescue outbound's asset to the token actually recorded on the original UTX: reject the event if `event.PRC20` (or its resolved `tokenCfg.Address`) does not match `originalUtx.InboundTx.AssetAddr`'s registered PRC20 (mirroring how `buildRevertOutbound` derives the asset directly from `inbound.AssetAddr`), rather than trusting the caller-supplied `event.PRC20` to select an arbitrary registered token.

### Proof of Concept
1. Attacker submits a CEA inbound (`IsCEA=true`, `TxType_FUNDS_AND_PAYLOAD`) with `AssetAddr` = an unregistered/junk address and `Amount` = `"1000000000000000000"` (1e18), `Sender` = attacker. Validators vote it to quorum; PC deposit fails because `GetTokenConfig` finds no config, producing `PcTx[0].Status == "FAILED"` and no revert (per [8](#0-7) ).
2. Attacker calls `rescueFunds(...)` on `UniversalGatewayPC` on Push Chain, emitting `RescueFundsOnSourceChain(universalTxId=<attacker's UTX id>, prc20=<WETH_PRC20>, sender=attacker, ...)` as in the test helper `buildRescueFundsLog` [9](#0-8) .
3. `AttachRescueOutboundFromReceipt` processes the receipt, passes all eligibility checks, resolves `tokenCfg` for `WETH_PRC20`, and attaches an outbound with `Amount="1000000000000000000"`, `ExternalAssetAddr=<WETH source-chain address>`, `Recipient=attacker` [5](#0-4) .
4. Once validators vote this outbound to `OBSERVED`/execution, the vault sends 1 WETH to the attacker despite the attacker never having deposited any WETH.

### Citations

**File:** x/uexecutor/keeper/build_revert_outbound.go (L16-28)
```go
	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
		Amount:            inbound.Amount,
		ExternalAssetAddr: inbound.AssetAddr,
		Sender:            inbound.Sender,
		TxType:            types.TxType_INBOUND_REVERT,
		OutboundStatus:    types.Status_PENDING,
		Id:                types.GetOutboundRevertId(inbound.SourceChain, inbound.TxHash, inbound.LogIndex),
	}

	// Look up the PRC20 address for this external token
	tokenCfg, err := k.uregistryKeeper.GetTokenConfig(sdkCtx, inbound.SourceChain, inbound.AssetAddr)
```

**File:** x/uexecutor/keeper/create_outbound.go (L193-223)
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
```

**File:** x/uexecutor/keeper/create_outbound.go (L239-282)
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L211-214)
```go
	// isCEA failures: record FAILED PCTx but no revert
	if execErr != nil && utx.InboundTx.IsCEA {
		return nil
	}
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L32-72)
```go
func buildRescueFundsLog(
	t *testing.T,
	utxId string, // UTX key (64-char hex, no 0x prefix)
	prc20Addr common.Address,
	senderAddr common.Address,
	chainNamespace string,
	gasFee, gasPrice, gasLimit *big.Int,
) *evmtypes.Log {
	t.Helper()

	stringType, _ := abi.NewType("string", "", nil)
	uint8Type, _ := abi.NewType("uint8", "", nil)
	uint256Type, _ := abi.NewType("uint256", "", nil)

	args := abi.Arguments{
		{Type: stringType},  // chainNamespace
		{Type: uint8Type},   // txType (RESCUE_FUNDS = 4)
		{Type: uint256Type}, // gasFee
		{Type: uint256Type}, // gasPrice
		{Type: uint256Type}, // gasLimit
	}
	data, err := args.Pack(chainNamespace, uint8(4), gasFee, gasPrice, gasLimit)
	require.NoError(t, err)

	// UTX ID is stored as a bytes32 topic: "0x" + the 64-char hex UTX key.
	utxIdTopic := "0x" + utxId

	gwPCAddr := utils.GetDefaultAddresses().UniversalGatewayPCAddr

	return &evmtypes.Log{
		Address: gwPCAddr.Hex(),
		Topics: []string{
			uexecutortypes.RescueFundsOnSourceChainEventSig,
			utxIdTopic,
			common.BytesToHash(prc20Addr.Bytes()).Hex(),  // indexed prc20
			common.BytesToHash(senderAddr.Bytes()).Hex(), // indexed sender
		},
		Data:    data,
		Removed: false,
	}
}
```
