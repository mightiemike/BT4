## Title
Gas fees for outbound/revert transactions on a newly-registered (not-yet-bootstrapped) chain resolve to a zero `gasPrice`/`gasFee`, causing under-collected or free outbound execution - (File: `x/uexecutor/keeper/gas_fee.go`, `x/uexecutor/keeper/build_revert_outbound.go`, `x/uexecutor/keeper/chain_meta.go`)

### Summary
The external report describes a TWAP oracle that returns `0` for a newly-registered asset because the default zero value of an uninitialized accumulator is blindly used in a downstream computation instead of being excluded until real data exists. Push Chain has a structurally identical pattern in the chain-meta (gas price) oracle: `VoteChainMeta` in `x/uexecutor/keeper/chain_meta.go` requires `chainMetaMinVotesForFirstWrite` fresh votes before writing a chain's gas price into the `UniversalCore` EVM contract, so `gasPriceByChainNamespace(chainId)` returns the EVM storage default (`0`) for any chain that is enabled for inbound/outbound but has not yet accumulated enough fresh validator votes. That zero value is read directly, without a nonzero check, by `GetOutboundTxGasAndFees` / `GetGasFeeInfoForRevertOutbound` (`x/uexecutor/keeper/gas_fee.go`) and used to populate `OutboundTx.GasFee` / `GasPrice` / `GasLimit` in `buildRevertOutbound` (`x/uexecutor/keeper/build_revert_outbound.go`).

### Finding Description
`VoteChainMeta` (`x/uexecutor/keeper/chain_meta.go:62-189`) implements a cold-start gate: [1](#0-0) . Until `chainMetaMinVotesForFirstWrite` fresh votes are collected, no `CallUniversalCoreSetChainMeta` EVM write happens, so the `UniversalCore` contract's on-chain `gasPriceByChainNamespace` mapping for that chain stays at its Solidity default value of `0`.

`GetOutboundTxGasAndFees` reads `gasPrice`, `gasFee`, and `gasLimit` straight from that contract call with no validation that the returned values are non-zero: [2](#0-1) . This is used both for real outbound creation and for revert-outbound construction: [3](#0-2) .

When a chain is added via `MsgAddChainConfig`/`MsgUpdateChainConfig` with inbound/outbound enabled, and inbound deposits start flowing before the Universal Validators' `ChainMetaOracle` fetch-and-vote loop has accumulated the bootstrap quorum of fresh votes (a window that is inherent to any newly enabled chain, and also re-occurs any time the fresh-vote window empties out due to staleness, since `VoteChainMeta` treats a chain that dropped back below the fresh-vote threshold the same as never-bootstrapped for the gate check only on `LastAppliedChainHeight == 0`), any inbound that needs a revert (`buildRevertOutbound`) or an outbound created via the gateway event path silently receives `gasPrice = 0`, `gasFee = 0` from `GetOutboundTxGasAndFees`, because there is no guard rejecting a zero price the way the TWAP report recommends (`require(price != 0)`).

This exactly mirrors the reported bug class: a value that legitimately defaults to `0` before any real observation exists is consumed downstream as if it were a valid, current market price, skewing subsequent computations (there, a weighted price sum; here, an outbound's gas-fee/gas-price/gas-limit fields).

### Impact Explanation
An `OutboundTx` created with `GasFee = "0"` and `GasPrice = "0"` means the protocol reserves no funds to compensate for the real gas cost of executing the transaction on the destination external chain. Concretely:
- For `INBOUND_REVERT` outbounds (`buildRevertOutbound`), the reverted funds are sent back to the user's `recipient` without any gas-fee deduction, while the TSS/relayer infrastructure still incurs real gas cost to broadcast the transaction on the external chain — the vault/protocol effectively refunds the full amount with none earmarked for gas, corrupting gas-fee accounting for that outbound.
- Because `applyGasRefund` (`x/uexecutor/keeper/outbound.go`) only refunds `gasFee - gasFeeUsed` when `gasFee != ""` and positive, a `gasFee = "0"` outbound skips refund logic and there is no compensating mechanism to correct the shortfall after the fact.
- This is reachable by an ordinary, unprivileged user simply sending a deposit on a freshly-registered/-enabled external chain before the Universal Validator set has produced the bootstrap quorum of fresh chain-meta votes — no privileged action beyond the (out-of-scope) admin's normal chain-onboarding step is required to trigger the zero-price window.

The impact falls under "corruption of ... gas fee accounting, refund accounting ... or canonical UniversalTx state" in the allowed-impact gate, since the wrong (zero) `GasFee`/`GasPrice`/`GasLimit` values become the canonical, immutable fields of the `OutboundTx` record once created.

### Likelihood Explanation
Likelihood is limited to a narrow bootstrap window that only occurs right after a chain is newly enabled (or after all `ChainMeta` votes go stale simultaneously, since staleness pruning can reduce fresh votes below the bootstrap threshold again in edge cases). During that window, any unprivileged user's inbound/outbound activity on that chain automatically hits the zero-price path — no attacker crafting or race condition beyond ordinary timing is required, but the window is transient (closes once ≥`chainMetaMinVotesForFirstWrite` fresh votes land) and depends on how quickly Universal Validators pick up and vote on the newly added chain.

### Recommendation
In `GetOutboundTxGasAndFees` (`x/uexecutor/keeper/gas_fee.go`), reject or defer outbound/revert construction when the returned `gasPrice` (and/or `gasFee`) is zero, mirroring the report's recommended `require(price != 0)` check, e.g.:
```go
if gasPrice.Sign() == 0 || gasFee.Sign() == 0 {
    return nil, fmt.Errorf("gas price oracle not yet bootstrapped for this chain")
}
```
`buildRevertOutbound` already has an error-handling path (`k.Logger().Warn(...); return outbound` without gas fields) for failures — route the zero-price case through that same "proceed without gas fields" / retry-later path instead of silently accepting a zero-fee outbound, or block outbound creation entirely until the chain-meta oracle is bootstrapped for that `chainId`.

### Proof of Concept
1. Admin adds a new `ChainConfig` (e.g., a new EVM chain) with `IsInboundEnabled = true`, `IsOutboundEnabled = true`, and registers a `TokenConfig` with a `NativeRepresentation.ContractAddress` (PRC20) for it — this is normal onboarding, not itself part of the attack.
2. Before ≥`chainMetaMinVotesForFirstWrite` Universal Validators have submitted fresh `MsgVoteChainMeta` votes for this new `observedChainId` (a window on the order of the oracle's fetch interval, confirmed by the "votes below bootstrap quorum store but do not bootstrap oracle" test: [4](#0-3) ), an unprivileged user submits a deposit on the new chain that later needs to be reverted (e.g., a payload execution failure) — a completely ordinary, permissionless user action.
3. `buildRevertOutbound` calls `GetGasFeeInfoForRevertOutbound` → `GetOutboundTxGasAndFees`, which calls `UniversalCore.getOutboundTxGasAndFees`. Since `gasPriceByChainNamespace(chainId)` is still the EVM storage default of `0` (confirmed by `TestVoteChainMetaContractState`, which shows the mapping is populated only after 3 votes: [5](#0-4) ), the contract returns `gasPrice = 0`, and downstream `gasFee` derived from it is also `0`.
4. The resulting `OutboundTx.GasFee = "0"`, `OutboundTx.GasPrice = "0"` is persisted as canonical state and later signed/broadcast by TSS with no gas-fee reservation, corrupting the outbound's gas-fee accounting and leaving no funds earmarked to cover the real-world execution cost.

### Citations

**File:** x/uexecutor/keeper/chain_meta.go (L129-143)
```go
	// Cold-start gate: the first EVM write requires at least N fresh votes
	// so the oracle is never defined by a single validator. Once bootstrapped,
	// the existing fresh-votes-median path handles every subsequent vote.
	if !bootstrapped && len(fresh) < chainMetaMinVotesForFirstWrite {
		k.Logger().Info("chain meta vote recorded, awaiting bootstrap quorum",
			"chain_id", observedChainId,
			"validator", universalValidator.String(),
			"have_fresh_votes", len(fresh),
			"need_fresh_votes", chainMetaMinVotesForFirstWrite,
		)
		if err := k.SetChainMeta(ctx, observedChainId, entry); err != nil {
			return sdkerrors.Wrap(err, "failed to set chain meta entry during bootstrap")
		}
		return nil
	}
```

**File:** x/uexecutor/keeper/gas_fee.go (L26-63)
```go
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

	gasToken := results[0].(common.Address)
	gasFee := results[1].(*big.Int)
	// protocolFee := results[2].(*big.Int) — not needed for outbound fields
	gasPrice := results[3].(*big.Int)
	// chainNamespace := results[4].(string) — not needed for outbound fields
	// gasLimitUsed (results[5]) is the exact gas limit the contract resolved
	// (caller-supplied or per-chain baseGasLimitByChainNamespace fallback).
	// Reading it directly avoids the gasFee/gasPrice round-trip and keeps us
	// in lock-step with the contract's own resolution.
	gasLimit := results[5].(*big.Int)

	return &GasFeeInfo{
		GasToken: gasToken,
		GasFee:   gasFee,
		GasPrice: gasPrice,
		GasLimit: gasLimit,
	}, nil
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L38-53)
```go
	// Fetch gas fields from UniversalCore.getOutboundTxGasAndFees(prc20, 0)
	// 0 means use the contract's baseLimit for this chain
	gasToken, gasFee, gasPrice, gasLimit, err := k.GetGasFeeInfoForRevertOutbound(sdkCtx, tokenCfg.NativeRepresentation.ContractAddress)
	if err != nil {
		k.Logger().Warn("failed to fetch gas fee info for revert outbound, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"prc20", tokenCfg.NativeRepresentation.ContractAddress,
			"error", err,
		)
		return outbound
	}

	outbound.GasToken = gasToken
	outbound.GasFee = gasFee
	outbound.GasPrice = gasPrice
	outbound.GasLimit = gasLimit
```

**File:** test/integration/uexecutor/vote_chain_meta_test.go (L60-85)
```go
	t.Run("votes below bootstrap quorum store but do not bootstrap oracle", func(t *testing.T) {
		// With chainMetaMinVotesForFirstWrite = 3, votes 1 and 2 are recorded
		// in state but do NOT trigger an EVM oracle write. LastAppliedChainHeight
		// stays 0 until the third fresh vote accumulates.
		testApp, ctx, uvals, vals := setupVoteChainMetaTest(t, 2)

		coreAccs := make([]string, 2)
		for i := range vals {
			coreVal, _ := sdk.ValAddressFromBech32(vals[i].OperatorAddress)
			coreAccs[i] = sdk.AccAddress(coreVal).String()
		}

		// Vote 1
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[0], coreAccs[0], chainId, 100_000_000_000, 12345))
		stored, found, err := testApp.UexecutorKeeper.GetChainMeta(ctx, chainId)
		require.NoError(t, err)
		require.True(t, found)
		require.Len(t, stored.Prices, 1)
		require.Equal(t, uint64(0), stored.LastAppliedChainHeight, "single vote should not bootstrap the oracle")

		// Vote 2
		require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[1], coreAccs[1], chainId, 200_000_000_000, 12346))
		stored, _, _ = testApp.UexecutorKeeper.GetChainMeta(ctx, chainId)
		require.Len(t, stored.Prices, 2)
		require.Equal(t, uint64(0), stored.LastAppliedChainHeight, "two votes should still not bootstrap the oracle")
	})
```

**File:** test/integration/uexecutor/vote_chain_meta_test.go (L382-399)
```go
	// Three agreeing votes → median == voted values, oracle is written.
	require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[0], coreAccs[0], chainId, price, height))
	require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[1], coreAccs[1], chainId, price, height))
	require.NoError(t, utils.ExecVoteChainMeta(t, ctx, testApp, uvals[2], coreAccs[2], chainId, price, height))

	// Read from the UniversalCore contract using the public mapping getters
	universalCoreAddr := utils.GetDefaultAddresses().HandlerAddr
	ucABI, err := uexecutortypes.ParseUniversalCoreABI()
	require.NoError(t, err)

	caller, _ := testApp.UexecutorKeeper.GetUeModuleAddress(ctx)

	t.Run("gasPriceByChainNamespace matches voted price", func(t *testing.T) {
		res, err := testApp.EVMKeeper.CallEVM(ctx, ucABI, caller, universalCoreAddr, false, nil, "gasPriceByChainNamespace", chainId)
		require.NoError(t, err)
		got := new(big.Int).SetBytes(res.Ret)
		require.Equal(t, new(big.Int).SetUint64(price), got)
	})
```
