### Title
Stale in-memory allowlist cache allows revoked/blocked senders to keep using the Functions Gateway - ([File: core/services/gateway/handlers/functions/allowlist/allowlist.go])

### Summary
`onchainAllowlist.updateAllowedSendersInBatches` reconstructs its refreshed allowlist snapshot by copying the *existing* in-memory `a.allowlist` map as a starting point and then only adds newly observed allowed senders on top of it, exactly like the reported Yearn `BaseWrapper._cachedVaults` bug where the cached prefix is copied forward and never revisited. `syncBlockedSenders`, which is supposed to purge blocked/revoked senders, only deletes them from the persistent ORM table — it never touches the in-memory `a.allowlist` map that `Allow()` actually consults, so a sender removed on-chain remains authorized in memory indefinitely.

### Finding Description
`updateFromContractV1` runs two independent update steps when using the batch-processing contract path: `updateAllowedSendersInBatches` (adds allowed senders) and `syncBlockedSenders` (removes blocked senders) [1](#0-0) .

`updateAllowedSendersInBatches` seeds `currentAllowedSenderList` from the currently-stored in-memory allowlist (`a.allowlist.Load()`), i.e. the previous cache snapshot, and then only merges newly fetched addresses into it — it never removes any address already present in that seed set: [2](#0-1) [3](#0-2) 

`syncBlockedSenders`, called right after, fetches the on-chain blocked-sender list and calls `a.orm.DeleteAllowedSenders`, which only deletes rows from the persisted `functions_allowlist` table — it never calls `a.allowlist.Store(...)` or otherwise mutates the in-memory map: [4](#0-3) 

`Allow()`, the function that actually gates Functions Gateway requests, only reads from the in-memory `a.allowlist` pointer: [5](#0-4) 

Because the in-memory prefix is only ever carried forward and appended to (never rebuilt from a fresh, complete on-chain snapshot, and never pruned to reflect blocked/removed addresses), an address that is revoked on-chain (moved from allowed to blocked, or simply removed from the allowlist contract) continues to satisfy `Allow()` on every subsequent periodic update for as long as the node process runs, since the stale entry is copied forward into every new `snapshot` written via `a.allowlist.Store(&snapshot)`. This is the same root cause pattern as the `_cachedVaults` bug: the cache is refreshed by extending an old copy rather than being reconciled against the authoritative source, so previously-cached-but-now-invalid entries are never corrected.

### Impact Explanation
`OnchainAllowlist.Allow` gates access to the Chainlink Functions Gateway handler for unprivileged, external callers (`core/services/gateway/handlers/functions/handler.functions.go` consumes this allowlist to authorize incoming requests). A sender that is removed from the on-chain Terms-of-Service allowlist (e.g., due to abuse, ToS violation, or compromise) will continue to be treated as authorized by any already-running gateway node, since the in-memory cache is never pruned — only newly-added, up-to-date. This is an authorization bypass: a revoked/blocked identity retains privileged access to the Functions Gateway despite on-chain revocation, until the node process restarts and reloads state fresh from `loadStoredAllowedSenderList`/ORM (which, notably, is also fed from `a.orm.CreateAllowedSenders` writes that are never pruned for blocked senders in the in-memory path).

### Likelihood Explanation
This triggers on every periodic `UpdateFromContract` cycle (`UpdateFrequencySec`) for any node using the newer batch-processing ToS contract path (`tosContractMinBatchProcessingVersion` or later) whenever an operator/admin blocks or removes a previously-allowed sender on-chain. This is a normal, expected admin operation (revoking access), not requiring any attacker action beyond having been allowed at some point and then revoked — the vulnerable window is any period between the block event and the next full process restart.

### Recommendation
In `syncBlockedSenders` (or immediately after it runs), remove the blocked addresses from the in-memory `a.allowlist` snapshot as well as from the ORM, e.g. load the current snapshot, delete the blocked addresses, and re-`Store` it — mirroring the recommendation from the reference report to update all elements of the cache, not just append new ones. More robustly, `updateAllowedSendersInBatches` should reconcile against the full authoritative on-chain state (or intersect with a freshly-fetched complete set) rather than perpetually extending the previous in-memory snapshot, so that on-chain removals are reflected in the in-memory `Allow()` decision.

### Proof of Concept
1. Sender `0xABC` is added to the ToS allow-list contract; the gateway node runs `UpdateFromContract`, and `updateAllowedSendersInBatches` sets `a.allowlist = {0xABC}` [3](#0-2) .
2. `0xABC` is subsequently blocked/removed on-chain (added to the blocked-senders list, or its allowed-sender slot cleared).
3. On the next periodic update, `updateAllowedSendersInBatches` again seeds `currentAllowedSenderList` from the existing in-memory map, which still contains `0xABC` [6](#0-5) ; `syncBlockedSenders` runs afterward but only calls `a.orm.DeleteAllowedSenders(ctx, blockedSendersBatch)`, which affects only the persisted ORM table, not `a.allowlist` [7](#0-6) .
4. `Allow(0xABC)` still returns `true` [5](#0-4) , so the gateway continues to accept and process requests from a sender that was explicitly revoked on-chain, until the node process restarts.

### Citations

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L175-179)
```go
func (a *onchainAllowlist) Allow(address common.Address) bool {
	allowlist := *a.allowlist.Load()
	_, ok := allowlist[address]
	return ok
}
```

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L223-232)
```go
	if semver.Compare(tosContractMinBatchProcessingVersion, currentVersion) <= 0 {
		err = a.updateAllowedSendersInBatches(ctx, tosContract, blockNum)
		if err != nil {
			return errors.Wrap(err, "failed to get allowed senders in rage")
		}

		err := a.syncBlockedSenders(ctx, tosContract, blockNum)
		if err != nil {
			return errors.Wrap(err, "failed to sync the stored allowed and blocked senders")
		}
```

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L263-270)
```go
func (a *onchainAllowlist) updateAllowedSendersInBatches(ctx context.Context, tosContract functions_allow_list.TermsOfServiceAllowListInterface, blockNum *big.Int) error {
	// currentAllowedSenderList will be the starting point from which we will be adding the new allowed senders
	currentAllowedSenderList := make(map[common.Address]struct{}, 0)
	if cal := a.allowlist.Load(); cal != nil {
		for k := range *cal {
			currentAllowedSenderList[k] = struct{}{}
		}
	}
```

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L341-349)
```go
	// add the fetched batch to the currentAllowedSenderList and replace the existing allowlist
	for _, addr := range allowedSendersBatch {
		currentAllowedSenderList[addr] = struct{}{}
	}

	snapshot := make(map[common.Address]struct{}, len(currentAllowedSenderList))
	maps.Copy(snapshot, currentAllowedSenderList)
	a.allowlist.Store(&snapshot)
	a.lggr.Infow("allowlist updated in batches successfully", "len", len(currentAllowedSenderList))
```

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L380-393)
```go
		blockedSendersBatch, err := tosContract.GetBlockedSendersInRange(&bind.CallOpts{
			Pending:     false,
			BlockNumber: blockNum,
			Context:     ctx,
		}, idxStart, idxEnd)
		if err != nil {
			return errors.Wrap(err, "error calling GetAllowedSendersInRange")
		}

		err = a.orm.DeleteAllowedSenders(ctx, blockedSendersBatch)
		if err != nil {
			a.lggr.Errorf("failed to delete blocked address from allowed list in storage: %v", err)
		}
	}
```
