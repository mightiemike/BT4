Confirmed: `PostTxProcessing`'s `outbound disabled returns error` test proves that a hard error from `BuildOutboundsFromReceipt` (via `IsChainOutboundEnabled`/`GetTokenConfigByPRC20` lookups) propagates as an `error` return value from `EVMHooks.PostTxProcessing` [1](#0-0) , and this hook is wired as the global `EvmHooks` for the entire EVM keeper — invoked after **every** EVM transaction on the chain, not just protocol-initiated ones [2](#0-1) .

### Title
Unvalidated `UniversalTxOutbound` event decoding in the global `EvmHooks.PostTxProcessing` callback can be forced to hard-error by any user, reverting normal EVM transactions - ([File: x/uexecutor/keeper/evm_hooks.go])

### Summary
`PostTxProcessing` is registered as the chain-wide `evmtypes.EvmHooks` implementation via `app.EVMKeeper.SetHooks(uexecutorkeeper.NewEVMHooks(...))`, so it runs after **every** EVM transaction's execution, not only transactions the protocol itself initiates. [2](#0-1)  Inside it, `CreateUniversalTxFromReceiptIfOutbound` → `BuildOutboundsFromReceipt` decodes any log emitted by the `UNIVERSAL_GATEWAY_PC` system-contract address with the `UniversalTxOutbound` topic, then performs `IsChainOutboundEnabled` and `GetTokenConfigByPRC20` lookups and **returns a hard `error`** if the destination chain is disabled or the PRC20 token isn't registered. [3](#0-2)  That error is bubbled straight up through `PostTxProcessing` without being caught/logged-and-swallowed. [1](#0-0)  An integration test explicitly documents this: emitting the event for a chain with `IsOutboundEnabled: false` makes `PostTxProcessing` return an error containing `"outbound is disabled"`. [4](#0-3) 

This is structurally analogous to the Lido `OrderedCallbacksArray`/`processLidoOracleReport()` issue: a system callback invoked on every core execution path is not defensively validated against attacker-influenced or malformed/unexpected input, and its failure propagates up and reverts the parent transaction — creating a DoS/self-halting condition triggered by ordinary, unprivileged calldata rather than by the callback failing to implement an expected interface.

### Impact Explanation
If the `UNIVERSAL_GATEWAY_PC` contract's `withdraw`/`withdrawAndExecute` (or any function) can be invoked by an ordinary EVM user with an attacker-chosen destination chain-id or PRC20 token address that is not registered/enabled in `x/uregistry` (e.g. a disabled chain, or a token address that was never registered as a PRC20), the resulting `UniversalTxOutbound` log will cause `BuildOutboundsFromReceipt` to hard-fail, and that failure propagates through `PostTxProcessing` as a hook error. Depending on how the cosmos-evm fork handles `EvmHooks` errors (this repo does not vendor that logic; it only defines the hook implementation), this can cause the *user's own EVM transaction* to fail/revert unexpectedly even though its EVM execution otherwise succeeded, and in the worst case (if the fork treats hook errors as fatal to `ApplyTransaction`/block processing) could halt processing for any transaction that reaches this code path. At minimum, it converts an attacker-reachable value-mismatch (unregistered token/disabled chain) into a hard failure of a callback that runs on the hot path of every EVM transaction on the chain, rather than being recorded as a FAILED PCTx the way the equivalent isCEA `executeUniversalTx` failures are already handled elsewhere in the codebase. [5](#0-4) 

### Likelihood Explanation
Reachability depends entirely on whether the real (non-test) `UniversalGatewayPC` contract lets an ordinary caller trigger a withdraw with an arbitrary/unregistered token or disabled-chain destination — the contract source itself is out of scope for this repo (only a test stub bytecode is present, explicitly noted as skipping validation for test purposes only). [6](#0-5)  I could not verify from this codebase whether the production Solidity gateway contract enforces token/chain registration before emitting the event, so I cannot confirm the trigger is actually reachable by an unprivileged external attacker in production — this is the key open uncertainty.

### Recommendation
Treat `BuildOutboundsFromReceipt`/`PostTxProcessing` failures the same defensive way the isCEA `executeUniversalTx` path already does: catch decode/lookup errors, record them without propagating a hard error out of the global `EvmHooks.PostTxProcessing`, and only fail loudly for genuine infrastructure errors (KV-store failures), not for attacker-controllable event contents (unregistered token, disabled chain). This mirrors the Lido report's recommendation to validate/guard external-input-driven callback behavior rather than letting it uncontrollably revert the caller (or, worse, chain-wide EVM tx processing).

### Proof of Concept
Not independently constructible from this repository alone, because the actual `UniversalGatewayPC.sol` contract logic (whether/how it validates chain/token before emitting `UniversalTxOutbound`) is not present in this codebase — only a test-only bytecode stub is available. [6](#0-5)  The `TestPostTxProcessing_WithSyntheticOutboundEvent/outbound disabled returns error` subtest confirms the propagation mechanism (hook returns hard error for a synthetically-constructed log), but does not confirm real-world attacker reachability of the trigger. [7](#0-6) 

**Given the unresolved uncertainty about whether an unprivileged user can actually drive the production gateway contract to emit this event with an invalid/unregistered token or disabled chain, and because I could not confirm from this repo how the cosmos-evm fork treats a returned `EvmHooks` error (whether it only fails the emitting user's own tx, or something more severe), I cannot assert this rises to a confirmed, reachable Push Chain vulnerability with full confidence.** If you want this fully confirmed, the production `UniversalGatewayPC.sol` source and the cosmos-evm fork's `ApplyTransaction`/hook-error-handling code (not present in this repo's index) would need to be reviewed.

### Citations

**File:** x/uexecutor/keeper/evm_hooks.go (L60-63)
```go
	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}
```

**File:** app/app.go (L794-794)
```go
	app.EVMKeeper.SetHooks(uexecutorkeeper.NewEVMHooks(app.UexecutorKeeper))
```

**File:** x/uexecutor/keeper/create_outbound.go (L44-67)
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

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L258-276)
```go
		callPcTx := types.PCTx{
			Sender:      ueModuleAddressStr,
			BlockHeight: uint64(sdkCtx.BlockHeight()),
			Status:      "FAILED",
		}
		if contractReceipt != nil {
			callPcTx.TxHash = contractReceipt.Hash
			callPcTx.GasUsed = contractReceipt.GasUsed
		}
		switch {
		case contractErr != nil:
			callPcTx.ErrorMsg = contractErr.Error()
		case contractReceipt == nil:
			// EVM call returned nil receipt without error — leave Status FAILED, no message.
		case feeErr != nil:
			callPcTx.ErrorMsg = fmt.Sprintf("gas fee deduction failed: %s", feeErr.Error())
		default:
			callPcTx.Status = "SUCCESS"
		}
```

**File:** test/utils/contracts_setup.go (L325-346)
```go
// ---------------------------------------------------------------------------------------
// NOTE: The UniversalGatewayPC contract deployed here is a TEST-ONLY version.
//
// The withdraw() and withdrawAndExecute() functions inside this test contract:
//
//   - DO NOT run validation (_validateCommon)
//   - DO NOT compute gas fees via UniversalCore
//   - DO NOT pull PRC20 fees into VaultPC
//   - DO NOT burn PRC20 tokens
//   - DO NOT interact with any external contracts
//
// Instead, both functions simply **emit UniversalTxWithdraw with hardcoded values**:
//
//	chainId   = "eip155:11155111"
//	gasToken  = fixed test address
//	gasFee    = 111
//
// This behavior is intentional because Cosmos integration tests only need to verify:
//   - ABI correctness
//   - Event emission structure
//   - Outbound pipeline handling
//   - UE/UEM processing logic on the Cosmos side
```
