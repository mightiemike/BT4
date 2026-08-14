### Title
Blocked senders are never purged from the in-memory Functions Gateway allowlist, allowing blocked addresses to remain "allowed" indefinitely - (File: core/services/gateway/handlers/functions/allowlist/allowlist.go)

### Summary
The Functions Gateway's `onchainAllowlist` maintains two representations of the allowed sender list: a persisted DB table and an in-memory `atomic.Pointer[map[common.Address]struct{}]` (`a.allowlist`) consulted by `Allow()` on every incoming request. When senders are blocked on-chain, `syncBlockedSenders` removes them from the DB table but never removes them from the in-memory map, so `Allow()` continues to authorize a blocked sender indefinitely (until the node restarts). This mirrors the reported bug class: an entity is "removed" from one part of the access-control state (like clearing the `allowed`/`fullAccess` flags in `removeContract`) while stale permission data (the in-memory allowlist entry, analogous to `policy[_contract].methods`) is left intact, letting the removed/blocked entity keep acting with old privileges.

### Finding Description
`onchainAllowlist.Allow` is the sole gate used by the Functions gateway handler to decide whether to process a message from a given sender: [1](#0-0) 

The in-memory map it reads is only ever updated in two places: `a.update()` (full replace, used only for pre-v1.1.0 contracts) and `updateAllowedSendersBatch` (which only *adds* entries): [2](#0-1) 

For contracts at or above `tosContractMinBatchProcessingVersion` ("v1.1.0"), `updateFromContractV1` always takes the batch path (`updateAllowedSendersInBatches` + `syncBlockedSenders`), never the full-replace `a.update()` path: [3](#0-2) 

`updateAllowedSendersInBatches` seeds `currentAllowedSenderList` from the *existing* in-memory allowlist and only merges in newly fetched allowed senders — it never removes addresses that are no longer allowed: [4](#0-3) 

`syncBlockedSenders`, which is supposed to handle removal, only deletes blocked senders from the persisted ORM table (`o.orm.DeleteAllowedSenders`) — it never touches `a.allowlist`: [5](#0-4) 

As a result, once an address has been added to the in-memory allowlist, blocking it on-chain (via the ToS AllowList contract's block-list mechanism) has no effect on the running node's `Allow()` decision — the stale "allowed" entry persists in memory just like the stale `policy[_contract].methods` entries persist after `removeContract` in the reported analog. The only way to clear it is a full node restart, which reloads from the DB via `loadStoredAllowedSenderList` (DB is correctly purged, so a restarted node is safe) — but the running node remains vulnerable between block-listing and restart, which for long-lived production nodes with infrequent restarts can be an extended window.

### Impact Explanation
This is a Functions Gateway trust-boundary flaw: the entire purpose of the on-chain ToS allowlist/block-list is to gate which end-users can submit `secrets_set`/`secrets_list` requests to node operators through the gateway (`HandleLegacyUserMessage` calls `h.allowlist.Allow(sender)` before processing): [6](#0-5) 

A sender who has been explicitly blocked (e.g., for abuse, ToS violation, or compromise) continues to be treated as authorized by the gateway handler indefinitely, letting an unprivileged/blocked user keep submitting Functions requests (spending node resources, potentially exfiltrating/setting secrets) despite being removed from the allowlist on-chain.

### Likelihood Explanation
This will trigger deterministically any time: (1) the allowlist runs periodic updates (`UpdateFrequencySec` configured) against a v1.1.0+ ToS AllowList contract, and (2) an address previously present in the in-memory allowlist is subsequently block-listed on-chain. No attacker action beyond having previously been allowlisted is required — this is a passive node bug, not something requiring privileged/malicious node behavior, and it is reachable purely through the normal, expected on-chain block-listing operational flow.

### Recommendation
In `syncBlockedSenders`, after removing blocked senders from the DB, also remove them from the in-memory `a.allowlist` map (e.g., load current snapshot, delete blocked addresses, and `a.allowlist.Store` the updated snapshot), so the in-memory state and persisted state stay consistent and `Allow()` immediately reflects on-chain block-listing.

### Proof of Concept
1. Configure `OnchainAllowlistConfig` with `UpdateFrequencySec` > 0 against a ToS AllowList contract with version ≥ `v1.1.0`.
2. Add address `A` to the on-chain allowlist; wait for an update cycle — `A` is added to `a.allowlist` in memory via `updateAllowedSendersBatch`.
3. Sender `A` sends a `secrets_set` request through the gateway; `HandleLegacyUserMessage` calls `h.allowlist.Allow(A)` → `true`, request is processed.
4. On-chain, block `A` via the ToS AllowList contract's block-listing mechanism.
5. Next update cycle runs `updateAllowedSendersInBatches` (does not remove `A`) then `syncBlockedSenders` (removes `A` only from the DB table, `core/services/gateway/handlers/functions/allowlist/orm.go` `DeleteAllowedSenders`).
6. Sender `A` sends another `secrets_set` request; `a.allowlist` still contains `A`, so `Allow(A)` returns `true` and the blocked sender's request is processed — despite being blocked on-chain.

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

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L324-357)
```go
func (a *onchainAllowlist) updateAllowedSendersBatch(
	ctx context.Context,
	tosContract functions_allow_list.TermsOfServiceAllowListInterface,
	blockNum *big.Int,
	idxStart uint64,
	idxEnd uint64,
	currentAllowedSenderList map[common.Address]struct{},
) error {
	allowedSendersBatch, err := tosContract.GetAllowedSendersInRange(&bind.CallOpts{
		Pending:     false,
		BlockNumber: blockNum,
		Context:     ctx,
	}, idxStart, idxEnd)
	if err != nil {
		return errors.Wrap(err, "error calling GetAllowedSendersInRange")
	}

	// add the fetched batch to the currentAllowedSenderList and replace the existing allowlist
	for _, addr := range allowedSendersBatch {
		currentAllowedSenderList[addr] = struct{}{}
	}

	snapshot := make(map[common.Address]struct{}, len(currentAllowedSenderList))
	maps.Copy(snapshot, currentAllowedSenderList)
	a.allowlist.Store(&snapshot)
	a.lggr.Infow("allowlist updated in batches successfully", "len", len(currentAllowedSenderList))

	// persist each batch to the underalying orm layer
	err = a.orm.CreateAllowedSenders(ctx, allowedSendersBatch)
	if err != nil {
		a.lggr.Errorf("failed to update stored allowedSenderList: %v", err)
	}
	return nil
}
```

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L359-397)
```go
// syncBlockedSenders fetches the list of blocked addresses from the contract in batches
// and removes the addresses from the functions_allowlist table if present
func (a *onchainAllowlist) syncBlockedSenders(ctx context.Context, tosContract *functions_allow_list.TermsOfServiceAllowList, blockNum *big.Int) error {
	count, err := tosContract.GetBlockedSendersCount(&bind.CallOpts{
		Pending:     false,
		BlockNumber: blockNum,
		Context:     ctx,
	})
	if err != nil {
		return errors.Wrap(err, "unexpected error during functions_allow_list.GetBlockedSendersCount")
	}

	throttleTicker := time.NewTicker(time.Duration(a.config.FetchingDelayInRangeSec) * time.Second)
	for idxStart := uint64(0); idxStart < count; idxStart += uint64(a.config.OnchainAllowlistBatchSize) {
		<-throttleTicker.C

		idxEnd := idxStart + uint64(a.config.OnchainAllowlistBatchSize)
		if idxEnd >= count {
			idxEnd = count - 1
		}

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
	throttleTicker.Stop()

	return nil
}
```

**File:** core/services/gateway/handlers/functions/handler.functions.go (L208-214)
```go
func (h *functionsHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	sender := common.HexToAddress(msg.Body.Sender)
	if h.allowlist != nil && !h.allowlist.Allow(sender) {
		h.lggr.Debugw("received a message from a non-allowlisted address", "sender", msg.Body.Sender)
		promHandlerError.WithLabelValues(h.donConfig.DonId, ErrNotAllowlisted.Error()).Inc()
		return ErrNotAllowlisted
	}
```
