### Title
`TokenConfig.Enabled` flag is never enforced on the inbound deposit/mint path, making the admin token whitelist unable to stop minting - (File: `x/uregistry/keeper/keeper.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
`x/uregistry` stores a per-token `Enabled` flag (`TokenConfig.Enabled`) that admins can toggle via `MsgUpdateTokenConfig`, intended as the mechanism to pull a token off the whitelist (e.g. during an incident). However, `Keeper.GetTokenConfig` [1](#0-0)  never checks this field, and the `x/uexecutor` inbound execution path that looks up the token config to mint/deposit PRC20 (`k.uregistryKeeper.GetTokenConfig(...)` in `ExecuteInboundFundsAndPayload`) [2](#0-1)  also never checks `tokenConfig.Enabled` before proceeding to deposit/mint. A grep across `x/uexecutor/**/*.go` for `.Enabled` usage shows the field is checked only in `msg_execute_payload.go` and `msg_migrate_uea.go` (chain-level `Enabled`, not `TokenConfig.Enabled`), never in the deposit/mint call sites.

### Finding Description
This is the same bug class as the external report: a permission/whitelist mechanism (`TokenConfig.Enabled`) exists in the data model and is validated for shape in `ValidateBasic` [3](#0-2) , and admins can flip it via `MsgUpdateTokenConfig`, but the consuming code path that actually performs privileged behavior (minting PRC20 to a UEA on inbound) never consults the flag. `GetTokenConfig` is a blind KV lookup with no enabled check [1](#0-0) , and `IsChainInboundEnabled`/`IsChainOutboundEnabled` (which do enforce a similar `Enabled` sub-struct) exist only for `ChainConfig`, not `TokenConfig` [4](#0-3) . `VoteInbound` only gates on chain-level inbound-enabled [5](#0-4)  before proceeding to `ExecuteInbound` → `ExecuteInboundFundsAndPayload`, which fetches the token config purely to resolve the PRC20 contract address for `CallExecuteUniversalTx`/`depositPRC20`, with no gate on `tokenConfig.Enabled` anywhere in that function.

### Impact Explanation
If an admin disables a token (`Enabled=false`) intending to halt further deposits/mints for that asset — the documented purpose of the whitelist per `x/uregistry/README.md` ("Whitelist a token on a chain" / "Remove a token from the whitelist") — an unprivileged user can still submit inbound transfers of that token from the source chain. Honest Universal Validators will still vote and finalize the inbound (chain-level inbound is unaffected), and `ExecuteInboundFundsAndPayload` will still resolve the disabled token's `NativeRepresentation.ContractAddress` and mint/deposit PRC20 for it, because nothing checks `Enabled`. This defeats the intended kill-switch for a compromised or misbehaving token mapping, potentially allowing continued unauthorized minting of a token the admin explicitly tried to freeze.

### Likelihood Explanation
Low-to-moderate. It requires the operational precondition that an admin has toggled `Enabled=false` on an existing `TokenConfig` (rather than removing it entirely via `MsgRemoveTokenConfig`, which would actually delete the row and cause `GetTokenConfig` to fail). Given the field's name and the README's stated intent ("Whitelist a token on a chain" / "Modify a token config"), it is a plausible admin workflow to disable-then-later-remove a token, during which window the flag provides no actual protection. This is a code-level invariant break reachable by any external user submitting a normal inbound transfer — no privileged or malicious-validator assumption is required.

### Recommendation
- **Short term:** Enforce `tokenConfig.Enabled` in the inbound execution path — add a check in `ExecuteInboundFundsAndPayload` (and any other PRC20 deposit/mint call site) immediately after `GetTokenConfig`/`GetTokenConfigByPRC20` lookups, rejecting execution (and routing to the existing revert/failed-PCTx path) if `Enabled == false`.
- **Long term:** Consolidate token-permission checks the same way `IsChainInboundEnabled`/`IsChainOutboundEnabled` do for chains — add an analogous `IsTokenEnabled` keeper helper and require every mint/deposit/execution code path to call it, rather than relying on ad-hoc lookups of `TokenConfig` for unrelated purposes (contract address resolution) that silently bypass the flag.

### Proof of Concept
1. Admin registers a token via `MsgAddTokenConfig` with `Enabled=true` and a `NativeRepresentation.ContractAddress` (PRC20).
2. Users bridge the token in normally; UVs vote `MsgVoteInbound`, ballot finalizes, `ExecuteInboundFundsAndPayload` mints PRC20 to the recipient UEA.
3. Admin discovers an issue with the token/bridge and calls `MsgUpdateTokenConfig` to set `Enabled=false`, expecting no further deposits to be processed.
4. An attacker (or any ordinary user) submits another cross-chain transfer of the same token from the source chain.
5. Honest UVs still observe and vote the inbound (chain-level `IsChainInboundEnabled` is untouched), the ballot finalizes, and `ExecuteInboundFundsAndPayload` calls `k.uregistryKeeper.GetTokenConfig(...)` which returns the row regardless of `Enabled`, then proceeds to mint/deposit PRC20 as usual — the intended freeze has no effect.

Note: I was unable to fully trace every downstream helper (`depositPRC20`, `CallPRC20Deposit`, `CallExecuteUniversalTx` internals) within the available tool budget to rule out an enabled-check deeper in the EVM call chain; the keeper-level and `x/uexecutor` grep results found no such check, but full confirmation would require reading those EVM helper implementations directly (e.g. via a Devin session with full file access, since index size limits may have truncated some files).

### Citations

**File:** x/uregistry/keeper/keeper.go (L195-225)
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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L210-219)
```go
		tokenConfig, tcErr := k.uregistryKeeper.GetTokenConfig(sdkCtx, utx.InboundTx.SourceChain, utx.InboundTx.AssetAddr)

		var contractReceipt *evmtypes.MsgEthereumTxResponse
		var contractErr error
		var feeErr error

		if tcErr != nil {
			contractErr = fmt.Errorf("token config lookup failed: %w", tcErr)
		} else {
			prc20Addr := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
```

**File:** x/uregistry/types/token_config.go (L22-68)
```go
// ValidateBasic performs sanity checks on the TokenConfig
func (p TokenConfig) ValidateBasic() error {
	if strings.TrimSpace(p.Chain) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "chain cannot be empty")
	}

	if strings.TrimSpace(p.Address) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token contract address cannot be empty")
	}

	// Enforce a parseable address for the chain's namespace (e.g. 20-byte hex
	// for eip155, base58 for solana) so every registration lands on the
	// canonical storage key.
	if _, err := utils.CanonicalizeAddressByNamespace(p.Chain, p.Address); err != nil {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid token address for chain %s: %s", p.Chain, err)
	}

	if strings.TrimSpace(p.Name) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token name cannot be empty")
	}

	if strings.TrimSpace(p.Symbol) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "token symbol cannot be empty")
	}

	if p.Decimals == 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "decimals must be greater than zero")
	}

	// Validate token_type is within known enum range
	if _, ok := TokenType_name[int32(p.TokenType)]; !ok {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid token_type: %v", p.TokenType)
	}

	if strings.TrimSpace(p.LiquidityCap) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "liquidity_cap cannot be empty")
	}

	if p.NativeRepresentation == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "native_representation is required")
	}
	if err := p.NativeRepresentation.ValidateBasic(); err != nil {
		return errors.Wrap(err, "invalid native representation")
	}

	return nil
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
