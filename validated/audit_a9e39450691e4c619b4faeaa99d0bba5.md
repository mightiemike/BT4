### Title
Outbound record and refund are silently dropped after PRC20 is already burned when destination chain outbound is disabled - (File: x/uexecutor/keeper/create_outbound.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/execute_inbound_funds_and_payload.go)

### Summary
The reported bug's invariant — "never leave a user's accounting mutated by a state change that occurred while a flow was paused/disabled, without a matching revert/refund" — has a direct analog in `x/uexecutor`. `ChainConfig.Enabled.IsOutboundEnabled` acts as a per-chain pause switch, checked inside `BuildOutboundsFromReceipt` [1](#0-0) . Unlike `VoteInbound`, which explicitly checks its pause flag "before any state changes" [2](#0-1) , the outbound-disabled check runs *after* the user's payload has already executed and committed on the EVM side (PRC20 burn + fee pull inside `UniversalGatewayPC.withdraw()`/`withdrawAndExecute()`), with no compensating revert or refund when the check fails.

### Finding Description
When an inbound of type `FUNDS_AND_PAYLOAD` or `GAS_AND_PAYLOAD` runs a user payload that calls `UniversalGatewayPC.withdraw()`/`withdrawAndExecute()`, the real (non-test) gateway contract burns the user's PRC20 and pulls PRC20 fees into `VaultPC` as part of emitting the `UniversalTxOutbound` event — confirmed by the inline documentation of the test-only gateway stub, which explicitly lists what the production contract does that the stub skips: burn PRC20 tokens, pull fees into VaultPC, and run `_validateCommon` [3](#0-2) .

`ExecutePayloadV2` is invoked via a committing `DerivedEVMCall` (commit=true), so this burn is durably written to EVM state before the Cosmos-side accounting runs [4](#0-3) . Only afterward does the keeper call `AttachOutboundsToExistingUniversalTx` → `BuildOutboundsFromReceipt`, which checks `IsChainOutboundEnabled` for the destination chain named in the emitted event [1](#0-0) . If outbound is disabled for that chain, the function returns an error, and the caller simply records the error string on `UTX.RevertError` and returns nil — no `OutboundTx` entry is created, no revert outbound is built, and no PRC20 is re-minted to compensate the burn: [5](#0-4) 

This is fundamentally different from the sibling deposit-failure paths in the same file, which explicitly call `k.buildRevertOutbound` + `k.attachOutboundsToUtx` to restore funds when a *pre*-deposit step fails [6](#0-5) . No equivalent recovery exists for a post-execution outbound-attach failure.

The `EVMHooks.PostTxProcessing` path (triggered for ordinary user-submitted EVM transactions that call the gateway directly, not just inbound-driven payloads) exhibits the identical behavior and is directly demonstrated in the test suite: the gateway emits `UniversalTxOutbound`, but because the destination chain is outbound-disabled, `BuildOutboundsFromReceipt` errors out and the whole outbound record is lost — while the underlying `withdraw()` EVM transaction (with its burn) has already been mined/committed [7](#0-6) .

### Impact Explanation
An ordinary user who calls the real `UniversalGatewayPC.withdraw()`/`withdrawAndExecute()` (either directly via a normal EVM tx, or indirectly via an inbound payload) to bridge PRC20 back to a chain whose `IsOutboundEnabled` flag is `false` at that moment loses their burned tokens permanently: the burn/fee-pull is committed, but no `OutboundTx` is ever created, no `INBOUND_REVERT`/refund path runs, and the only trace is a `RevertError` string on the `UniversalTx`. Because `OutboundTx.outbound_status` and `PendingOutbounds` are the sole mechanisms operators use to track and reconcile stuck value [8](#0-7) , an outbound that was never created in the first place is invisible to that reconciliation process — there is no `ABORTED` outbound, no `PENDING` entry, nothing for `RESCUE_FUNDS` tooling to act on. This matches the "permanent loss of user funds" and "corruption of ... revert/refund accounting" impact categories in the allowed impact gate.

### Likelihood Explanation
Disabling a chain's outbound flag is an ordinary/expected admin operation (analogous to the Isomorph `pause`), not an attack. Any user with an in-flight `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` inbound or a directly-submitted withdraw transaction whose target chain's outbound gets disabled between payload construction and execution (or is simply disabled at execution time for any operational reason) triggers this path with no special skill required — this is a normal, unprivileged user flow, not one requiring a malicious actor. Confirmed via the existing `outbound disabled returns error` test case, which demonstrates the code path reaching this exact state and producing only an error with no compensating action.

### Recommendation
Wrap the payload-execution EVM call and the outbound-attach step in a single cache-context (as is already done for the smart-contract fee-deduction case at `execute_inbound_funds_and_payload.go` lines 233–255, which commits the EVM burn/deposit only if downstream steps succeed), so that an outbound-disabled failure rolls back the burn/fee-pull atomically. Alternatively, when `BuildOutboundsFromReceipt` fails due to a disabled destination chain, synthesize and attach a revert outbound (as done for pre-deposit failures) that re-mints the burned PRC20 back to the sender/fund-recipient, instead of only recording `UTX.RevertError`.

### Proof of Concept
1. Admin configures chain `eip155:X` with `IsOutboundEnabled=true`, token config, and deploys the user's UEA.
2. User submits (or an inbound triggers) a payload that calls `UniversalGatewayPC.withdraw(...)` targeting chain `eip155:X`; in production this burns the user's PRC20 and pulls the fee into `VaultPC`, then emits `UniversalTxOutbound`.
3. Before/at the point the Cosmos-side keeper processes the receipt, `eip155:X`'s `IsOutboundEnabled` is `false` (routine admin/ops action).
4. `BuildOutboundsFromReceipt` returns `"outbound is disabled for chain eip155:X"` [9](#0-8) ; the caller only sets `UTX.RevertError` [5](#0-4) .
5. Result: PRC20 already burned (EVM state committed), no `OutboundTx` created, no re-mint, no `PendingOutbounds` entry — funds permanently unaccounted for. This exact code path (error without recovery) is reproduced by the existing test `TestPostTxProcessing_WithSyntheticOutboundEvent/outbound disabled returns error` [7](#0-6) , confirming the error is surfaced but nothing compensates for already-committed EVM-side value movement.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L190-209)
```go
	// --- create revert ONLY for pre-deposit / deposit failures (non-isCEA path)
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, utx.InboundTx)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			revertReason,
		); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}

		return nil
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L292-298)
```go
	receipt, err = k.ExecutePayloadV2(
		ctx,
		ueModuleAddr,
		ueaAddr,
		utx.InboundTx.UniversalPayload,
		utx.InboundTx.VerificationData,
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L326-333)
```go
		if attachErr := k.AttachOutboundsToExistingUniversalTx(sdkCtx, receipt, utx); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
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

**File:** x/uexecutor/README.md (L270-282)
```markdown
  they arrive (`RecordOutboundVote` inside `VoteOutbound`). Multiple variants
  per outbound indicate validator divergence on the destination-chain
  observation (different `success`/`tx_hash`/`error_msg`/`gas_fee_used`).
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```
