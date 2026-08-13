### Title
Stale zone-b DON membership cache allows vault `GetSecrets` restriction bypass on transient registry lookup failure - ([File: core/capabilities/vault/zone_b_restriction.go])

### Summary
`zoneBRestrictor.enforce()` gates vault `GetSecrets` reads from "zone-b" workflow DONs behind a per-owner allowlist, but the underlying membership check silently falls back to a locally cached, potentially stale boolean whenever the authoritative capabilities-registry lookup transiently fails. Because the cache is only ever refreshed on a *successful* lookup and is never invalidated or re-checked once a value is cached, a DON that is later moved into (or already is in) the `zone-b` family can be treated as non-zone-b if the last successful resolution happened before that membership existed, or if `DONByID` errors after the membership changes. This mirrors the reported bug class: security-relevant logic ("is this DON restricted?") executes against a cache that is not guaranteed to be synced with authoritative on-chain/registry state, and the code proceeds as if it were.

### Finding Description
`enforce()` is the sole gate for restricting vault secret reads from zone-b DONs to an owner allowlist: [1](#0-0) 

The zone determination itself is delegated to `isZoneBWorkflowDON`, which resolves membership from `capabilitiesRegistry.DONByID`. On a lookup error, instead of failing closed unconditionally, it falls back to `cachedZoneMembership`, a value stored from the *last successful* resolution for that DON: [2](#0-1) 

The cache is a simple map, written only in `storeZoneMembership` after a successful registry read, and read in `cachedZoneMembership` with no timestamp/versioning/staleness check: [3](#0-2) 

The comment at lines 103-108 explicitly acknowledges the registry can be "not yet synced after startup, or nil mid-update," i.e., the exact scenario the external report describes (cache not guaranteed synced with authoritative state) — but the mitigation chosen (fall back to last-known value) can be actively wrong rather than merely stale-but-safe: if a DON's family membership changes from non-zone-b to zone-b (an operator/config-driven event), and the node's cache still holds the pre-change `false` value because the most recent lookup after the change happened to error, `enforce()` will treat all subsequent calls from that DON as non-restricted until a lookup happens to succeed again. There is no bound on how long this stale state can persist, and no re-validation is forced independent of the registry's own error/retry behavior.

This is directly analogous to the reported class: a security decision (`create()`/`swapTokenForTokens()` trusting a synced operator cache in the original report; here, `enforce()` trusting a synced DON-family cache) is made without a hard guarantee that the cache reflects current authoritative state, and the flow provides no explicit mechanism (equivalent to `rebuildCache()`/a "cache synced" modifier) to detect and reject decisions made against stale data.

### Impact Explanation
If exploitable, this allows a workflow owner that is *not* on the zone-b allowlist to read vault secrets via `GetSecrets` that should be restricted, because the restrictor incorrectly resolves their workflow DON as "not zone-b" due to a stale cached value. This is a secret-disclosure trust-boundary violation — the exact class of impact that is explicitly in scope per the validation rules.

### Likelihood Explanation
Likelihood depends on the operational frequency of DON-family changes coinciding with `DONByID` registry-read errors, which the code's own comments say do occur ("not yet synced after startup, or nil mid-update"). Because the fallback is unconditional (any error, regardless of cause, uses the cache) and the cache has no TTL/versioning, the window where stale data can be relied upon is unbounded once established, making this a persistent-until-next-successful-lookup condition rather than a narrow race. It requires no attacker privilege beyond being a workflow owner already using the CRE/vault path — no malicious operator/node is required, satisfying the "unprivileged-user analog" constraint.

### Recommendation
Do not treat a cached "non-zone-b" result as a valid basis for skipping the allowlist check. Options:
- Fail closed only for the case that matters most for security: if the *cached* value is `false` (not restricted) but the fresh lookup fails, still enforce as if restricted (fail closed) unless a fresh registry read successfully confirms non-membership — i.e., asymmetric fallback: cache hit of `true` (restricted) can be trusted defensively, cache hit of `false` should not be trusted to bypass a security check.
- Add a staleness bound (max age) to `zoneCache` entries and require a fresh resolution before use once expired, rather than trusting indefinitely-old cached memberships.
- Ensure the workflow-registry/capabilities-registry sync path exposes a way to know "is my view current" (analogous to `rebuildCache()`), and have `enforce()` fail closed when that freshness cannot be established, rather than defaulting to permissive behavior on any error.

### Proof of Concept
1. Node starts; DON `X` is not part of the `zone-b` family. `isZoneBWorkflowDON` resolves and caches `zoneCache[X] = false`.
2. Operator later adds DON `X` to the `zone-b` family on-chain/in the registry (a legitimate, expected administrative action, not a malicious actor).
3. Shortly after this change (e.g., during registry propagation/mid-update, as the code comment itself anticipates), a `GetSecrets` request from a non-allowlisted workflow owner in DON `X` arrives; `capabilitiesRegistry.DONByID(ctx, X)` transiently errors.
4. `isZoneBWorkflowDON` falls back to `cachedZoneMembership(X)`, returning the stale `false`.
5. `enforce()` sees `isZoneB == false` and returns `nil` immediately, skipping the `ownerAllowed.AllowErr` check — the non-allowlisted owner's `GetSecrets` call proceeds and secrets are disclosed, despite DON `X` now being restricted to zone-b's allowlist.

### Citations

**File:** core/capabilities/vault/zone_b_restriction.go (L71-95)
```go
func (z *zoneBRestrictor) enforce(ctx context.Context, workflowDonID uint32) error {
	enabled, err := z.restrictEnabled.Limit(ctx)
	if err != nil {
		return fmt.Errorf("could not evaluate zone-b vault read restriction gate: %w", err)
	}
	if !enabled {
		return nil
	}

	isZoneB, err := z.isZoneBWorkflowDON(ctx, workflowDonID)
	if err != nil {
		// Fail closed: if we cannot authoritatively resolve the caller's zone, do
		// not proceed. The registry is in-process, so this only fires for an
		// unknown/unregistered WorkflowDonID.
		return err
	}
	if !isZoneB {
		return nil
	}

	if err := z.ownerAllowed.AllowErr(ctx); err != nil {
		return fmt.Errorf("zone-b workflow DON may only read vault secrets for allowlisted workflow owners: %w", err)
	}
	return nil
}
```

**File:** core/capabilities/vault/zone_b_restriction.go (L100-122)
```go
func (z *zoneBRestrictor) isZoneBWorkflowDON(ctx context.Context, workflowDonID uint32) (bool, error) {
	don, err := z.capabilitiesRegistry.DONByID(ctx, workflowDonID)
	if err != nil {
		// The registry view can be transiently unavailable (e.g. not yet synced
		// after startup, or nil mid-update: DONByID returns "metadataRegistry
		// information not available"). That error is not specific to zone-b
		// callers, so failing closed here would block every vault GetSecrets read
		// DON-wide. Fall back to the last successfully-resolved membership for this
		// DON; only a never-before-resolved DON fails closed.
		if cached, ok := z.cachedZoneMembership(workflowDonID); ok {
			z.lggr.Warnw("capabilities registry lookup failed; using cached zone-b membership",
				"workflowDonID", workflowDonID, "isZoneB", cached, "err", err)
			return cached, nil
		}
		return false, fmt.Errorf("could not resolve caller workflow DON %d for zone-b vault read restriction: %w", workflowDonID, err)
	}
	// Case-insensitive match: family casing may vary across registry sources.
	isZoneB := slices.ContainsFunc(don.Families, func(family string) bool {
		return strings.EqualFold(family, zoneBFamily)
	})
	z.storeZoneMembership(workflowDonID, isZoneB)
	return isZoneB, nil
}
```

**File:** core/capabilities/vault/zone_b_restriction.go (L124-135)
```go
func (z *zoneBRestrictor) cachedZoneMembership(workflowDonID uint32) (bool, bool) {
	z.zoneCacheMu.RLock()
	defer z.zoneCacheMu.RUnlock()
	isZoneB, ok := z.zoneCache[workflowDonID]
	return isZoneB, ok
}

func (z *zoneBRestrictor) storeZoneMembership(workflowDonID uint32, isZoneB bool) {
	z.zoneCacheMu.Lock()
	defer z.zoneCacheMu.Unlock()
	z.zoneCache[workflowDonID] = isZoneB
}
```
