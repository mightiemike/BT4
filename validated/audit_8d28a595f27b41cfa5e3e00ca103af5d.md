### Title
Missing nil-safe chain-enabled guard in `ExecutePayload` allows panic on gasless payload execution - (File: `x/uexecutor/keeper/msg_execute_payload.go`)

### Summary
`ExecutePayload` is the Push Chain analog of the external `claimConverted()` bug: a user-facing function that must first check a "started" precondition (the target chain is inbound-enabled) before performing state-mutating work. Unlike `VoteInbound`, which uses the safe `IsChainInboundEnabled` helper, `ExecutePayload` dereferences `chainConfig.Enabled.IsInboundEnabled` without verifying that `Enabled` is non-nil. If a chain config exists with `Enabled == nil`, the handler panics instead of cleanly rejecting the transaction.

### Finding Description
In `x/uexecutor/keeper/msg_execute_payload.go`, `ExecutePayload` fetches the chain config and immediately evaluates:

```go
chainConfig, err := k.uregistryKeeper.GetChainConfig(sdkCtx, caip2Identifier)
if err != nil {
    return errors.Wrapf(err, "failed to get chain config for chain %s", caip2Identifier)
}

if !chainConfig.Enabled.IsInboundEnabled {
```

`chainConfig.Enabled` is a pointer (`*uregistrytypes.ChainEnabled`). When it is `nil`, the expression `chainConfig.Enabled.IsInboundEnabled` causes a nil pointer dereference. The codebase already treats nil `Enabled` as "disabled" in `x/uregistry/keeper/keeper.go` via `IsChainInboundEnabled`, but `ExecutePayload` bypasses that helper and performs an unsafe direct access.

### Impact Explanation
`MsgExecutePayload` is gasless and callable by any account (`app/txpolicy/gasless.go`). A panic in this handler can be triggered by an unprivileged user if a chain config with `Enabled == nil` is present. Depending on the SDK panic-recovery path, this results in a transaction-level panic failure and can be used as a denial-of-service vector against the node. No unauthorized execution occurs, but the broken guard prevents the intended rejection and destabilizes the handler.

### Likelihood Explanation
The vulnerable state is realistic because `ChainConfig.Enabled` is an optional proto message pointer. `uregistry/keeper.go` explicitly handles `config.Enabled == nil` as a valid state, returning `false` from `IsChainInboundEnabled`. If governance, genesis, or a migration stores a `ChainConfig` without an `Enabled` field, any subsequent `MsgExecutePayload` targeting that chain reaches the nil dereference.

### Recommendation
Replace the direct field access with the safe keeper helper:

```go
enabled, err := k.uregistryKeeper.IsChainInboundEnabled(sdkCtx, caip2Identifier)
if err != nil {
    return errors.Wrapf(err, "failed to check inbound enabled for chain %s", caip2Identifier)
}
if !enabled {
    return fmt.Errorf("inbound is disabled for chain %s", caip2Identifier)
}
```

This aligns `ExecutePayload` with `VoteInbound` and correctly treats nil `Enabled` as disabled.

### Proof of Concept
1. Store a `ChainConfig` for chain `eip155:11155111` with `Enabled` left nil.
2. Submit a `MsgExecutePayload` whose `UniversalAccountId` resolves to that chain, with any well-formed payload and verification data.
3. The handler reaches `if !chainConfig.Enabled.IsInboundEnabled` and panics with a nil pointer dereference. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** app/txpolicy/gasless.go (L17-25)
```go
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
```
