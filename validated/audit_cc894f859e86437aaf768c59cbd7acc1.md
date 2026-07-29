## Finding

### Title
Bridged funds can be permanently lost when a destination chain's outbound is disabled after the withdraw-triggering execution has already committed - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
Push Chain implements a per-chain, independently-toggleable enable/disable flag (`ChainConfig.Enabled.IsOutboundEnabled`) that is the functional analog of Omni's `ACTION_WITHDRAW` pause. The check is enforced in `BuildOutboundsFromReceipt`, which hard-fails and drops the withdraw event entirely instead of persisting it for retry, mirroring the exact failure mode the external report describes.

### Finding Description
`ChainConfig.Enabled.IsOutboundEnabled` gates whether a `UniversalTxOutbound` event emitted by `UniversalGatewayPC` is ever converted into an `OutboundTx`: [1](#0-0) 

If the destination chain's outbound is disabled at the moment the withdraw-triggering EVM call runs, `BuildOutboundsFromReceipt` returns a hard error (`"outbound is disabled for chain %s"`) instead of creating an `OutboundTx`. This error propagates up through `CreateUniversalTxFromReceiptIfOutbound` and `EVMHooks.PostTxProcessing`: [2](#0-1) 

Critically, the decoded event data (destination chain, recipient, amount, gas fields, revert instructions) is only logged via `k.Logger().Warn` and then discarded — there is no on-chain, queryable, or retryable record created for it (no analog to `OmniGasPump.owed`/`OmniBridgeNative.claimable`): [3](#0-2) 

Push Chain's own architecture documents that PC execution (`PcTx`) and outbound creation (`OutboundTx`) are separate, sequential steps in the UTX lifecycle, and that the codebase deliberately uses staged `CacheContext`/`writeCache()` commits so that an earlier successful phase (e.g., the deposit, or a payload execution that triggers the withdraw call) persists even when a later phase fails — this pattern is explicitly documented and tested elsewhere in the repo: [4](#0-3) [5](#0-4) 

This is the same root cause shape as the Omni report: a withdraw-style action (burn/lock of PRC20 backing a bridged asset, emitted as `UniversalTxOutbound`) can complete and be recorded as a successful PC-side execution, while the step that actually delivers value to the destination chain (`OutboundTx` creation) is blocked by the per-chain outbound-disable flag, with no compensating revert and no persisted retry path once the flag is re-enabled.

### Impact Explanation
If the withdraw-triggering EVM execution (burn/lock of the PRC20 representation) commits independently of the outbound-creation step — as the repo's documented staged-commit pattern for inbound/payload execution suggests is possible for at least some call paths — a user's bridged funds are burned/locked on Push Chain with no corresponding `OutboundTx`/`PendingOutbounds` entry ever created, and no mechanism to reconstruct or retry the withdrawal once `IsOutboundEnabled` is restored. This is a permanent, unrecoverable loss of user funds, matching the in-scope impact category of "permanent loss ... of user or protocol-controlled funds."

### Likelihood Explanation
The `IsOutboundEnabled` check and its hard-fail/drop behavior in `BuildOutboundsFromReceipt` is confirmed and directly reachable from an ordinary user's bridging transaction whenever a destination chain has outbound disabled (as validated by the test `TestPostTxProcessing_WithSyntheticOutboundEvent`'s "outbound disabled returns error" case). What I could **not** conclusively verify within the available tooling is whether, for every call path that can emit `UniversalTxOutbound` (in particular the inbound-triggered `FUNDS_AND_PAYLOAD`/payload-execution path in `execute_inbound_funds_and_payload.go`), the underlying burn/lock EVM state is guaranteed to be rolled back atomically together with the outbound-creation failure, or whether it is committed via an earlier `writeCache()` before outbound attachment is attempted. This distinction determines whether funds are actually stranded or the whole operation cleanly reverts. I recommend this specific code path (`execute_inbound_funds_and_payload.go` past the deposit-recording section, and the exact scope boundary between `CallExecuteUniversalTx`/`AttachOutboundsToExistingUniversalTx`) be reviewed in a live session to confirm atomicity.

### Recommendation
- Ensure the withdraw/burn-triggering EVM call and outbound creation are wrapped in a single atomic scope (e.g., a shared `CacheContext` that is only committed via `writeCache()` after `BuildOutboundsFromReceipt`/`attachOutboundsToUtx` succeed), so a disabled destination chain cannot leave a committed burn with no corresponding outbound.
- Alternatively, persist the decoded `UniversalTxOutbound` event data (not just a log line) when outbound creation is blocked by a disabled chain, so it can be replayed into an `OutboundTx` once `IsOutboundEnabled` is restored — mirroring the `OmniGasPump.owed` retry-mapping suggestion from the source report.
- Require operational tooling/admin runbooks to drain in-flight withdraw-capable execution paths before disabling `IsOutboundEnabled` for a chain, and to disable the corresponding inbound/deposit paths first, consistent with the source report's recommended pause ordering.

### Proof of Concept
Not independently reproduced beyond the existing test `TestPostTxProcessing_WithSyntheticOutboundEvent/"outbound disabled returns error"` [6](#0-5) , which confirms `BuildOutboundsFromReceipt` errors out and drops the event when `IsOutboundEnabled=false`. Full exploitation would require constructing an inbound `FUNDS_AND_PAYLOAD` whose UEA payload itself calls the gateway's withdraw-equivalent method targeting a chain with `IsOutboundEnabled=false`, then confirming whether the resulting `PcTx` is recorded as `SUCCESS` (funds burned) with no corresponding `OutboundTx` ever attached — this final confirmation step is the part I was unable to complete before this session's tool budget was exhausted.

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

**File:** x/uexecutor/keeper/evm_hooks.go (L60-63)
```go
	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}
```

**File:** x/uexecutor/README.md (L174-193)
```markdown
3. Threshold of UV votes reached. The keeper executes the inbound:
   a. Mints the PRC20 to the recipient's UEA address.
      A new PCTx (deposit) is appended to UTX.PcTx.
   b. Runs the universal payload through the UEA.
      A second PCTx (executeUniversalTx) is appended.
   (UTX id removed from PendingInbounds.)

4. The payload triggered a destination-chain call (e.g. release funds on
   another chain). An OutboundTx is created with Status_PENDING and
   appended to UTX.OutboundTx. It is also indexed in PendingOutbounds.

5. UVs sign the outbound via TSS, broadcast it, and vote the result back
   via MsgVoteOutbound. The OutboundTx.observed_tx is filled in and
   outbound_status flips to OBSERVED. The PendingOutbounds entry is
   removed.

6. If the destination chain refunds excess gas, a refund PCTx runs on
   Push Chain. PCTx.pc_refund_execution is set on the OutboundTx. The
   refund is just additional evidence attached to the existing OutboundTx.
```
```

**File:** test/integration/uexecutor/inbound_cea_smart_contract_test.go (L354-360)
```go
	// F-2026-16738: when DeductGasFeesFromReceipt fails after a successful
	// CallExecuteUniversalTx, the EVM call + fee deduction now run inside a
	// CacheContext that is discarded on fee failure. The deposit (which
	// happens before this scope) stays committed; the executeUniversalTx
	// state changes are rolled back so the recipient cannot consume gas
	// without paying for it.
	t.Run("fee deduction failure rolls back executeUniversalTx, keeps deposit", func(t *testing.T) {
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
