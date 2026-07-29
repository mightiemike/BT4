### Title
`BuildOutboundsFromReceipt` can strand already-burned bridged funds when `GetTokenConfigByPRC20` fails after a valid `withdraw()` call - (File: `x/uexecutor/keeper/create_outbound.go`)

### Summary
`x/uexecutor`'s EVM post-processing hook (`EVMHooks.PostTxProcessing`) parses `UniversalTxOutbound` events emitted by `UniversalGatewayPC.withdraw()`/`withdrawAndExecute()` — the point at which PRC20 tokens are already burned on Push Chain in exchange for a promise to release funds on the destination chain. If the subsequent token-config lookup used to build the `OutboundTx` record fails, `BuildOutboundsFromReceipt` returns an error and no `OutboundTx`/pending-outbound record is ever created, so TSS/UV signing for the destination-chain release never starts. Because the PRC20 burn already happened inside the EVM call that produced the receipt, the user's bridged value is permanently gone with no compensating record — the same "silent/partial failure after funds already moved" pattern as the Lock/BondNFT bug.

### Finding Description
`BuildOutboundsFromReceipt` (`x/uexecutor/keeper/create_outbound.go:16-105`) is invoked from `CreateUniversalTxFromReceiptIfOutbound`, which is called from `EVMHooks.PostTxProcessing` (`x/uexecutor/keeper/evm_hooks.go:28-67`) — a hook the EVM module runs after a transaction has already executed and its receipt/logs have been produced. For each `UniversalTxOutbound` log emitted by `UniversalGatewayPC`, the function:

1. Decodes the event (`types.DecodeUniversalTxOutboundFromLog`).
2. Checks `IsChainOutboundEnabled` — returns an error if disabled.
3. Calls `k.uregistryKeeper.GetTokenConfigByPRC20(ctx, event.ChainId, event.Token)` to resolve the external asset address — and returns the raw error immediately if the PRC20 has no matching `TokenConfig` for that chain: [1](#0-0) 

The test suite explicitly demonstrates that this failure path returns an `error` all the way up through `PostTxProcessing`: [2](#0-1) 

The key issue is *when* this check runs relative to the burn. `UniversalTxOutbound` is only emitted by `UniversalGatewayPC.withdraw()`/`withdrawAndExecute()` after the contract has already burned the user's PRC20 balance and pulled protocol/gas fees (per the module docs, this happens synchronously inside the same EVM call whose receipt is later inspected by `PostTxProcessing`). Once that EVM message has committed (the burn is part of the same `MsgEthereumTx` state transition, independent of the *hook's* return value), the *only* remaining mechanism to eventually release equivalent funds on the destination chain is the `OutboundTx`/`PendingOutbounds` record that `BuildOutboundsFromReceipt` is supposed to create. If `GetTokenConfigByPRC20` errors (e.g., because the PRC20 was removed/renamed from the registry, or the config was never added for that exact `(chain, prc20)` pair — a state entirely reachable by ordinary registry drift, not by an attacker forging data), `BuildOutboundsFromReceipt` returns an error with **zero outbound created** and **zero UniversalTx created** (confirmed by `CreateUniversalTxFromReceiptIfOutbound_NoLogs`/`WithSyntheticOutboundEvent` tests, which show the UTX+outbound are only persisted when `BuildOutboundsFromReceipt` returns successfully — an error short-circuits `CreateUniversalTxFromReceiptIfOutbound` before any UTX write). No `INBOUND_REVERT`-style compensating outbound exists for this path (that compensation mechanism only exists for *inbound* execution failures, e.g. `handleFailedInboundValidation`, `execute_inbound_gas.go`, `execute_inbound_funds.go` — not for the outbound/withdraw creation path).

This is structurally identical to the reported Tigris bug: `Lock.claimGovFees` pulls funds from `GovNFT` into `Lock`, then calls `BondNFT.distribute`, which can silently no-op (`return`) when `totalShares==0` or asset not allowed, stranding the funds in `Lock` with no path to recovery. Here, `UniversalGatewayPC.withdraw()` burns PRC20 (moves value out of user control), then the on-chain hook that is supposed to record the compensating "release on destination chain" instruction can fail/no-op, stranding the value with no compensating outbound and no revert path.

### Impact Explanation
This falls under "permanent loss ... of user or protocol-controlled funds" and "unauthorized state transitions in universal execution flows" per the allowed impact gate: an ordinary user's cross-chain withdraw burns their PRC20 on Push Chain, but the corresponding `OutboundTx`/`PendingOutbounds` entry that would trigger TSS signing and destination-chain fund release is never created. The user has no recourse — there is no `INBOUND_REVERT`-equivalent flow for a failed outbound-creation step, only the `RevertStuckInbound` admin path which is scoped to *inbound* ballots, not to this receipt-driven outbound-creation path. This is reachable purely through ordinary user withdraw calls interacting with registry state that can legitimately be in flux (e.g., a token config removed/updated between when a UEA's payload was originally signed and when it executes, or a PRC20/chain pairing that was never registered for outbound use even though inbound minting exists for it).

### Likelihood Explanation
Likelihood is moderate: it requires a registry/token-config state where a PRC20 is mintable (inbound path) but its `(chain, prc20)` reverse mapping via `GetTokenConfigByPRC20` is missing or stale at the time `withdraw()` executes — this is a normal administrative/registry consistency issue rather than requiring privileged or malicious action, and it is entirely plausible given that `RemoveTokenConfig` is a normal governance/admin operation exercised elsewhere in the test suite. Any user whose withdraw transaction races such a registry change, or targets a PRC20 that was minted through some path without a corresponding reverse-indexed config, would trigger this loss deterministically.

### Recommendation
`BuildOutboundsFromReceipt` (and `PostTxProcessing`) must not allow the underlying EVM burn to be finalized without a durable, recoverable record. Options:
1. Make the token-config/outbound-enabled checks happen *before* `UniversalGatewayPC` is allowed to burn (i.e., validate synchronously inside the EVM call itself via a precompile/view call, so the burn transaction reverts atomically if the destination isn't resolvable) rather than after the fact in a post-processing hook.
2. If a config lookup fails after the burn is already committed, do not merely propagate an error and drop the event — persist a "stuck outbound" / `RESCUE`-eligible record analogous to `RevertStuckInbound`/`RESCUE_FUNDS`, so an admin or automated process can later fix the registry and retroactively attach the correct outbound instead of the funds being permanently unrecoverable.
3. Add an explicit invariant test asserting that every `UniversalTxOutbound` event decoded from a receipt results in either a successfully created `OutboundTx`/`PendingOutbounds` entry or a queryable "failed outbound creation" record with the raw event data preserved for manual recovery.

### Proof of Concept
1. Register chain `eip155:11155111` with `IsOutboundEnabled=true` and a PRC20 token that is *not* registered via `AddTokenConfig` for that chain (or whose `TokenConfig` has since been removed via `RemoveTokenConfig`, as done in `chainApp.UregistryKeeper.RemoveTokenConfig` calls throughout the test suite, e.g. `test/integration/uexecutor/inbound_solana_test.go:195`).
2. A user's UEA/payload calls `UniversalGatewayPC.withdraw()` (or `withdrawAndExecute()`) with that PRC20 address and a nonzero amount — the contract burns the PRC20 and emits `UniversalTxOutbound`.
3. `EVMHooks.PostTxProcessing` runs (`x/uexecutor/keeper/evm_hooks.go:61`) → `CreateUniversalTxFromReceiptIfOutbound` → `BuildOutboundsFromReceipt` (`x/uexecutor/keeper/create_outbound.go:60-67`) calls `GetTokenConfigByPRC20`, which returns `collections.ErrNotFound` because no config exists for `(eip155:11155111, <prc20>)`.
4. `BuildOutboundsFromReceipt` returns the error; `CreateUniversalTxFromReceiptIfOutbound` propagates it; no `UniversalTx`, no `OutboundTx`, no `PendingOutbounds` entry is created — demonstrated directly by the analogous existing test `TestPostTxProcessing_WithSyntheticOutboundEvent/"outbound disabled returns error"` (`test/integration/uexecutor/evm_hooks_and_outbound_test.go:576-627`), which shows the same short-circuit behavior for the `IsChainOutboundEnabled` check; the `GetTokenConfigByPRC20` error path at line 66 behaves identically but with no dedicated regression test covering it.
5. Result: the user's PRC20 was burned in step 2, but there is no on-chain record anywhere instructing UVs/TSS to release equivalent value on `eip155:11155111` — the funds are permanently lost with no revert/rescue path (unlike inbound failures, which do get an `INBOUND_REVERT` outbound via `buildRevertOutbound`).

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L59-67)
```go
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
