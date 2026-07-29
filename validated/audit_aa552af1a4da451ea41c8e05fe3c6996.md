## Finding

### Title
INBOUND_REVERT outbound path bypasses the `IsChainOutboundEnabled` kill-switch, allowing outbound communication to a disabled chain - (File: `x/uexecutor/keeper/build_revert_outbound.go`)

### Summary
The Maia M-05 bug class is "a deactivated bridge agent can still originate cross-chain calls because one code path enforces the active/inactive check while a sibling code path does not." The Push Chain analog exists in `uexecutor`'s outbound-creation logic: the *normal* outbound path enforces `IsChainOutboundEnabled` before creating an `OutboundTx`, but the *revert* outbound path (`buildRevertOutbound`) does not, so a chain whose outbound has been administratively disabled can still have `INBOUND_REVERT` outbounds queued and signed for it.

### Finding Description
`BuildOutboundsFromReceipt`, the path that creates outbounds from a `UniversalTxOutbound` EVM event, explicitly checks the registry's kill-switch before building an outbound: [1](#0-0) 

However, `buildRevertOutbound`, which constructs an `INBOUND_REVERT` `OutboundTx` targeting `inbound.SourceChain`, performs no such check: [2](#0-1) 

This function is invoked from multiple production call sites, none of which gate on `IsChainOutboundEnabled(sourceChain)`:
- `handleFailedInboundValidation`, called whenever `Inbound.ValidateForExecution()` fails after ballot finalization [3](#0-2) 
- `RevertStuckInbound` (admin escape hatch, but it also has no outbound-enabled check before attaching) [4](#0-3) 
- `execute_inbound_funds.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go` (each falls back to `buildRevertOutbound` when execution/deposit fails).

`inbound.SourceChain` is the same CAIP-2 chain identifier gated by `ChainConfig.Enabled.IsOutboundEnabled` — it is both the source of the inbound and the destination of its revert, so "outbound disabled for chain X" should logically block *all* outbound-directed traffic to X, including reverts. Only `VoteInbound` checks `IsChainInboundEnabled` before creating a UTX at all [5](#0-4) ; there is no corresponding check on the outbound side for the revert path.

### Impact Explanation
An admin can disable outbound to a chain via `UpdateChainConfig` (e.g., in response to a compromised gateway, a bug in that chain's integration, or a TSS/migration event on that chain) intending it to be an emergency stop of all outbound communication to that chain, consistent with `x/uregistry/keeper/msg_update_chain_config.go`. Despite this, an ordinary unprivileged user can still trigger the queuing of a signed outbound transaction back to that "disabled" chain simply by submitting a malformed or invalid inbound deposit (e.g., an unsupported token, empty recipient, or any condition that fails `ValidateForExecution`/deposit). This is precisely the "no-deposit"-analog bypass from the Maia finding: the emergency kill switch is rendered ineffective for one of the module's outbound sub-flows, and honest universal validators will faithfully sign/broadcast a TSS transaction to the disabled chain because nothing in the revert pipeline stops them. This matches the Push Chain scope's "TSS coordination" and "revert accounting ... must not misroute value" impact categories — funds/authorization flow to a chain the operators intentionally cut off.

### Likelihood Explanation
High reachability: any ordinary user who submits a deposit that fails execution validation (e.g., unsupported/removed token config, malformed recipient) on a chain that has since had outbound disabled will trigger this path with no special privileges, no validator collusion, and no race condition required.

### Recommendation
Add the same `k.uregistryKeeper.IsChainOutboundEnabled(ctx, inbound.SourceChain)` check used in `BuildOutboundsFromReceipt` to `buildRevertOutbound` (or to its callers before invoking `attachOutboundsToUtx`), so that revert/rescue outbounds are subject to the identical enable/disable gate as forward outbounds. If outbound is disabled, the failure should be recorded on the UTX (e.g., via `RevertError`) without creating a `PendingOutbounds` entry, consistent with how `handleFailedInboundValidation` already records `RevertError` on attach failures.

### Proof of Concept
1. Admin registers chain `eip155:X` with `Enabled.IsInboundEnabled = true`, `Enabled.IsOutboundEnabled = true`, allowing normal deposit traffic.
2. Admin later calls `MsgUpdateChainConfig` to set `Enabled.IsOutboundEnabled = false` for `eip155:X` (e.g., an emergency stop after discovering an issue with that chain's gateway/vault), while inbound remains enabled (or is independently re-enabled) so users can still deposit.
3. An unprivileged user submits (or a UV observes) an inbound deposit from `eip155:X` referencing a token whose `TokenConfig` has been removed/is invalid, or an empty/invalid recipient for a `FUNDS` tx type.
4. Validators vote via `MsgVoteInbound`; `IsChainInboundEnabled` passes, ballot finalizes, `ValidateForExecution` (or the deposit step) fails.
5. `handleFailedInboundValidation` → `buildRevertOutbound` constructs an `INBOUND_REVERT` `OutboundTx` with `DestinationChain: eip155:X` and calls `attachOutboundsToUtx`, which writes it to `PendingOutbounds` — with no `IsChainOutboundEnabled` check anywhere in this path (contrast with `test/integration/uexecutor/evm_hooks_and_outbound_test.go` lines 576-627, which shows the *only* place this check is asserted, on the `PostTxProcessing`/`BuildOutboundsFromReceipt` path).
6. Universal validators will pick up the pending outbound and proceed to TSS-sign/broadcast it to `eip155:X`, defeating the admin's outbound disable. [6](#0-5)

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

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-26)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

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

```

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L39-54)
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
```

**File:** x/uexecutor/keeper/admin_revert.go (L73-80)
```go
	revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)
	if revertOutbound == nil {
		return "", "", fmt.Errorf("failed to build revert outbound for inbound %s", universalTxKey)
	}

	if attachErr := k.attachOutboundsToUtx(sdkCtx, universalTxKey, []*types.OutboundTx{revertOutbound}, "admin revert: stuck ballot expired"); attachErr != nil {
		return "", "", fmt.Errorf("failed to attach revert outbound: %w", attachErr)
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L31-39)
```go
	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
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
