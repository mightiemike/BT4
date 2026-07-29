### Title
TokenConfig.Enabled is never enforced in PRC20 deposit/outbound paths, letting disabled tokens keep minting via inbound/outbound flows - (File: x/uexecutor/keeper/handler.go)

### Summary
`x/uregistry`'s `TokenConfig` has an `Enabled` flag intended to let governance pause/delist a token (e.g., after discovering it's compromised, mis-configured, or should be delisted) without removing the whole chain. However, none of the value-moving code paths in `x/uexecutor/keeper` that look up a `TokenConfig` ever check this flag — they only check chain-level `ChainConfig.Enabled` (`IsChainInboundEnabled`/`IsChainOutboundEnabled`). This is the same class of bug as the Aave/Compound report: a governance "disable" switch exists, but a separate write path (here, PRC20 deposit/mint and outbound creation) bypasses it and keeps moving value for the disabled entity.

### Finding Description
`TokenConfig.Enabled` is defined in the registry schema [1](#0-0)  and is meant to gate whether a token is usable, analogous to `ChainConfig.Enabled` gating inbound/outbound per chain.

`x/uregistry` exposes `IsChainInboundEnabled`/`IsChainOutboundEnabled` which are actively checked, but there is no equivalent `IsTokenEnabled` helper exported or consumed anywhere in `x/uexecutor`: [2](#0-1) 

Every call site in `x/uexecutor/keeper` that fetches a `TokenConfig` only uses it to resolve the PRC20 contract address, never inspecting `Enabled`:
- `depositPRC20`, the core function used by `ExecuteInboundFunds`, `ExecuteInboundFundsAndPayload`, and `ExecuteInboundGasAndPayload` to mint PRC20 into a user's UEA, fetches `TokenConfig` and immediately proceeds to `CallPRC20Deposit` with no `Enabled` check: [3](#0-2) 
- `buildRevertOutbound`, which re-mints tokens back to the sender on a failed inbound, likewise fetches `TokenConfig` only to read `NativeRepresentation.ContractAddress`, with no enabled check: [4](#0-3) 
- `BuildOutboundsFromReceipt`, which creates withdrawal outbounds from a `UniversalTxOutbound` EVM event, explicitly checks `IsChainOutboundEnabled` for the destination chain but resolves the token via `GetTokenConfigByPRC20` without checking token `Enabled`: [5](#0-4) 
- `gasAndPayloadDepositAutoSwap` (used by `ExecuteInboundGasAndPayload`) resolves `tokenConfig.NativeRepresentation.ContractAddress` for the swap/deposit without any enabled gate: [6](#0-5) 

By contrast, chain-level enable/disable is consistently and correctly enforced at the entry points (`VoteInbound`, `ExecutePayload`, `BuildOutboundsFromReceipt`): [7](#0-6)  This asymmetry — chain-level disable enforced, token-level disable never enforced — mirrors the Aave/Compound report's core defect: a governance kill-switch exists to stop new positions/deposits, but a separate code path (delta-matching there, PRC20 deposit/mint here) doesn't check it and keeps creating the same effect the switch was meant to prevent.

### Impact Explanation
If governance/admin sets `TokenConfig.Enabled = false` for a token on a chain (e.g., to stop deposits of a token found to be compromised, mispriced, or intentionally being delisted from the registry), unprivileged users can still submit ordinary `FUNDS`, `FUNDS_AND_PAYLOAD`, or `GAS_AND_PAYLOAD` inbounds referencing that token's `AssetAddr`. Since chain-level inbound is a separate flag and honest validators only gate on `IsChainInboundEnabled` at `VoteInbound`, the ballot finalizes normally, and the keeper proceeds straight into `depositPRC20`/`CallPRC20Deposit`, minting the disabled token's PRC20 representation regardless of the disabled state. The same gap applies to revert-minting (`buildRevertOutbound`) and outbound withdrawal creation (`BuildOutboundsFromReceipt`), so a disabled token continues to have PRC20 accounting mutated (mint on deposit, mint on revert, and withdrawal outbound creation) purely through ordinary user-submitted cross-chain transactions with honest validators. This corrupts PRC20/native asset accounting for a token administrators explicitly intended to freeze, defeating the purpose of the `Enabled` flag.

### Likelihood Explanation
High likelihood of triggerability: it requires no privileged action or malicious validator collusion — only an ordinary external-chain deposit event that honest Universal Validators observe and vote on as usual. The only precondition is that an admin has toggled `TokenConfig.Enabled = false` for that token while leaving `ChainConfig.Enabled.IsInboundEnabled = true` (a realistic operational scenario, since token-level and chain-level toggles are separate admin messages: `MsgUpdateTokenConfig` vs `MsgUpdateChainConfig`).

### Recommendation
Add an `IsTokenEnabled`-style check (mirroring `IsChainInboundEnabled`/`IsChainOutboundEnabled`) in `x/uregistry/keeper`, and enforce it at every uexecutor value-moving entry point that resolves a `TokenConfig`:
- `depositPRC20` (`x/uexecutor/keeper/handler.go`) before calling `CallPRC20Deposit`.
- `buildRevertOutbound` (`x/uexecutor/keeper/build_revert_outbound.go`) before minting funds back on revert (or handle gracefully if disabled mid-flight).
- `BuildOutboundsFromReceipt` (`x/uexecutor/keeper/create_outbound.go`) alongside the existing `IsChainOutboundEnabled` check.
- `gasAndPayloadDepositAutoSwap` / `ExecuteInboundGasAndPayload` before executing the autoswap deposit.

### Proof of Concept
1. Admin registers `TokenConfig{Chain: "eip155:X", Address: TOKEN, Enabled: true, NativeRepresentation: {ContractAddress: PRC20}}` and `ChainConfig{Chain: "eip155:X", Enabled: {IsInboundEnabled: true}}`.
2. Admin later sets `TokenConfig.Enabled = false` via `MsgUpdateTokenConfig` to pause the token (chain inbound remains enabled).
3. An external-chain user submits a normal `FUNDS` deposit for `TOKEN`; Universal Validators observe it honestly and call `MsgVoteInbound`.
4. `VoteInbound` only checks `IsChainInboundEnabled("eip155:X")` (true) — it never checks token `Enabled` [7](#0-6) .
5. On quorum, `ExecuteInboundFunds` → `depositPRC20` fetches the (disabled) `TokenConfig` and calls `CallPRC20Deposit`, minting PRC20 to the recipient despite `Enabled = false` [3](#0-2) .

Note: I could not fully verify within tool-call limits whether `msg_execute_payload.go`'s single `Enabled`-related match refers to a `TokenConfig.Enabled` check or something unrelated (e.g., a different config flag); if it does check token-enabled status only in that one payload path, the finding would need to be scoped to the remaining (unchecked) paths rather than a total lack of any check. A Devin session with full repo access should confirm this specific reference before finalizing severity.

### Citations

**File:** test/integration/uexecutor/chain_enabled_test.go (L59-72)
```go
	tokenConfig := uregistrytypes.TokenConfig{
		Chain:        "eip155:11155111",
		Address:      usdcAddress.String(),
		Name:         "USD Coin",
		Symbol:       "USDC",
		Decimals:     6,
		Enabled:      true,
		LiquidityCap: "1000000000000000000000000",
		TokenType:    1,
		NativeRepresentation: &uregistrytypes.NativeRepresentation{
			Denom:           "",
			ContractAddress: prc20Address.String(),
		},
	}
```

**File:** x/uregistry/keeper/keeper.go (L195-234)
```go
// IsChainInboundEnabled checks if inbound is enabled for a given chain
func (k Keeper) IsChainInboundEnabled(ctx context.Context, chain string) (bool, error) {
	config, err := k.GetChainConfig(ctx, chain)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			// chain not found
			return false, nil
		}
		return false, err
	}
	if config.Enabled == nil {
		return false, nil
	}
	return config.Enabled.IsInboundEnabled, nil
}

// IsChainOutboundEnabled checks if outbound is enabled for a given chain
func (k Keeper) IsChainOutboundEnabled(ctx context.Context, chain string) (bool, error) {
	config, err := k.GetChainConfig(ctx, chain)
	if err != nil {
		if errors.Is(err, collections.ErrNotFound) {
			// chain not found
			return false, nil
		}
		return false, err
	}
	if config.Enabled == nil {
		return false, nil
	}
	return config.Enabled.IsOutboundEnabled, nil
}

func (k Keeper) GetTokenConfig(ctx context.Context, chain, address string) (types.TokenConfig, error) {
	storageKey := types.GetTokenConfigsStorageKey(chain, address)
	config, err := k.TokenConfigs.Get(ctx, storageKey)
	if err != nil {
		return types.TokenConfig{}, err
	}
	return config, nil
}
```

**File:** x/uexecutor/keeper/handler.go (L12-46)
```go
func (k Keeper) depositPRC20(
	ctx sdk.Context,
	sourceChain string,
	assetAddr string,
	recipient common.Address,
	amountStr string,
) (*vmtypes.MsgEthereumTxResponse, error) {
	// get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, sourceChain, assetAddr)
	if err != nil {
		return nil, err
	}

	if tokenConfig.NativeRepresentation == nil {
		return nil, fmt.Errorf("token config for %s:%s has no native representation", sourceChain, assetAddr)
	}
	prc20Address := tokenConfig.NativeRepresentation.ContractAddress
	prc20AddressHex := common.HexToAddress(prc20Address)

	// convert amount
	amount := new(big.Int)
	amount, ok := amount.SetString(amountStr, 10)
	if !ok {
		return nil, fmt.Errorf("invalid amount: %s", amountStr)
	}

	k.Logger().Debug("EVM call: depositPRC20Token",
		"prc20", prc20AddressHex.Hex(),
		"recipient", recipient.Hex(),
		"amount", amountStr,
	)

	// call PRC20 deposit
	return k.CallPRC20Deposit(ctx, prc20AddressHex, recipient, amount)
}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L27-48)
```go
	// Look up the PRC20 address for this external token
	tokenCfg, err := k.uregistryKeeper.GetTokenConfig(sdkCtx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil || tokenCfg.NativeRepresentation == nil || tokenCfg.NativeRepresentation.ContractAddress == "" {
		k.Logger().Warn("failed to get PRC20 for revert outbound gas lookup, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"asset", inbound.AssetAddr,
			"error", err,
		)
		return outbound
	}

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
```

**File:** x/uexecutor/keeper/create_outbound.go (L49-67)
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L47-53)
```go
	// --- Step 1: token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
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
