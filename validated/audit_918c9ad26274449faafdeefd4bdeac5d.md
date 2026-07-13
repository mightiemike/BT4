### Title
Stale In-Memory `decayCache` After Governance Parameter Update - (`File: x/inflation/keeper/keeper.go`)

### Summary

The `inflation` keeper maintains an in-memory `decayCache` struct that caches `DecayRate`, `BlocksPerYear`, and `BlockFactor`. The `SetParams` function persists updated parameters to the KV store but does not update `k.cache`. If governance updates `DecayRate` via `MsgUpdateParams`, the in-memory cache remains stale and inflation calculations continue using the old rate until the node is restarted.

### Finding Description

The `Keeper` struct in `x/inflation/keeper/keeper.go` holds a private in-memory field `cache *decayCache`: [1](#0-0) 

This cache stores `DecayRate`, `BlocksPerYear`, and `BlockFactor` — all derived from governance-updatable parameters. The `SetParams` function writes the new parameters to the KV store: [2](#0-1) 

However, `SetParams` does not call `SetDecayCache` to invalidate or refresh `k.cache`. The cache is only accessible via the exported `GetDecayCache`/`SetDecayCache` pair: [3](#0-2) 

This creates the same desynchronization pattern as the reference report: one component (KV store) is updated while another component (in-memory `decayCache`) retains the stale value. The `BeginBlocker` inflation logic reads from the cache for per-block decay factor computation, not from the KV store directly, so the stale cache drives actual minting behavior.

### Impact Explanation

After a successful governance `MsgUpdateParams` transaction that changes `DecayRate`, the KV store reflects the new rate but the running node's `decayCache` continues to use the old `DecayRate` and the old pre-computed `BlockFactor`. Every subsequent `BeginBlock` mints tokens at the wrong decay rate. Depending on the direction of the change (rate increased or decreased), this results in either over-minting (inflating supply beyond the governance-approved cap) or under-minting (failing to distribute expected staking rewards). The error accumulates block-by-block until the node is restarted.

### Likelihood Explanation

Any governance proposal that updates `inflation` params with a non-zero `DecayRate` change is a sufficient trigger. This is a normal, permissionless governance flow available to any token holder with sufficient voting power. No privileged key or special access is required beyond the standard on-chain governance process.

### Recommendation

`SetParams` should invalidate or rebuild the `decayCache` whenever parameters are persisted. The simplest fix is to set `k.cache = nil` inside `SetParams` so that the next `BeginBlock` recomputes the cache from the freshly stored parameters:

```go
func (k Keeper) SetParams(ctx context.Context, params types.Params) error {
    if err := params.Validate(); err != nil {
        return err
    }
    store := k.storeService.OpenKVStore(ctx)
    bz := k.cdc.MustMarshal(&params)
    if err := store.Set([]byte(types.ParamsKey), bz); err != nil {
        return err
    }
    k.cache = nil  // invalidate stale cache
    return nil
}
```

Alternatively, rebuild the cache inline from the new params immediately after persisting them, mirroring the approach suggested in the reference report (dynamically fetch from the authoritative source rather than relying on a static copy).

### Proof of Concept

1. Governance submits and passes a `MsgUpdateParams` for the `inflation` module, changing `DecayRate` from `0.05` to `0.10`.
2. `SetParams` is called; the KV store now holds `DecayRate = 0.10`.
3. `k.cache` still holds `DecayRate = 0.05` and the old pre-computed `BlockFactor`.
4. Every subsequent `BeginBlock` calls `GetDecayCache()`, receives the stale cache, and mints tokens using the old `BlockFactor`.
5. The chain continues to apply the pre-governance decay schedule indefinitely, diverging from the governance-approved inflation policy until the node process is restarted. [4](#0-3) [2](#0-1) [3](#0-2)

### Citations

**File:** x/inflation/keeper/keeper.go (L19-31)
```go
type Keeper struct {
	cdc           codec.BinaryCodec
	storeService  store.KVStoreService
	logger        log.Logger
	bankKeeper    types.BankKeeper
	stakingKeeper types.StakingKeeper

	// the address capable of executing a MsgUpdateParams message. Typically, this
	// should be the x/inflation module account.
	authority string

	cache *decayCache
}
```

**File:** x/inflation/keeper/keeper.go (L33-37)
```go
type decayCache struct {
	DecayRate     math.LegacyDec
	BlocksPerYear uint64
	BlockFactor   math.LegacyDec
}
```

**File:** x/inflation/keeper/keeper.go (L80-87)
```go
func (k Keeper) SetParams(ctx context.Context, params types.Params) error {
	if err := params.Validate(); err != nil {
		return err
	}
	store := k.storeService.OpenKVStore(ctx)
	bz := k.cdc.MustMarshal(&params)
	return store.Set([]byte(types.ParamsKey), bz)
}
```

**File:** x/inflation/keeper/keeper.go (L136-146)
```go
func (k *Keeper) GetDecayCache() *decayCache {
	if k.cache == nil {
		return nil
	}
	c := *k.cache
	return &c
}

func (k *Keeper) SetDecayCache(cache *decayCache) {
	k.cache = cache
}
```
