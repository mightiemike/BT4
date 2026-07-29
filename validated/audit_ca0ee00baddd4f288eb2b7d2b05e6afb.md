### Title
Nil-pointer panic in `ExecutePayload` via unchecked `ChainConfig.Enabled` dereference - (File: `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
The `vesting_validation_bypass` report's underlying bug class is: an invariant that is properly checked in one code path is silently skipped in a related mutation/consumption path, letting previously-valid state be used in an invalid way. Push Chain's `x/uregistry` module has the same structural gap: `ChainConfig.ValidateBasic()` never requires the `Enabled` sub-message to be set, and the safe accessor helpers (`IsChainInboundEnabled`/`IsChainOutboundEnabled`) guard against `Enabled == nil`, but `x/uexecutor`'s `ExecutePayload` keeper method reads `ChainConfig.Enabled` directly and dereferences it without a nil check.

### Finding Description
`ChainConfig.ValidateBasic()` (`x/uregistry/types/chain_config.go`) validates chain, RPC URL, gateway address, VM type, gateway/vault methods, gas-oracle interval, and block confirmation — but never asserts `Enabled != nil`: [1](#0-0) 

Because of this gap, a `ChainConfig` can be legitimately stored (via `MsgAddChainConfig`/`MsgUpdateChainConfig`, or via genesis import) with `Enabled == nil`. The rest of the codebase treats this as "expected and safe": both `x/uregistry`'s own keeper helpers and the Universal Validator client's mirrored logic explicitly nil-check `Enabled` before reading its flags: [2](#0-1) [3](#0-2) 

`x/uexecutor`'s `VoteInbound` path also uses the safe helper `IsChainInboundEnabled`: [4](#0-3) 

However, `Keeper.ExecutePayload` (invoked by the gasless, any-caller `MsgExecutePayload` message) fetches the raw `ChainConfig` and dereferences `Enabled` directly, bypassing the nil-safe helper entirely: [5](#0-4) 

If `chainConfig.Enabled` is `nil` (a state the module itself allows to be persisted, since `ValidateBasic` doesn't forbid it), `chainConfig.Enabled.IsInboundEnabled` is a nil-pointer field dereference on a Go struct pointer and panics.

### Impact Explanation
Any unprivileged, unauthenticated caller can submit a gasless `MsgExecutePayload` (in the `IsGaslessTx` allowlist — no fee, no special role required) referencing a `UniversalAccountId` whose `ChainNamespace:ChainId` resolves to a `ChainConfig` with `Enabled == nil`. This triggers a Go nil-pointer panic inside message execution. While Cosmos SDK's `BaseApp.runTx` recovers panics per-transaction (so it does not halt the whole chain), it is still an uncontrolled crash-and-recover path instead of the clean, validated error path (`"inbound is disabled for chain %s"`) that the same invariant produces everywhere else in the codebase (e.g. `VoteInbound`). This is a genuine broken invariant reachable purely through a default/omitted admin config plus an ordinary user transaction — the underlying defect (skipped re-validation of an assumption established elsewhere) mirrors the reported vesting bug exactly, even though the blast radius here is bounded to per-tx panic recovery rather than fund loss.

### Likelihood Explanation
Triggering requires only that some registered `ChainConfig` exists with `Enabled` unset — plausible whenever an admin adds/updates a chain config without explicitly supplying the optional `Enabled` field (there is no validation forcing it), which is an easy, non-malicious omission given `ValidateBasic` doesn't catch it. Once such a config exists, any external, non-privileged party can trigger the panic at will and for free (gasless message, no signature-cost) by targeting `UniversalAccountId{ChainNamespace, ChainId}` matching that chain.

### Recommendation
1. Add `Enabled != nil` (or a documented default) to `ChainConfig.ValidateBasic()` in `x/uregistry/types/chain_config.go` so misconfigured chains cannot be persisted.
2. Defense in depth: change `Keeper.ExecutePayload` in `x/uexecutor/keeper/msg_execute_payload.go` to use the nil-safe `uregistryKeeper.IsChainInboundEnabled(ctx, caip2Identifier)` helper (as `VoteInbound` already does) instead of dereferencing `chainConfig.Enabled` directly.

### Proof of Concept
1. Admin calls `MsgAddChainConfig` for `eip155:X` with all required fields but omits `Enabled` (protobuf leaves it `nil`); this passes `ValidateBasic` today.
2. Any external user submits `MsgExecutePayload` with `UniversalAccountId{ChainNamespace:"eip155", ChainId:"X", Owner:<any>}` and an otherwise well-formed payload.
3. Execution reaches `if !chainConfig.Enabled.IsInboundEnabled` in `x/uexecutor/keeper/msg_execute_payload.go:43`, causing a nil-pointer dereference panic instead of the intended `"inbound is disabled for chain %s"` error.

### Citations

**File:** x/uregistry/types/chain_config.go (L21-72)
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
```

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

**File:** universalClient/chains/chains.go (L379-393)
```go
// IsChainInboundEnabled returns whether inbound is enabled for the given chain
func (c *Chains) IsChainInboundEnabled(chainID string) bool {
	c.chainsMu.RLock()
	cfg := c.chainConfigs[chainID]
	c.chainsMu.RUnlock()
	return cfg != nil && cfg.Enabled != nil && cfg.Enabled.IsInboundEnabled
}

// IsChainOutboundEnabled returns whether outbound is enabled for the given chain
func (c *Chains) IsChainOutboundEnabled(chainID string) bool {
	c.chainsMu.RLock()
	cfg := c.chainConfigs[chainID]
	c.chainsMu.RUnlock()
	return cfg != nil && cfg.Enabled != nil && cfg.Enabled.IsOutboundEnabled
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
