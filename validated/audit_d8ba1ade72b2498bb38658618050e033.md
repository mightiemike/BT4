### Title
Outbound event processing hard-fails (instead of gracefully skipping) when destination chain has outbound disabled, permanently stranding user funds/payload-forwarding state - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
The external report describes `Karma::_slash` being unable to complete a mandatory security operation (claiming/redeeming rewards) because a downstream component (`IRewardDistributor`) is pausable and the pause is not distinguished from a hard failure — the fix was to make the mandatory loop tolerant of a paused/disabled component instead of aborting the whole operation. Push Chain's `x/uexecutor` module has the same structural pattern: `BuildOutboundsFromReceipt`, which is invoked from mandatory crosschain-execution paths (inbound vote finalization and `ExecutePayload`), treats a disabled destination chain as a hard error instead of gracefully skipping that outbound, even though the EVM-side state (gateway event, token debit/mint on Push Chain) has already been produced.

### Finding Description
`BuildOutboundsFromReceipt` scans the receipt's `UniversalTxOutbound` events and, for each one, checks `IsChainOutboundEnabled`: [1](#0-0) 

If the destination chain has outbound disabled, the function does **not** skip just that outbound — it aborts the entire receipt scan and returns a hard `error`. This function is the single source used by two mandatory production code paths:

- `AttachOutboundsToExistingUniversalTx`, called after inbound execution to attach outbounds discovered in the inbound's execution receipt to the already-created `UniversalTx`: [2](#0-1) 

- `CreateUniversalTxFromReceiptIfOutbound`, called from `ExecutePayload` and the EVM `PostTxProcessing` hooks: [3](#0-2) 

The integration test suite confirms the hard-fail behavior at the EVM-hook level: [4](#0-3) 

and at the `MsgVoteInbound`/quorum level (when the *inbound* side is disabled, no state is created — a different and correctly-guarded case): [5](#0-4) 

The `x/uexecutor` README explicitly documents that `PendingOutbounds` has **no automatic resolution** once an outbound is expected but does not land cleanly — "Auto-refund risks double-pay ... auto-retry risks double-delivery, and there is no safe automatic resolution. Operators investigate stuck outbounds ... resolution is governance-driven, not chain-driven": [6](#0-5) 

This is the same bug class as the external report: a legitimate, non-malicious "disabled/paused" state on one component (`ChainConfig.Enabled.IsOutboundEnabled`, set via `uregistry`, analogous to `StakeManager`'s pausability) causes a *mandatory* accounting/execution step (attaching the outbound / completing the `UniversalTx` lifecycle) to abort entirely rather than degrade gracefully, and the module's own documentation confirms there is no safe automated remediation once this happens — mirroring exactly why the original finding was rated as a real bug (mandatory security/accounting flow silently blocked by a pause state).

### Impact Explanation
When a Universal Gateway contract call on Push Chain (triggered by an ordinary inbound execution or by a user's own `MsgExecutePayload`) emits a `UniversalTxOutbound` event targeting a chain whose outbound leg is currently disabled, the EVM-side effects (token debits/burns/gateway state changes reflected in the log) have already occurred by the time `BuildOutboundsFromReceipt` runs post-hoc over the receipt. The hard error returned prevents the `PendingOutbounds` entry and `OutboundTx` record from ever being created for that leg, and per the module's own documentation there is no automatic refund or retry path for this situation. This can result in unrecoverable/stranded fund-forwarding state for ordinary users' crosschain transactions, without requiring any attacker-controlled input — the only "unusual" precondition is the same kind of externally-imposed pause state (chain outbound disabled) that was present, but not attacker-controlled, in the original `Karma::_slash` finding.

### Likelihood Explanation
Chains can be legitimately disabled for maintenance, incident response, or deprecation (this is a documented, intended admin capability, `ChainConfig.Enabled`), and in-flight user transactions routed through `x/uexecutor` do not check this flag before the fact — only at receipt-processing time. Any user submitting a normal crosschain deposit or payload whose execution generates an outbound to a chain that becomes (or already is) outbound-disabled will hit this hard-fail path. This is a reachable, non-privileged trigger condition (the only precondition, chain-disablement, is an environmental/operational state rather than an attacker action) — directly analogous to the original report's precondition of "any reward distributor paused."

### Recommendation
Do not hard-fail the entire outbound-attachment/UTX-completion flow when a destination chain's outbound is disabled. Instead:
- Skip building/attaching that specific outbound (log a warning) similar to how the report recommends `isPaused()`-based skipping in `IRewardDistributor`, and mark the `UniversalTx`/inbound with an explicit terminal or "awaiting-manual-resolution" state that is safely retryable once the chain is re-enabled, rather than aborting silently with an untracked error.
- Ensure the underlying EVM-side receipt processing (mint/burn/execution) and the Cosmos-side bookkeeping (`UniversalTx`, `PendingOutbounds`) cannot diverge — i.e., if EVM state changes are already committed, the module-level accounting must still be updated to reflect the situation (e.g., an explicit `AWAITING_OUTBOUND_ENABLE` status) instead of returning a bare error that the caller may or may not properly roll back or persist.
- Add a governance-safe, chain-driven resolution path (auto-retry once the chain is re-enabled) instead of relying purely on manual/governance intervention as currently documented.

### Proof of Concept
Not independently reproduced in this analysis (index-only investigation); this is inferred from the following verified code/test evidence:
1. `IsChainOutboundEnabled` gate hard-errors instead of skipping in `BuildOutboundsFromReceipt`: [1](#0-0) .
2. Test proving the hard failure surfaces from the EVM post-processing hook path even after the EVM-side gateway event has already been decoded from a completed receipt: [4](#0-3) .
3. Module documentation confirming no safe automatic resolution exists once an outbound expectation cannot be fulfilled: [6](#0-5) .

**Caveat / open question for follow-up verification:** I was not able to fully trace, within the available iterations, whether the caller of `AttachOutboundsToExistingUniversalTx` in the `VoteInbound` finalization path (a) rolls back the entire inbound execution atomically when this error occurs (in which case the impact is "vote/finalization repeatedly fails and can never complete for that inbound, a liveness/DoS-style impact"), or (b) commits the EVM-side effects (mint, payload execution) while silently failing to record the outbound (in which case the impact is a genuine fund-freeze/accounting-corruption case). This distinction materially affects the severity and should be confirmed by a background engineer by reading `x/uexecutor/keeper/msg_vote_inbound.go` (or equivalent) and any surrounding DB/state transaction boundaries around the call to `AttachOutboundsToExistingUniversalTx`.

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

**File:** x/uexecutor/keeper/create_outbound.go (L144-155)
```go
func (k Keeper) AttachOutboundsToExistingUniversalTx(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	utx types.UniversalTx,
) error {
	outbounds, err := k.BuildOutboundsFromReceipt(ctx, utx.Id, receipt)
	if err != nil {
		return err
	}

	return k.attachOutboundsToUtx(ctx, utx.Id, outbounds, "")
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L160-185)
```go
func (k Keeper) CreateUniversalTxFromReceiptIfOutbound(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	pcTx types.PCTx,
) error {
	universalTxKey, err := k.BuildPcUniversalTxKey(ctx, pcTx)
	if err != nil {
		return errors.Wrap(err, "failed to create UniversalTx key")
	}

	outbounds, err := k.BuildOutboundsFromReceipt(ctx, universalTxKey, receipt)
	if err != nil {
		return err
	}

	if len(outbounds) == 0 {
		return nil
	}

	utx, err := k.CreateUniversalTxFromPCTx(ctx, pcTx)
	if err != nil {
		return err
	}

	return k.attachOutboundsToUtx(ctx, utx.Id, outbounds, "")
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

**File:** x/uexecutor/README.md (L273-282)
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
