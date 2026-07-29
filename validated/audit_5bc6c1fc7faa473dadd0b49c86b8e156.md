## Finding

### Title
Missing pagination handling in `GetGranteeGrants` allows unprivileged grant-spam to permanently hide a UV's real AuthZ grants and block signer initialization - (File: `universalClient/pushcore/pushCore.go`, `universalClient/pushsigner/grant_verifier.go`)

### Summary
`Client.GetGranteeGrants` issues an unpaginated `QueryGranteeGrantsRequest` (no `Pagination` field is ever set), and `validateKeysAndGrants` consumes only the single response it gets back without ever following `PageResponse.NextKey`. Because the underlying `x/authz` grant store is keyed by `(grantee, granter, msgTypeUrl)`, an unprivileged attacker can submit ordinary `MsgGrant` transactions — as the granter, targeting a known/observable Universal Validator hotkey address as grantee — using freely-precomputed key addresses whose bytes sort before the legitimate granter's address. Once enough such junk grants exist, they occupy the entire first page (bounded by the SDK's `query.DefaultLimit`), pushing the legitimate `push1granter`-issued grants for `MsgVoteInbound`, `MsgVoteChainMeta`, `MsgVoteOutbound`, `MsgVoteTssKeyProcess`, `MsgVoteFundMigration` off the first page. `verifyGrants` then reports `"missing grants from granter %s"`, and `Signer.New()` fails.

### Finding Description
- `GetGranteeGrants` in [1](#0-0)  queries `GranteeGrants` with only a `Grantee` field set, never a `Pagination` request, and never inspects a page response.
- `validateKeysAndGrants` in [2](#0-1)  takes the single returned page, extracts grant info, and fails outright with `"no AuthZ grants found"` or (after `verifyGrants`) `"missing grants from granter %s"` if the required message grants are not present in that page.
- `Signer.New()` calls `validateKeysAndGrants` on every construction (i.e., every node start/restart) as shown in [3](#0-2) , and a failure here is fatal to the signer, which is required to cast `MsgVoteInbound`/`MsgVoteOutbound`/etc.
- The AuthZ store key order is `(grantee, granter, msgType)`, so any attacker able to submit `MsgGrant` transactions (an ordinary, unprivileged, gas-paying transaction — not part of the gasless allow-list in [4](#0-3) ) can enumerate off-chain key addresses whose raw bytes sort before the legitimate granter's address, then grant arbitrary/no-op `GenericAuthorization`s from those addresses to the victim UV's grantee address. Because address generation is free and only submission costs gas, roughly half of randomly generated candidate addresses satisfy "sorts before target," making the attack cheap and entirely offline-precomputable before broadcasting.
- Once the default page limit (`query.DefaultLimit`, commonly 100) worth of such junk grants exist ahead of the real grants in iteration order, the real grants are never returned to the unpaginated query, and `validateKeysAndGrants` will keep failing with `"missing grants"` on every subsequent `Signer.New()` call — i.e., on every node restart — until someone (not the victim, since only the original granter of a grant can revoke it) intervenes.

### Impact Explanation
This is a non-network-level, targeted denial of service: an unprivileged, un-relayed, ordinary user (paying normal gas) can permanently block a specific Universal Validator's `pushsigner.Signer` from initializing after any restart, without needing validator, TSS, relayer, or admin privileges. The affected UV cannot vote on inbound/outbound/chain-meta/TSS/migration messages until the underlying pagination/query gap is fixed or the spam grants are pruned/expired — which the victim cannot do unilaterally since AuthZ grants can only be revoked by their granter. This matches the "denial of service only when it is not network-level and reachable without privileged control" allowed-impact category, scoped to the affected UV's readiness/liveness rather than a global consensus halt.

### Likelihood Explanation
Medium. The attacker needs the target UV's grantee (hotkey) address, which is discoverable on-chain (grants/votes reveal the grantee address), and needs to submit on the order of the default page-limit number of `MsgGrant` transactions from precomputed addresses — a bounded, moderate but non-trivial gas cost, entirely executable by any unprivileged account with no special access.

### Recommendation
- In `GetGranteeGrants` (`universalClient/pushcore/pushCore.go`), always issue a `Pagination` request and loop using `PageResponse.NextKey` until all pages are retrieved (or apply a query with an explicit high limit and detect/reject truncation) before returning to callers.
- In `validateKeysAndGrants` (`universalClient/pushsigner/grant_verifier.go`), do not rely on iteration order alone; explicitly filter for the exact `(granter, msgType)` combinations needed rather than assuming they will appear on the first page.
- Consider a dedicated on-chain query path (e.g., a custom query keyed by `(grantee, granter)` directly) instead of the generic paginated `GranteeGrants` query, to make the lookup immune to unrelated-granter grant volume.

### Proof of Concept
1. Observe the target UV's grantee (hotkey) address (e.g., from prior `MsgVoteInbound` transactions signed via AuthZ on behalf of that grantee).
2. Offline, generate keypairs and their derived `sdk.AccAddress` bytes until enough (~`query.DefaultLimit`, e.g., 100) addresses are found whose byte representation sorts lexicographically before the legitimate granter's (`push1granter`) address.
3. From each of those attacker-controlled addresses, broadcast a cheap `MsgGrant` (paying normal gas fees — `MsgGrant` is not in the gasless allow-list) granting some arbitrary/no-op `GenericAuthorization` message type to the victim UV's grantee address.
4. On the victim UV's next restart, `Signer.New()` → `validateKeysAndGrants` → `pushCore.GetGranteeGrants` returns only the first (unpaginated) page of grants, which is now filled with the attacker's junk grants; the legitimate `push1granter` grants are pushed past the page boundary.
5. `verifyGrants` returns `"missing grants from granter push1granter: [...]"`, `Signer.New()` fails, and the UV cannot rejoin voting until the query/pagination handling is fixed or the spam grants are cleared (which the victim cannot do themselves). [1](#0-0) [5](#0-4) [3](#0-2) [4](#0-3)

### Citations

**File:** universalClient/pushcore/pushCore.go (L251-264)
```go
// GetGranteeGrants queries AuthZ grants for a grantee using round-robin logic.
func (c *Client) GetGranteeGrants(ctx context.Context, granteeAddr string) (*authz.QueryGranteeGrantsResponse, error) {
	return retryWithRoundRobin(
		len(c.authzClients),
		&c.rr,
		func(idx int) (*authz.QueryGranteeGrantsResponse, error) {
			return c.authzClients[idx].GranteeGrants(ctx, &authz.QueryGranteeGrantsRequest{
				Grantee: granteeAddr,
			})
		},
		"GetGranteeGrants",
		c.logger,
	)
}
```

**File:** universalClient/pushsigner/grant_verifier.go (L85-108)
```go
	grantResp, err := pushCore.GetGranteeGrants(ctx, keyAddrStr)
	if err != nil {
		return nil, fmt.Errorf("failed to query grants: %w", err)
	}

	grants := extractGrantInfo(grantResp, cdc)
	if len(grants) == 0 {
		return nil, fmt.Errorf("no AuthZ grants found for %s", keyAddrStr)
	}

	// Verify grants against the specified granter
	authorizedMsgs, err := verifyGrants(grants, granter)
	if err != nil {
		return nil, fmt.Errorf("%w (grantee: %s)", err, keyAddrStr)
	}

	return &validationResult{
		Keyring:  kr,
		KeyName:  keyInfo.Name,
		KeyAddr:  keyAddrStr,
		Granter:  granter,
		Messages: authorizedMsgs,
	}, nil
}
```

**File:** universalClient/pushsigner/pushsigner.go (L50-68)
```go
// New creates a new Signer instance with validation.
func New(
	ctx context.Context,
	log zerolog.Logger,
	keyringBackend config.KeyringBackend,
	keyringPassword string,
	nodeHome string,
	pushCore *pushcore.Client,
	chainID string,
	granter string,
) (*Signer, error) {
	log = log.With().Str("component", "push_signer").Logger()
	log.Debug().Msg("Validating hotkey and AuthZ permissions...")

	validationResult, err := validateKeysAndGrants(ctx, keyringBackend, keyringPassword, nodeHome, pushCore, granter)
	if err != nil {
		log.Error().Err(err).Msg("PushSigner validation failed")
		return nil, fmt.Errorf("PushSigner validation failed: %w", err)
	}
```

**File:** app/txpolicy/gasless.go (L14-26)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```
