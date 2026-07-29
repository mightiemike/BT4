### Title
Outbound-disabled destination chain permanently strands PRC20 funds already burned in `sendUniversalTxOutbound` on Push Chain - (File: `x/uexecutor/keeper/create_outbound.go`, `x/uexecutor/keeper/evm_hooks.go`)

### Summary
This is a direct structural analog of the SKALE `TokenManagerERC20` bug: value is committed/locked on one side of a bridge before the destination-side "is this route allowed" check runs, and a negative check result leaves the committed value stranded with no compensating revert path.

### Finding Description
When a user calls the `UniversalGatewayPC` EVM contract's outbound-withdraw function, the PRC20 is burned/escrowed on Push Chain and a `UniversalTxOutboundEvent` log is emitted as part of that same, already-committed EVM transaction. `EVMHooks.PostTxProcessing` [1](#0-0)  runs *after* the transaction has executed, and calls `CreateUniversalTxFromReceiptIfOutbound` → `BuildOutboundsFromReceipt` [2](#0-1)  to decide whether to actually create the `UniversalTx`/`OutboundTx` record that will drive delivery to the destination chain.

Inside `BuildOutboundsFromReceipt`, the destination chain's `IsChainOutboundEnabled` flag is checked only at this post-hoc stage:
```go
outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
...
if !outboundEnabled {
    return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
}
``` [3](#0-2) 

If the flag is `false`, the function returns an error and **no `OutboundTx`/`UniversalTx` is ever created**. This error is confirmed by the project's own integration test to propagate straight up from `PostTxProcessing`: [4](#0-3) . Unlike every other failure path in this module (missing token config, zero amount, failed swap, etc.), which explicitly builds an `INBOUND_REVERT`/gas-refund via `handleFailedInboundValidation` / `buildRevertOutbound` [5](#0-4) , the outbound-disabled branch in `BuildOutboundsFromReceipt` performs **no compensating action at all** — it just bubbles an error with no UTX record and no re-mint/refund logic on the Push Chain side.

This is the same fact pattern as the SKALE finding: the source-side burn/lock of value is irreversible-by-design (the `UniversalGatewayPC` EVM tx already committed and emitted the log), while the destination-side "is this route permitted" gate is evaluated afterward, and a negative result has no defined recovery.

### Impact Explanation
If the admin-controlled `IsOutboundEnabled` flag for a chain is `false` (or is flipped to `false` between when a user submits the transaction and when it's processed — race condition), any unprivileged user's `sendUniversalTxOutbound` call that already burned/locked their PRC20 on Push Chain results in: (a) tokens gone from the user's balance on Push Chain, (b) no `OutboundTx` created to deliver funds to the destination chain, and (c) no automatic re-mint/refund path, since the "disabled" branch returns bare `nil, err` with none of the revert/rescue machinery invoked. This matches the "permanent freezing of user or protocol-controlled funds" impact category. There is a manual `RESCUE_FUNDS`/`AttachRescueOutboundFromReceipt` path, but it is gated on specific pre-existing UTX states (`IsCEA` deposit `FAILED`, or an existing `REVERTED` `INBOUND_REVERT` outbound) [6](#0-5)  — none of which exist here because no `UniversalTx` was ever created for this PC-originated withdrawal in the first place, so the rescue path cannot key off it.

### Likelihood Explanation
This is contingent on chain configuration (an admin having set/left `IsOutboundEnabled=false` for a given destination chain, or racing a disable-toggle against in-flight user transactions), matching the "configuration and admin privilege"-influenced medium-severity classification the original SKALE finding received. The trigger itself (a user calling the gateway's outbound withdraw function) is fully unprivileged and requires no validator/relayer/admin collusion — it's a straightforward chain-registry-state × ordinary-user-action interaction, which is why it's in scope under "corruption of ... refund accounting ... reachable from ordinary user deposits/payloads/contracts."

### Recommendation
1. Gate the source-side EVM call in `UniversalGatewayPC`'s outbound/withdraw function itself so the burn/escrow of the PRC20 can never occur for a chain with `IsOutboundEnabled=false` — i.e., push the "is destination allowed" check to *before* the value-affecting state change, not after.
2. If that check must remain in the Cosmos hook, add an explicit compensating branch in `BuildOutboundsFromReceipt`'s `!outboundEnabled` case that re-mints/returns the burned PRC20 to the sender (mirroring `handleFailedInboundValidation`'s revert-outbound construction) instead of just returning an error.
3. Consider caching/mirroring the `IsOutboundEnabled` flag on the EVM-side registry so the gateway contract's own check and the Cosmos-side check can never diverge, closing the toggle-race window as well.

### Proof of Concept
The project's own test demonstrates the vulnerable state transition (an `UniversalTxOutboundEvent` log for a disabled destination chain is dropped with only an error, no UTX/outbound/refund created): [4](#0-3) . Concretely:
1. Admin sets `ChainConfig{Chain: "eip155:11155111", Enabled: {IsOutboundEnabled: false}}`.
2. A user calls `sendUniversalTxOutbound` on `UniversalGatewayPC`, which burns/escrows their PRC20 and emits `UniversalTxOutboundEvent` — this EVM tx commits successfully regardless of the flag.
3. `EVMHooks.PostTxProcessing` → `BuildOutboundsFromReceipt` sees `outboundEnabled == false` and returns `"outbound is disabled for chain %s"`, with no `OutboundTx`/`UniversalTx` created and no PRC20 re-minted back to the user.
4. The user's PRC20 is now unrecoverable through the module's normal flow, since no UTX exists for the `RESCUE_FUNDS` path to key off.

Note: I could not fully verify (index limitations) whether the `PostTxProcessing` error is treated as fatal by the surrounding cosmos-evm `ApplyTransaction`/hook-dispatch machinery in a way that would revert the whole EVM transaction (which would neutralize the bug) — that wiring lives in the forked `cosmos-evm` dependency, not in this repo's indexed files. If hook errors do cause the encompassing EVM tx to revert, then the burn itself would roll back and this would not be exploitable; the test only exercises `EVMHooks.PostTxProcessing` in isolation and confirms an error is returned, not the end-to-end transaction-commit behavior. I recommend a Devin session with full repository/build access (including the `github.com/pushchain/evm` fork) to confirm how a non-nil `PostTxProcessing` error affects the enclosing `MsgEthereumTx`'s state commitment before treating this as a confirmed, exploitable finding.

### Citations

**File:** x/uexecutor/keeper/evm_hooks.go (L25-67)
```go
// PostTxProcessing is called by the EVM module after transaction execution.
// It inspects the receipt and creates UniversalTx + Outbound only if
// UniversalTxWithdraw event is detected.
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
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L16-57)
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

**File:** test/integration/uexecutor/evm_hooks_and_outbound_test.go (L576-627)
```go
	t.Run("outbound disabled returns error", func(t *testing.T) {
		chainApp, ctx, _ := utils.SetAppWithValidators(t)

		destChain := "eip155:11155111"
		chainConfig := uregistrytypes.ChainConfig{
			Chain:          destChain,
			VmType:         uregistrytypes.VmType_EVM,
			PublicRpcUrl:   "https://sepolia.drpc.org",
			GatewayAddress: "0x28E0F09bE2321c1420Dc60Ee146aACbD68B335Fe",
			Enabled: &uregistrytypes.ChainEnabled{
				IsInboundEnabled:  true,
				IsOutboundEnabled: false,
			},
		}
		require.NoError(t, chainApp.UregistryKeeper.AddChainConfig(ctx, &chainConfig))

		gatewayAddr := uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address
		eventSigHash := common.HexToHash(uexecutortypes.UniversalTxOutboundEventSig)
		txIdHash := common.HexToHash("0x0000000000000000000000000000000000000000000000000000000000000002")
		senderHash := common.HexToHash("0x000000000000000000000000" + utils.GetDefaultAddresses().DefaultTestAddr[2:])
		prc20Addr := utils.GetDefaultAddresses().PRC20USDCAddr
		tokenHash := common.HexToHash("0x000000000000000000000000" + prc20Addr.Hex()[2:])
		recipient := common.HexToAddress("0x527f3692f5c53cfa83f7689885995606f93b6164")

		data, err := encodeUniversalTxOutboundData(
			destChain, recipient.Bytes(), big.NewInt(500000),
			common.Address{}, big.NewInt(111), big.NewInt(21000),
			[]byte{}, big.NewInt(0),
			common.HexToAddress(utils.GetDefaultAddresses().DefaultTestAddr),
			2, big.NewInt(1000000000),
		)
		require.NoError(t, err)

		evmLog := &ethtypes.Log{
			Address: common.HexToAddress(gatewayAddr),
			Topics:  []common.Hash{eventSigHash, txIdHash, senderHash, tokenHash},
			Data:    data,
			Removed: false,
		}
		receipt := &ethtypes.Receipt{
			TxHash:  common.HexToHash("0xsynth002"),
			GasUsed: 50000,
			Logs:    []*ethtypes.Log{evmLog},
		}

		sender := common.HexToAddress(utils.GetDefaultAddresses().DefaultTestAddr)
		hooks := uexecutorkeeper.NewEVMHooks(chainApp.UexecutorKeeper)

		err = hooks.PostTxProcessing(ctx, sender, core.Message{}, receipt)
		require.Error(t, err)
		require.Contains(t, err.Error(), "outbound is disabled")
	})
```

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L1-68)
```go
package keeper

import (
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/pushchain/push-chain-node/x/uexecutor/types"
)

// handleFailedInboundValidation records a failed PCTx on the UTX and, for non-isCEA
// inbounds, schedules an INBOUND_REVERT outbound so the user's funds can be returned
// on the source chain. This is called when ValidateForExecution fails after the ballot
// has already been finalized and the UTX created.
func (k Keeper) handleFailedInboundValidation(sdkCtx sdk.Context, utx types.UniversalTx, validationErr error) error {
	inbound := utx.InboundTx
	_, ueModuleAddressStr := k.GetUeModuleAddress(sdkCtx)
	universalTxKey := utx.Id

	k.Logger().Warn("inbound validation failed",
		"utx_key", universalTxKey,
		"source_chain", inbound.SourceChain,
		"is_cea", inbound.IsCEA,
		"error", validationErr.Error(),
	)

	// Record the failed PCTx
	failedPcTx := types.PCTx{
		Sender:      ueModuleAddressStr,
		BlockHeight: uint64(sdkCtx.BlockHeight()),
		Status:      "FAILED",
		ErrorMsg:    validationErr.Error(),
	}

	if err := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(utx *types.UniversalTx) error {
		utx.PcTx = append(utx.PcTx, &failedPcTx)
		return nil
	}); err != nil {
		return err
	}

	// For non-isCEA inbounds, schedule a revert outbound to return funds on source chain.
	// isCEA failures never create an INBOUND_REVERT outbound (consistent with execute_inbound_funds_and_payload.go).
	if !inbound.IsCEA {
		k.Logger().Info("scheduling inbound revert outbound",
			"utx_key", universalTxKey,
			"source_chain", inbound.SourceChain,
			"amount", inbound.Amount,
		)
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			validationErr.Error(),
		); attachErr != nil {
			// Store the revert failure reason on the UTX so it's queryable on-chain.
			// The FAILED PCTx is already recorded above — this adds why the revert wasn't attached.
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(utx *types.UniversalTx) error {
				utx.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				// UpdateUniversalTx only fails on infra issues — return to roll back and retry
				return storeErr
			}
		}
	}

	return nil
}
```
