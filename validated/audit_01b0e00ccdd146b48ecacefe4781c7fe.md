Confirmed: none of `x/uexecutor`'s inbound-execution code paths (`execute_inbound_gas_and_payload.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas.go`, `create_outbound.go`, `build_revert_outbound.go`, `handler.go`) reference `tokenConfig.Enabled` anywhere — only `x/uregistry/keeper/keeper.go` even touches `.Enabled`, and there it's only for `ChainConfig.Enabled` (`IsChainInboundEnabled` / `IsChainOutboundEnabled`), never for `TokenConfig.Enabled`.

### Title
Disabled/blacklisted `TokenConfig.Enabled` is never enforced before minting PRC20 or creating outbound transfers - (File: `x/uregistry/keeper/keeper.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/create_outbound.go`)

### Summary
`TokenConfig` carries an `Enabled` field explicitly documented as "Whether this token is enabled for minting/bridging" [1](#0-0) . The admin uses `MsgUpdateTokenConfig`/`MsgRemoveTokenConfig` to disable or delist a token, mirroring the "blacklist" pattern in the referenced report. However, `Keeper.GetTokenConfig` and `Keeper.GetTokenConfigByPRC20` in `x/uregistry/keeper/keeper.go` return the config unconditionally without checking `Enabled`, and every consumer in `x/uexecutor` (inbound minting/deposit-autoswap and outbound creation) uses these lookups directly without ever inspecting the flag.

### Finding Description
- `GetTokenConfig` and `GetTokenConfigByPRC20` fetch the stored `TokenConfig` and return it as-is: [2](#0-1) [3](#0-2) . Unlike `IsChainInboundEnabled`/`IsChainOutboundEnabled`, which gate on `ChainConfig.Enabled`, there is no `IsTokenEnabled` equivalent and no check anywhere against `TokenConfig.Enabled`.
- `x/uexecutor/keeper/execute_inbound_gas_and_payload.go::ExecuteInboundGasAndPayload` calls `k.uregistryKeeper.GetTokenConfig(...)` and, as long as the lookup succeeds, proceeds straight to `gasAndPayloadDepositAutoSwap` → `CallPRC20DepositAutoSwap`, minting/depositing PRC20 into the recipient UEA, with no `Enabled` check in between: [4](#0-3) [5](#0-4) .
- Symmetrically, `x/uexecutor/keeper/create_outbound.go::BuildOutboundsFromReceipt` checks `IsChainOutboundEnabled` for the destination chain but calls `GetTokenConfigByPRC20` afterward purely to resolve the external asset address — with no equivalent per-token enabled check before constructing and queuing the `OutboundTx`: [6](#0-5) .
- The chain-level enabled check (`chainConfig.Enabled.IsInboundEnabled`) is enforced in `ExecutePayload` [7](#0-6) , confirming the codebase's pattern is to gate execution on config flags — but this pattern was not replicated for `TokenConfig.Enabled`, exactly the same class of omission as `DelegatorFactory::create` failing to check `blacklisted[type_]`.
- `UpdateTokenConfig` happily flips `Enabled` to `false` (e.g., to delist a compromised/deprecated token) but the flag is inert for any already-in-flight or newly submitted inbound using that token/chain pair: [8](#0-7) .

### Impact Explanation
An admin-disabled token (e.g., one being delisted due to a bridge exploit, depeg, or liquidity-cap breach) can still be used by an unprivileged user to submit/vote a valid `Inbound` (as long as the *chain* itself remains inbound-enabled) and have its PRC20 minted/deposited via `gasAndPayloadDepositAutoSwap`/`CallPRC20DepositAutoSwap`, and to have outbound transfers built and queued for that asset. This directly violates the intended token-level safety gate, allowing minting of PRC20 for a token administrators explicitly tried to freeze, and letting outbound funds continue to move for a delisted token. This maps to "unauthorized mint" / "corruption of PRC20 accounting" / "unauthorized module-originated EVM execution" in the allowed-impact list, reachable by an ordinary unprivileged user simply submitting an inbound/outbound for a token the admin has disabled.

### Likelihood Explanation
High likelihood of reachability: any user causing an inbound event referencing a disabled token's `AssetAddr`/chain pair triggers this path with no privileged action required beyond the admin having previously disabled the token (a legitimate governance action, not an attacker action). The only friction is that `Enabled=false` doesn't stop `GetTokenConfig` from succeeding — it's a pure logic gap, not a race condition, so it is deterministic and always exploitable once a token is disabled while the underlying chain remains active.

### Recommendation
Add explicit `Enabled` checks in `x/uregistry/keeper/keeper.go` (e.g., an `IsTokenEnabled` helper mirroring `IsChainInboundEnabled`) and call it from `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas.go`, and `create_outbound.go`/`BuildOutboundsFromReceipt` before any mint/deposit/outbound-creation logic runs, returning a descriptive error (and routing to the existing revert/failed-PCTx flow) analogous to how `IsChainInboundEnabled`/`IsChainOutboundEnabled` are already enforced.

### Proof of Concept
1. Admin registers a chain and token via `MsgAddChainConfig`/`MsgAddTokenConfig` with `TokenConfig.Enabled = true`.
2. Admin later calls `MsgUpdateTokenConfig` setting `Enabled = false` for that token (e.g., due to a discovered vulnerability in the source-chain token contract), while `ChainConfig.Enabled.IsInboundEnabled` remains `true`.
3. An unprivileged user (or the normal inbound-voting flow) submits/votes an `Inbound` referencing that disabled token's `AssetAddr` on that chain.
4. `ExecuteInboundGasAndPayload` calls `GetTokenConfig`, which succeeds (no `Enabled` check), and proceeds to `gasAndPayloadDepositAutoSwap` → mints/deposits the PRC20 into the recipient UEA — despite the token being explicitly disabled.

Note: I was not able to fully trace every downstream helper (`CallPRC20DepositAutoSwap`, `execute_inbound_funds_and_payload.go`, `execute_inbound_gas.go`) in complete detail due to index scope limits; if deeper verification of these files is needed, a full Devin session with repository access would allow confirming there is no indirect `Enabled` check elsewhere in those files.

### Citations

**File:** proto/uregistry/v1/types.proto (L140-140)
```text
  bool enabled = 6;                        // Whether this token is enabled for minting/bridging
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

**File:** x/uregistry/keeper/keeper.go (L242-276)
```go
func (k Keeper) GetTokenConfigByPRC20(
	ctx context.Context,
	chain string,
	prc20Addr string,
) (types.TokenConfig, error) {

	if strings.TrimSpace(prc20Addr) == "" {
		return types.TokenConfig{}, fmt.Errorf("prc20 address is empty")
	}
	// Same canonical form as the index function, so any case variant hits the row.
	prc20Addr = canonicalPRC20(prc20Addr)

	// PRC20 addresses are globally unique by construction; MatchExact returns at most one.
	iter, err := k.TokenConfigs.Indexes.PRC20Index.MatchExact(ctx, prc20Addr)
	if err != nil {
		return types.TokenConfig{}, err
	}
	defer iter.Close()

	for ; iter.Valid(); iter.Next() {
		pk, err := iter.PrimaryKey()
		if err != nil {
			return types.TokenConfig{}, err
		}
		cfg, err := k.TokenConfigs.Get(ctx, pk)
		if err != nil {
			return types.TokenConfig{}, err
		}
		if cfg.Chain == chain {
			return cfg, nil
		}
	}

	return types.TokenConfig{}, collections.ErrNotFound
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L146-154)
```go
					if execErr == nil && amount.Sign() > 0 {
						// --- Step 4 & 5: deposit + autoswap (only when amount > 0)
						prc20AddrHex := common.HexToAddress(tokenConfig.NativeRepresentation.ContractAddress)
						receipt, execErr = k.gasAndPayloadDepositAutoSwap(sdkCtx, prc20AddrHex, ueaAddr, amount)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}
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

**File:** x/uexecutor/keeper/msg_execute_payload.go (L38-46)
```go
	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}

	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("execute payload rejected: chain inbound disabled", "chain", caip2Identifier)
		return fmt.Errorf("inbound is disabled for chain %s", caip2Identifier)
	}
```

**File:** x/uregistry/keeper/msg_update_token_config.go (L10-29)
```go
// UpdateTokenConfig updates an existing token configuration in the uregistry.
func (k Keeper) UpdateTokenConfig(ctx context.Context, tokenConfig *types.TokenConfig) error {
	storageKey := types.GetTokenConfigsStorageKey(tokenConfig.Chain, tokenConfig.Address)

	// Check if the token config exists
	if has, err := k.TokenConfigs.Has(ctx, storageKey); err != nil {
		return err
	} else if !has {
		return fmt.Errorf("token config for %s on chain %s does not exist", tokenConfig.Address, tokenConfig.Chain)
	}

	if err := k.TokenConfigs.Set(ctx, storageKey, *tokenConfig); err != nil {
		return err
	}
	k.Logger().Info("token config updated",
		"chain", tokenConfig.Chain,
		"token_address", tokenConfig.Address,
	)
	return nil
}
```
