### Title
Universal Validators mint PRC20s for tokens whose registry `Enabled` flag is `false` — `TokenConfig.Enabled` is never checked during inbound execution - ([File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go], [File: x/uexecutor/keeper/execute_inbound_funds_and_payload.go], [File: x/uregistry/keeper/keeper.go])

### Summary
`x/uregistry.TokenConfig` carries an `Enabled` field explicitly documented as "Whether this token is enabled for minting/bridging" [1](#0-0) , and the module README repeats this framing as a whitelist that other modules must respect: "TokenConfigs — token whitelist by chain:address" [2](#0-1) . However, the lookup helper `GetTokenConfig` returns the stored config unconditionally, without checking `Enabled`: [3](#0-2) . Every inbound-execution code path in `x/uexecutor` — `ExecuteInboundGas`, `ExecuteInboundGasAndPayload`, and `ExecuteInboundFundsAndPayload` — calls this exact helper and, if it returns without error, proceeds straight to minting/depositing PRC20, with no subsequent check of `tokenConfig.Enabled` [4](#0-3) [5](#0-4) [6](#0-5) . A grep of the entire `x/uexecutor/keeper` package for `.Enabled` shows the field is checked only for chain-level inbound/outbound gating (`msg_vote_inbound.go`, `create_outbound.go`) — never for the token-level `Enabled` flag.

### Finding Description
This is the direct native analog of the reported ArbitrageManager/LiquidityManager issue: a downstream component (`LiquidityManager.depositToClosePreRelease`) accepted any token forwarded to it without verifying it was an actually-managed/whitelisted token. On Push Chain, the equivalent "acceptance gate" is `uregistry.TokenConfig.Enabled`, which is meant to be the single source of truth for "is this token allowed to be minted/bridged." The inbound execution pipeline (`VoteInbound` → `ExecuteInbound*`) is the code that is supposed to enforce that gate before minting PRC20 on behalf of a cross-chain deposit, but it only checks *existence* of a `TokenConfig` row via `GetTokenConfig`, never its `Enabled` bit.

Concretely:
- `VoteInbound` only checks chain-level `IsChainInboundEnabled` before allowing a ballot to proceed [7](#0-6) .
- `ValidateForExecution` only checks that `AssetAddr` is non-empty, not that the corresponding `TokenConfig` is enabled [8](#0-7) .
- `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`/`ExecuteInboundFundsAndPayload` fetch `TokenConfig` purely to resolve the PRC20 contract address (`tokenConfig.NativeRepresentation.ContractAddress`) and mint against it; the `Enabled` field is read from storage but discarded.

So any `TokenConfig` entry that exists in the registry — even one an admin has deliberately flipped to `Enabled=false` (deprecated, paused due to an exploit, or added-but-not-yet-activated) — is fully usable for inbound bridging and PRC20 minting by ordinary users, exactly the "token the project does not currently manage/support" scenario the audit report flags for the EVM contracts.

### Impact Explanation
This corrupts token-mapping/accounting invariants: PRC20 tokens can be minted for assets the registry admin has explicitly disabled. Practically this enables: (a) reactivating bridging for a token an admin paused after discovering a bug or depeg, defeating the purpose of the pause; (b) triggering unauthorized minting for a token added to the registry in a non-active/staging state before its liquidity cap, decimals, or native representation were finalized for production use — undermining the admin-curated whitelist invariant described in `x/uregistry/README.md`. This falls under "unauthorized mint ... of user or protocol-controlled funds" and "corruption of PRC20 or native asset accounting ... token mapping" in the allowed impact gate.

### Likelihood Explanation
Trigger requires only an ordinary, unprivileged user submitting a deposit on the source chain for a token that has a `TokenConfig` row present but `Enabled=false`, and honest Universal Validators observing/relaying that event exactly as they do for any other inbound — no malicious validator or admin action needed. The only precondition is that such a `TokenConfig` row exists (which is realistic any time an admin disables a previously-enabled token, or stages one prior to activation), making this readily reachable through the default `VoteInbound`/`ExecuteInbound` transaction path.

### Recommendation
Add an explicit `tokenConfig.Enabled` check immediately after every `GetTokenConfig` call in the inbound execution paths (`ExecuteInboundGas`, `ExecuteInboundGasAndPayload`, `ExecuteInboundFundsAndPayload`, and the smart-contract branch in `execute_inbound_funds_and_payload.go`), failing execution (and triggering the existing revert/failed-PCTx bookkeeping) when the token is disabled — mirroring the post-audit fix applied to `LiquidityManager.depositToClosePreRelease`, which now checks that only managed/expected tokens are accepted.

### Proof of Concept
1. Admin registers a token via `MsgAddTokenConfig` with `Enabled=true`, then later disables it via `MsgUpdateTokenConfig` with `Enabled=false` (e.g., because of a discovered issue on the source-chain token contract), leaving the `TokenConfig` row present in `uregistry.TokenConfigs` (this matches the existing test fixtures that build `TokenConfig{..., Enabled: true/false, ...}` [9](#0-8) ).
2. An ordinary user deposits that disabled token's external asset into the gateway on the source chain exactly as with any normal deposit.
3. Universal Validators observe the deposit and submit `MsgVoteInbound` honestly, as in the existing integration test flow [10](#0-9) .
4. `VoteInbound` finalizes the ballot (chain inbound is enabled, so no chain-level block); `ExecuteInboundGas`/`ExecuteInboundFundsAndPayload` calls `GetTokenConfig`, which succeeds and returns the config with `Enabled=false` still populated [3](#0-2) .
5. Execution proceeds to `depositPRC20`/`gasAndPayloadDepositAutoSwap` and mints PRC20 to the user's UEA, with no check ever inspecting `tokenConfig.Enabled` — the deposit succeeds despite the admin having disabled the token.

(Note: I was unable to fully inspect the body of `depositPRC20` in `x/uexecutor/keeper/handler.go` due to iteration limits, but all three top-level `ExecuteInbound*` entry points that call it were confirmed to skip the `Enabled` check before invoking it, which is sufficient to establish the missing gate.)

### Citations

**File:** proto/uregistry/v1/types.proto (L140-140)
```text
  bool enabled = 6;                        // Whether this token is enabled for minting/bridging
```

**File:** app/README.md (L99-101)
```markdown
**State**
- `ChainConfigs` — per-CAIP-2 chain config (RPC URL, gateway, vault methods, block confirmations, inbound/outbound enabled flags, gas oracle interval)
- `TokenConfigs` — token whitelist by `chain:address`, with native representation, decimals, and liquidity cap
```

**File:** x/uregistry/keeper/keeper.go (L227-234)
```go
func (k Keeper) GetTokenConfig(ctx context.Context, chain, address string) (types.TokenConfig, error) {
	storageKey := types.GetTokenConfigsStorageKey(chain, address)
	config, err := k.TokenConfigs.Get(ctx, storageKey)
	if err != nil {
		return types.TokenConfig{}, err
	}
	return config, nil
}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L39-54)
```go
	// --- step 1: get token config
	tokenConfig, err := k.uregistryKeeper.GetTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil {
		execErr = fmt.Errorf("GetTokenConfig failed: %w", err)
		shouldRevert = true
		revertReason = execErr.Error()
	} else {
		// --- step 2: parse amount
		amount := new(big.Int)
		if amount, ok := amount.SetString(inbound.Amount, 10); !ok {
			execErr = fmt.Errorf("invalid amount: %s", inbound.Amount)
			shouldRevert = true
			revertReason = execErr.Error()
		} else {
			// --- step 3: resolve / deploy UEA
			prc20AddressHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L208-219)
```go
	// Smart contract path: call executeUniversalTx and return
	if isSmartContract {
		tokenConfig, tcErr := k.uregistryKeeper.GetTokenConfig(sdkCtx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)

		var contractReceipt *evmtypes.MsgEthereumTxResponse
		var contractErr error
		var feeErr error

		if tcErr != nil {
			contractErr = fmt.Errorf("token config lookup failed: %w", tcErr)
		} else {
			prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
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

**File:** x/uexecutor/types/inbound.go (L140-143)
```go
	// Validate asset_addr
	if strings.TrimSpace(p.AssetAddr) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "asset_addr cannot be empty")
	}
```

**File:** x/uregistry/types/token_config_test.go (L22-36)
```go
		{
			name: "valid token config",
			config: types.TokenConfig{
				Chain:                "eip155:1",
				Address:              "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
				Name:                 "USD Coin",
				Symbol:               "USDC",
				Decimals:             6,
				Enabled:              true,
				LiquidityCap:         "1000000000000000000000000",
				TokenType:            types.TokenType_ERC20,
				NativeRepresentation: validNative,
			},
			expectErr: false,
		},
```

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L366-392)
```go
	t.Run("GAS inbound with missing token config records FAILED PCTx and creates revert", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals := setupInboundGasTest(t, 4)

		inbound.TxHash = "0xgas0020"

		// Remove token config to force GetTokenConfig to fail
		chainApp.UregistryKeeper.RemoveTokenConfig(ctx, inbound.SourceChain, inbound.AssetAddr)

		reachGasQuorum(t, ctx, chainApp, vals, coreVals, inbound, 3)

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found, "universal tx should exist even when token config is missing")

		// Must have a FAILED PCTx
		require.NotEmpty(t, utx.PcTx, "PCTx entries must be recorded")
		hasFailed := false
		for _, pcTx := range utx.PcTx {
			if pcTx.Status == "FAILED" {
				hasFailed = true
				require.Contains(t, pcTx.ErrorMsg, "GetTokenConfig failed",
					"error message should indicate token config lookup failure")
				break
			}
		}
		require.True(t, hasFailed, "should have a FAILED PCTx when token config is missing")
```
