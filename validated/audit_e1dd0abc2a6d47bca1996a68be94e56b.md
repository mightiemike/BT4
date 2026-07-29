Confirmed: `ChainConfig.ValidateBasic()` in `x/uregistry/types/chain_config.go` requires `Chain`, `PublicRpcUrl`, `GatewayAddress`, `GasOracleFetchInterval`, and `BlockConfirmation`, but it never checks that `Enabled` is non-nil. `AddChainConfig` in `x/uregistry/keeper/msg_add_chain_config.go` only checks for a duplicate chain key and then persists the config as-is after `MsgAddChainConfig.ValidateBasic()` runs `ChainConfig.ValidateBasic()`. Since `Enabled *ChainEnabled` is an optional protobuf message field, an admin can submit a chain config that omits `enabled` entirely (it defaults to `nil`), and it will be accepted and stored on-chain.

### Title
Nil-pointer panic in `ExecutePayload`/`MigrateUEA` from inconsistent `ChainConfig.Enabled` nil-checking - (File: x/uexecutor/keeper/msg_execute_payload.go, x/uexecutor/keeper/msg_migrate_uea.go)

### Summary
The `IsInboundEnabled`/`IsOutboundEnabled` gate is checked inconsistently across the codebase. `x/uregistry/keeper/keeper.go`'s `IsChainInboundEnabled`/`IsChainOutboundEnabled` (used by `VoteInbound`, `VoteOutbound`, `VoteChainMeta`, and EVM hooks) defensively treat a `nil` `ChainConfig.Enabled` as `false`. However, `ExecutePayload` and `MigrateUEA` in `x/uexecutor/keeper/` dereference `chainConfig.Enabled.IsInboundEnabled` directly, without a nil check, matching the exact pattern in the `InitializableV2` report where one code path enforces a guard and a sibling path silently omits it.

### Finding Description [1](#0-0) 
safely guards against `config.Enabled == nil`, returning `false`. This helper is used by the inbound/outbound/chain-meta voting flows: [2](#0-1) 

But `ExecutePayload`, the gasless, any-caller entry point for `MsgExecutePayload`, accesses the pointer field directly: [3](#0-2) 

and `MigrateUEA` has the identical pattern: [4](#0-3) 

`ChainConfig.ValidateBasic()` does not require `Enabled` to be set: [5](#0-4) 

and `AddChainConfig` persists whatever passes `ValidateBasic()` without any additional nil-guarding: [6](#0-5) 

If any chain in `ChainConfigs` has `Enabled == nil` (an admin-added config that omits the `enabled` field, which is legal protobuf/`ValidateBasic()`-wise), any unprivileged user submitting `MsgExecutePayload` or `MsgMigrateUEA` referencing a `UniversalAccountId` on that chain namespace will cause the transaction handler to dereference a nil pointer, panicking mid-`DeliverTx`.

### Impact Explanation
A nil-pointer dereference inside message processing in a Cosmos SDK module is caught by the SDK's panic-recovery in `baseapp`, which converts it into a failed transaction rather than crashing the whole node in most cases — so the practical impact is more likely a transaction-level DoS for that specific `MsgExecutePayload`/`MsgMigrateUEA` call rather than validator crash/consensus halt, since these messages are gasless but processed inside the normal ante/handler pipeline with panic recovery. This still means users submitting an otherwise well-formed gasless `MsgExecutePayload` against such a chain would be unable to ever execute their pre-authorized payload (or migrate their UEA) on that chain, a persistent, attacker/ops-triggerable functional block that could also be intentionally exploited by anyone who can get such a chain registered (or if `Enabled` is ever left nil by omission during a config update).

### Likelihood Explanation
Likelihood depends entirely on whether any registered `ChainConfig` in production genesis or via `MsgUpdateChainConfig` ever has a `nil` `Enabled` field. Since `ValidateBasic()` and `AddChainConfig`/`UpdateChainConfig` never enforce `Enabled != nil`, this is possible through ordinary admin operation (an accidental omission), not just a deliberate misconfiguration, making this a latent robustness gap rather than a directly attacker-forced state. The actual trigger to hit the panic is completely unprivileged (any user calling `MsgExecutePayload`).

### Recommendation
Route `ExecutePayload` and `MigrateUEA`'s inbound-enabled checks through the existing `k.uregistryKeeper.IsChainInboundEnabled(...)` helper (as `VoteInbound` does) instead of directly dereferencing `chainConfig.Enabled.IsInboundEnabled`, or add an explicit nil check before the dereference. Additionally, consider adding a `ChainConfig.ValidateBasic()` rule requiring `Enabled != nil`, matching the existing requirement for `BlockConfirmation != nil`, to close this class of inconsistency at the source.

### Proof of Concept
1. Admin submits `MsgAddChainConfig` for chain `eip155:99999` with all required fields populated except `Enabled` (left `nil`) — this passes `ChainConfig.ValidateBasic()` and `AddChainConfig` and is persisted.
2. Any user submits `MsgExecutePayload` with `UniversalAccountId.ChainNamespace/ChainId` resolving to `eip155:99999`.
3. `ExecutePayload` calls `k.uregistryKeeper.GetChainConfig(...)`, obtains the config with `Enabled == nil`, then executes `if !chainConfig.Enabled.IsInboundEnabled { ... }`, causing a nil-pointer dereference panic during message handling, distinct from the safe `false` result that `VoteInbound`'s `IsChainInboundEnabled` would have returned for the same misconfigured chain.

### Citations

**File:** x/uregistry/keeper/keeper.go (L195-209)
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

**File:** x/uexecutor/keeper/msg_migrate_uea.go (L39-48)
```go
	chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
	if err != nil {
		return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
	}

	// TODO: Decide later if migration should be disabled if inbound is disabled
	if !chainConfig.Enabled.IsInboundEnabled {
		k.Logger().Warn("migrate UEA rejected: chain not enabled", "chain", caip2Identifier)
		return fmt.Errorf("chain %s is not enabled", caip2Identifier)
	}
```

**File:** x/uregistry/types/chain_config.go (L21-73)
```go
// Validate does the sanity check on the params.
func (p ChainConfig) ValidateBasic() error {
	// Validate chain is non-empty and follows CAIP-2 format
	chain := strings.TrimSpace(p.Chain)
	if chain == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "chain cannot be empty")
	}
	if !strings.Contains(chain, ":") {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "chain must be in CAIP-2 format <namespace>:<reference>")
	}

	// Validate publicRpcUrl
	if strings.TrimSpace(p.PublicRpcUrl) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "public_rpc_url cannot be empty")
	}

	// Validate gatewayAddress
	if strings.TrimSpace(p.GatewayAddress) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "gateway_address cannot be empty")
	}

	// Validate vm_type is within known enum range
	if _, ok := VmType_name[int32(p.VmType)]; !ok {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid vm_type: %v", p.VmType)
	}

	// Validate gateway methods (can be empty, but each provided method must be valid)
	for _, method := range p.GatewayMethods {
		if err := method.ValidateBasic(); err != nil {
			return errors.Wrapf(err, "invalid method in gateway_methods: %s", method.Name)
		}
	}

	// Validate gas oracle fetch interval
	if p.GasOracleFetchInterval <= 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "gas_oracle_fetch_interval must be positive")
	}

	for _, method := range p.VaultMethods {
		if err := method.ValidateBasic(); err != nil {
			return errors.Wrapf(err, "invalid method in vault_methods: %s", method.Name)
		}
	}

	if p.TssSigningDeadline != nil && *p.TssSigningDeadline < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "tss_signing_deadline must not be negative")
	}

	if p.BlockConfirmation == nil {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "block_confirmation is required")
	}
	return p.BlockConfirmation.ValidateBasic()
}
```

**File:** x/uregistry/keeper/msg_add_chain_config.go (L10-24)
```go
// addChainConfig is for adding a new chain configuration
func (k Keeper) AddChainConfig(ctx context.Context, chainConfig *types.ChainConfig) error {
	// Check if chain already exists
	if has, err := k.ChainConfigs.Has(ctx, chainConfig.Chain); err != nil {
		return err
	} else if has {
		return fmt.Errorf("chain config for %s already exists", chainConfig.Chain)
	}

	if err := k.ChainConfigs.Set(ctx, chainConfig.Chain, *chainConfig); err != nil {
		return err
	}
	k.Logger().Info("chain config added", "chain", chainConfig.Chain)
	return nil
}
```
