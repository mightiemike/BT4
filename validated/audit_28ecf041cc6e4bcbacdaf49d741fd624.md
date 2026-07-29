## Analysis Result

I found a real analog of the M-5 "revert instead of skip" pattern in Push Chain's `PostTxProcessing` EVM hook / `BuildOutboundsFromReceipt` flow. This is confirmed by `TestPostTxProcessing_WithSyntheticOutboundEvent`'s "outbound disabled returns error" subtest at [1](#0-0) , which shows `hooks.PostTxProcessing` returns a hard error when any single decoded `UniversalTxOutbound` event targets a chain with outbound disabled.

### Title
Single disabled destination chain in a multi-outbound EVM tx reverts UTX/outbound creation for *all* outbounds in that receipt instead of skipping the disabled one - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`BuildOutboundsFromReceipt` iterates every `UniversalTxOutbound` log in a post-execution EVM receipt and, on encountering the first log whose destination chain has `IsOutboundEnabled=false`, immediately `return nil, err` [2](#0-1) . This discards every outbound already built from prior logs in the same loop, and the error propagates up through `CreateUniversalTxFromReceiptIfOutbound` and `PostTxProcessing` [3](#0-2) , meaning the entire batch of outbounds derived from one EVM transaction's receipt is lost when only one of the involved chains is disabled — mirroring the M-5 anti-pattern of "revert the whole thing" instead of "`continue`/skip the one bad element."

### Finding Description
`PostTxProcessing` runs after the EVM execution has already committed the transaction (the gateway contract's `sendUniversalTxOutbound` calls — and any corresponding token burns/locks inside the gateway — have already executed and are part of the committed receipt) [4](#0-3) . `BuildOutboundsFromReceipt` then walks `receipt.Logs` looking for `UniversalTxOutbound` events emitted by the gateway and, for each one, checks `IsChainOutboundEnabled` for that event's `ChainId` [5](#0-4) . If disabled, the function returns an error for the *entire* call rather than skipping just that log and continuing to build outbounds for the other (enabled) chains referenced in the same receipt.

Because `outbounds` accumulated so far in the loop is discarded on the `return nil, err` path, a single payload/transaction that legitimately produces multiple `UniversalTxOutbound` events to different destination chains (e.g., a payload performing multiple withdrawals in one call) would lose all outbound bookkeeping for chains that *are* enabled, purely because one *other* chain in the same batch happens to be disabled. This is structurally identical to the M-5 root cause: an admin-mutable "enabled/killed" flag on one item in a set is checked with a hard `revert`, which then improperly propagates to and destroys the validity of unrelated items in the same operation, rather than being isolated with a `continue`.

### Impact Explanation
If this path is reachable with multiple outbound-producing logs in one receipt, the practical effect is: value already moved/burned on the Push Chain EVM side (as part of the committed transaction) for the *enabled* destination chains never gets an `OutboundTx` record created, and is therefore never picked up by `PendingOutbounds`/TSS signing — funds intended to be released to a legitimate, enabled chain become permanently stuck with no on-chain accounting of the pending release. This falls under the "permanent freezing of user or protocol-controlled funds" and "corruption of ... UniversalTx state" impact categories in scope.

### Likelihood Explanation
Likelihood is **uncertain** given available information. I could not confirm from the indexed code/tests whether the gateway contract (off-repo Solidity, in `push-chain-core-contracts`) or any Push Chain payload path can emit more than one `UniversalTxOutbound` event within a single receipt/transaction. All test fixtures found (`TestPostTxProcessing_WithSyntheticOutboundEvent`, `TestOutbound_ChainEnabled`) construct receipts with exactly one such log [6](#0-5) . If a single receipt can never legitimately contain multiple `UniversalTxOutbound` logs for different chains, the practical impact of this early-return degrades to "no outbound created for this one chain" (which the code already handles correctly for the single-log case, per `TestOutbound_ChainEnabled`'s "outbound not created when destination chain outbound is disabled" case at [7](#0-6) , which shows the UTX is *still* created with an empty `OutboundTx` list in the inbound-driven path — a different, safer code path than `BuildOutboundsFromReceipt`). Confirming whether the multi-log scenario is reachable via `MsgExecutePayload` with an attacker-controlled payload that trigger multiple gateway withdraw calls in one EVM transaction requires deeper inspection of the gateway contract and `CallUEAExecutePayload`, which is outside the indexed data available to me.

### Recommendation
In `BuildOutboundsFromReceipt`, change the disabled-chain branch from `return nil, err` to logging a warning and `continue`ing to the next log, so that outbounds for other, enabled destination chains in the same receipt are still built and attached. If a disabled-chain event must still be surfaced, record it separately (e.g., append to a `revert_error`/audit list on the UTX) instead of aborting outbound construction for the whole receipt. Additionally, confirm whether `sendUniversalTxOutbound` can be called multiple times with different destination chains within one Push Chain-side transaction, since that determines whether this fix is purely defensive or closes an active fund-freezing bug.

### Citations

**File:** test/integration/uexecutor/evm_hooks_and_outbound_test.go (L542-552)
```go
		evmLog := &ethtypes.Log{
			Address: common.HexToAddress(gatewayAddr),
			Topics:  []common.Hash{eventSigHash, txIdHash, senderHash, tokenHash},
			Data:    data,
			Removed: false,
		}
		receipt := &ethtypes.Receipt{
			TxHash:  common.HexToHash("0xsynth001"),
			GasUsed: 50000,
			Logs:    []*ethtypes.Log{evmLog},
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

**File:** x/uexecutor/keeper/create_outbound.go (L44-57)
```go
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

**File:** x/uexecutor/keeper/evm_hooks.go (L25-63)
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
```

**File:** test/integration/uexecutor/chain_enabled_test.go (L360-380)
```go
func TestOutbound_ChainEnabled(t *testing.T) {

	t.Run("outbound not created when destination chain outbound is disabled", func(t *testing.T) {
		testApp, ctx, vals, inbound, coreVals := setupOutboundChainEnabledTest(t, 4, false)

		// Reach quorum — VoteInbound itself must succeed (inbound is enabled)
		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreValAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteInbound(t, ctx, testApp, vals[i], coreValAcc, inbound)
			require.NoError(t, err)
		}

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := testApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found, "UTX should still be created even when outbound is disabled")
		require.Empty(t, utx.OutboundTx, "no outbound should be attached when destination chain outbound is disabled")
	})
```
