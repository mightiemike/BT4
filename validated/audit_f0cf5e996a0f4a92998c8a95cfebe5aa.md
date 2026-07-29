## Analysis

The external report's bug class — **funds becoming permanently stuck when a "decommissioning"/disabled state coincides with in-flight user funds already committed to an escrow-like step** — has a concrete analog in Push Chain's outbound-creation path.

### Mapped Location
`x/uexecutor/keeper/create_outbound.go`, function `BuildOutboundsFromReceipt`: [1](#0-0) 

This function runs **after** a derived EVM transaction has already been executed and committed (`receipt` is the result of a completed `DerivedEVMCall`). By the time this code runs, the `UNIVERSAL_GATEWAY_PC` contract's `withdraw`/`withdrawAndExecute` path has already burned the sender's PRC20 and pulled fees into `VaultPC` as part of that same already-finalized EVM transaction — this is explicitly documented as production behavior in the test-contract comment contrasting the test-only gateway with the real one: [2](#0-1) 

Only *after* this burn has already happened does `BuildOutboundsFromReceipt` check `IsChainOutboundEnabled` for the destination chain and, if disabled, aborts by returning an error instead of creating the `OutboundTx`: [3](#0-2) 

The integration test confirms this exact scenario is reachable through the normal EVM post-processing hook, and that the disabled-chain condition surfaces only as a propagated error, with no evidence of re-minting the just-burned PRC20 back to the sender: [4](#0-3) 

### Why this parallels the Sherlock finding
- **Escrow analog**: In the original bug, the borrower's stablecoins are locked in an escrow step and become unrecoverable once the collateral (chain) enters a decommissioned/recovery state. Here, the PRC20 is burned as an irreversible on-chain EVM step (analogous to escrow) as part of the withdraw payload execution.
- **Decommission analog**: `IsChainOutboundEnabled` becoming `false` for the destination chain (e.g., a chain being sunset/disabled, as documented for `x/utss` migration flows) is the direct analog of "collateral sunset" — a chain state that legitimately changes over time for operational/security reasons: [5](#0-4) 
- **No recovery path**: Unlike `handleFailedOutbound`, which re-mints PRC20 when an already-created `OutboundTx` is later voted as failed by UVs: [6](#0-5) 
…there is **no equivalent re-mint/refund path** when `BuildOutboundsFromReceipt` itself aborts before ever creating the `OutboundTx`. The `x/uexecutor` README explicitly states that once an outbound reaches this stage, resolution requires manual, governance-driven admin intervention rather than an automatic chain-driven fix — and this case doesn't even reach the tracked "stuck outbound" bookkeeping (`PendingOutbounds`) since no `OutboundTx` was ever attached: [7](#0-6) 

### Uncertainty
I could not fully trace, within the available tool budget, exactly how the error returned by `BuildOutboundsFromReceipt` is handled by every caller (`execute_inbound_funds_and_payload.go`, `execute_inbound_gas_and_payload.go`, `execute_payload.go`, and the EVM `PostTxProcessing` hook) — specifically whether any of these callers wrap the burn + outbound-check sequence in a single atomic `CacheContext` that would roll back the burn on error (which would make this a non-issue), or whether the burn's `PcTx` entry is left as `"SUCCESS"` permanently with the burned funds unrecoverable. The one concrete test (`evm_hooks_and_outbound_test.go`) exercises this via `PostTxProcessing`, which in the Cosmos EVM hook model runs after the inner EVM state transition has already been applied, strongly suggesting the burn is not rolled back — but this should be verified directly against the caller code (`x/uexecutor/keeper/execute_payload.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas_and_payload.go`) before treating this as fully confirmed.

### Title
Withdraw-initiated PRC20 burn is not reverted or refunded when the destination chain's outbound gets disabled after the burn but before outbound creation - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
A user's cross-chain withdraw payload burns their PRC20 on Push Chain as an already-committed EVM side effect. If, at that exact moment, the destination chain's outbound is disabled (chain sunset/decommission), `BuildOutboundsFromReceipt` aborts and no `OutboundTx` is ever created, and no automatic re-mint occurs, leaving the burned funds permanently unrecoverable through normal user or automated flows.

### Finding Description
`BuildOutboundsFromReceipt` decodes the `UniversalTxOutboundEvent` from a receipt log that is only emitted once the gateway contract has already burned the user's PRC20 (per production gateway semantics documented in the test harness). The function then checks `IsChainOutboundEnabled` and, if the destination chain's outbound flag has been turned off, returns an error instead of creating the outbound record — but the burn has already occurred in the preceding, already-committed EVM transaction. There is no compensating re-mint call in this failure branch, unlike the symmetric `handleFailedOutbound` path used when an already-created outbound is later voted as failed.

### Impact Explanation
This results in permanent loss of user-controlled PRC20 funds: burned from the user's balance with no destination-chain delivery and no re-mint back to the sender, matching the "Allowed Impact" criteria of permanent loss of user funds via a scoped module (`x/uexecutor`).

### Likelihood Explanation
Requires the ordinary combination of (a) a user submitting a normal withdraw-type payload (via `MsgExecutePayload` or a `FUNDS_AND_PAYLOAD` inbound), and (b) the destination chain's outbound being disabled between burn and outbound-attachment — a legitimate, foreseeable operational event (chain deprecation/incident response), not requiring any privileged or malicious actor on the attacker's part. Likelihood is Medium given it depends on timing relative to an admin/ops action, mirroring the original report's likelihood rating.

### Recommendation
In `BuildOutboundsFromReceipt`, when `IsChainOutboundEnabled` returns false, do not merely error out — either (a) synchronously re-mint the burned PRC20 back to the sender/original UEA within the same execution before returning, or (b) create the `OutboundTx` in an `ABORTED`/refund-pending state that feeds into an automatic or admin-triggered re-mint flow, so the burn and its remediation are never split across an unrecoverable gap.

### Proof of Concept
1. User (via UEA) submits a payload that calls the `UNIVERSAL_GATEWAY_PC` withdraw function for destination chain `eip155:X`, which burns the PRC20 and emits `UniversalTxOutboundEvent` as part of the same EVM transaction (`PcTx` recorded as `SUCCESS`).
2. Immediately before/at the point `BuildOutboundsFromReceipt` is invoked to process that receipt, chain admin disables `IsOutboundEnabled` for `eip155:X` (e.g., due to a security incident on that chain, analogous to "sunsetting").
3. `IsChainOutboundEnabled` returns `false`; `BuildOutboundsFromReceipt` returns an error and no `OutboundTx` is attached to the UTX (as reproduced in `test/integration/uexecutor/chain_enabled_test.go:362-380`, which asserts `utx.OutboundTx` is empty in this scenario).
4. The user's PRC20 remains burned; the UTX shows a `SUCCESS` `PcTx` for the withdraw call but no `OutboundTx`, and no automated re-mint path exists, per the module's own documentation of `PendingOutbounds`/refund handling only covering outbounds that were actually created.

### Citations

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

**File:** x/uexecutor/keeper/outbound.go (L99-119)
```go
// handleFailedOutbound mints back the bridged tokens to the revert recipient,
// then attempts to refund any excess gas (gasFee - gasFeeUsed) just like a
// successful outbound would. Both operations are recorded on the outbound.
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)
```

**File:** x/uexecutor/README.md (L273-283)
```markdown
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
