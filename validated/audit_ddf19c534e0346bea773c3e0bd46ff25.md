Based on my investigation, I found a genuine, verified analog in this repository. The critical detail is that `PostTxProcessing` is a standard cosmos-evm `EvmHooks` post-processing hook, invoked *after* the EVM state transition of the underlying transaction has already been applied by the EVM keeper. `EvmHooks` in the `cosmos/evm` framework do not roll back the just-executed EVM transaction on hook error the way the module's own internal `CacheContext` patterns do elsewhere in this codebase (e.g. `execute_inbound_funds_and_payload.go` explicitly wraps EVM call + fee deduction in `sdkCtx.CacheContext()` specifically so a hook-adjacent failure can discard the EVM writes — but `PostTxProcessing` itself has no such wrapping around the *original* user transaction it's inspecting).

### Title
Permanently locked bridged funds when outbound is disabled after a `UniversalTxOutbound` event is emitted - (File: x/uexecutor/keeper/create_outbound.go, x/uexecutor/keeper/evm_hooks.go)

### Summary
`BuildOutboundsFromReceipt` checks `IsChainOutboundEnabled` for the destination chain and returns a hard error if outbound is disabled [1](#0-0) . This is invoked from `EVMHooks.PostTxProcessing`, which runs *after* the originating EVM transaction (which already burned/locked the user's PRC20 in `UniversalGatewayPC` and emitted the `UniversalTxOutbound` event) has been committed [2](#0-1) . Because the hook error does not roll back the already-executed gateway transaction, the user's PRC20 is burned/withdrawn on Push Chain but no `OutboundTx`/`UniversalTx` record is ever created, and no revert or refund path exists for this failure mode.

### Finding Description
This is the same invariant break as the Velodrome bug: an entity is "killed"/disabled (there, a gauge; here, a chain's outbound flag) while value already committed to the flow (there, `claimable[_gauge]`; here, PRC20 burned into the `UniversalGatewayPC` contract) has no path back to the user.

Trace:
1. A user calls the gateway contract on Push Chain (via `MsgExecutePayload`, an inbound-triggered `ExecuteInboundFundsAndPayload`, or any other path that ends in an EVM call to `UniversalGatewayPC`) to withdraw/bridge PRC20 out to a destination chain. The gateway burns/locks the PRC20 and emits `UniversalTxOutbound` — this state change is committed as part of the EVM tx.
2. `EVMHooks.PostTxProcessing` fires post-commit and calls `CreateUniversalTxFromReceiptIfOutbound` → `BuildOutboundsFromReceipt` [3](#0-2) .
3. `BuildOutboundsFromReceipt` checks `IsChainOutboundEnabled(ctx, event.ChainId)` for the destination chain named in the event. If outbound has been disabled for that chain (e.g., by an admin mid-migration, or simply a chain that had outbound turned off between the time the user constructed/queued their tx and when it landed), it returns an error instead of creating the `OutboundTx` [1](#0-0) .
4. `PostTxProcessing` propagates this error [4](#0-3) , confirmed by the integration test `TestPostTxProcessing_WithSyntheticOutboundEvent/"outbound disabled returns error"` [5](#0-4) .
5. Nowhere in this path is the already-burned PRC20 re-minted back to the sender, nor is any `OutboundTx`/`UniversalTx` record created to track the stuck funds — unlike the deposit-failure paths elsewhere in the module (`handleFailedInboundValidation`, `ExecuteInboundGas`, `ExecuteInboundFundsAndPayload`), which explicitly build and attach an `INBOUND_REVERT` outbound whenever a deposit/execution step fails [6](#0-5) .

Compare this with `x/utss`'s `InitiateFundMigration`, which explicitly guards against orphaned funds by requiring outbound to already be disabled *and* no pending outbounds to exist before migrating a chain's TSS key [7](#0-6)  — that check only covers `PendingOutbounds` records that already exist in `x/uexecutor` state; it says nothing about EVM-level gateway burns whose corresponding `UniversalTx`/`OutboundTx` never got created due to the disabled-outbound error in `BuildOutboundsFromReceipt`. There is no rescue path for this class of loss: `AttachRescueOutboundFromReceipt`/rescue-funds flow requires an existing `UniversalTx` record to attach to [8](#0-7) , but here no `UniversalTx` was ever created because `BuildOutboundsFromReceipt` errored out before any UTX construction.

### Impact Explanation
User-owned PRC20 value that was already burned/withdrawn from the gateway is permanently unaccounted for — it is neither delivered to the destination chain (no `OutboundTx` created) nor returned to the user (no revert/rescue path exists, since no `UniversalTx` record exists to hang a rescue outbound off of). This matches the "permanent loss / permanent freezing of user funds" impact category.

### Likelihood Explanation
Requires no privileged action from the attacker/victim's perspective — the ordinary flow is: (1) a chain's outbound gets disabled (an admin/governance action which can legitimately occur for many operational reasons, e.g., pausing a chain during incident response or ahead of a TSS migration) at some point after a user's Push Chain payload/inbound execution has already reached the gateway-burn EVM call but the corresponding `PostTxProcessing` hook check hasn't yet run, or (2) any inbound-triggered outbound creation racing a chain-disable in the same or adjacent block. Because `IsChainOutboundEnabled` is re-checked in `BuildOutboundsFromReceipt` at hook time rather than being consistent with whatever check (if any) gated the original gateway call, an ordinary/unprivileged user transaction that was valid when submitted can land in a state where the burn commits but the outbound/refund never gets created.

### Recommendation
Two changes, mirroring the Velodrome fix's spirit ("return claimable to Minter" + "wipe/refund"):
1. `BuildOutboundsFromReceipt` should not swallow-and-fail without a compensating action: when outbound is disabled for the destination chain at hook time, the keeper should either (a) still create the `UniversalTx`/`OutboundTx` records but hold them in a distinct "blocked/awaiting-outbound-enable" state rather than erroring out entirely, or (b) automatically re-mint the burned PRC20 back to the original sender/`RevertRecipient` in the same hook invocation, recording a `PCTx`/refund entry, so funds are never left in limbo.
2. Ensure `PostTxProcessing`'s error path is only used for cases where the underlying EVM burn can be guaranteed to be rolled back together with it (as is done elsewhere via `CacheContext`), or explicitly document/enforce that `IsChainOutboundEnabled` is checked and enforced *before* the gateway contract executes the burn, not only after, so the two checks cannot diverge.

### Proof of Concept
1. Admin adds `ChainConfig` for `eip155:11155111` with `IsOutboundEnabled: true`.
2. A user's inbound/payload execution triggers an EVM call to `UniversalGatewayPC`, which burns the user's PRC20 and emits `UniversalTxOutbound` targeting `eip155:11155111`. This EVM state change commits as part of the enclosing SDK transaction.
3. Before/concurrently with `PostTxProcessing` running for that receipt, admin flips `IsOutboundEnabled` to `false` for `eip155:11155111` (this only requires the flag to be false at the time `BuildOutboundsFromReceipt` runs, not at the time the gateway call was made).
4. `EVMHooks.PostTxProcessing` → `CreateUniversalTxFromReceiptIfOutbound` → `BuildOutboundsFromReceipt` returns `"outbound is disabled for chain eip155:11155111"` and no `UniversalTx`/`OutboundTx` is created, as shown by the existing test `TestPostTxProcessing_WithSyntheticOutboundEvent/"outbound disabled returns error"` [5](#0-4) .
5. Query `AllUniversalTx` — the burned amount has no corresponding `UniversalTx` record, no `OutboundTx`, and no rescue path is reachable (rescue requires a pre-existing `UniversalTx`). The PRC20 is permanently unaccounted for.

**Note on confidence**: I was not able to fully trace the exact caller sequencing/atomicity guarantees of the underlying `cosmos/evm` `EvmHooks.PostTxProcessing` contract (i.e., whether a hook error can, at the SDK message level, cause the *entire* enclosing Cosmos transaction — including the gateway burn — to roll back, similar to how ante-handler failures work). If `PostTxProcessing` errors do in fact abort/roll back the whole enclosing transaction at the SDK/ante layer, this finding would be moot for the "hook fires post-commit" framing, though the underlying design gap (no compensating refund/rescue path when `BuildOutboundsFromReceipt` errors on disabled outbound) would still stand as a design issue. Confirming this exactly would require deeper inspection of the `cosmos/evm` module's transaction-processing pipeline, which is a dependency outside this repo's own code and wasn't fully indexed in my search results.

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

**File:** x/uexecutor/keeper/create_outbound.go (L225-237)
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
```

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

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L39-65)
```go
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
```

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L31-47)
```go
	// 4. Verify outbound is disabled for this chain
	outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check outbound status for chain %s: %w", chain, err)
	}
	if outboundEnabled {
		return 0, fmt.Errorf("outbound is still enabled for chain %s; disable outbound before initiating migration", chain)
	}

	// 5. Verify no pending outbounds for this chain
	hasPending, err := k.uexecutorKeeper.HasPendingOutboundsForChain(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check pending outbounds for chain %s: %w", chain, err)
	}
	if hasPending {
		return 0, fmt.Errorf("chain %s still has pending outbounds; wait for them to drain before migration", chain)
	}
```
