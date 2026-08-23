Confirmed: this pattern is consistent across `shopify-app-express`, `shopify-app-remix`, and `shopify-app-react-router` — the Auth Code Flow strategy checks `session.isActive(config.scopes)` (scope-aware), while the Token Exchange strategy checks `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)` (scope check intentionally omitted) in all three packages.

### Title
Token-exchange authentication path skips scope-change validation, reusing stale sessions with revoked/reduced scopes - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts`)

### Summary
Analogous to the Rolla finding where an "active" check (`isOracleActive`) was enforced on one code path (vanilla option minting) but omitted on an equivalent path (spread minting), the shopify-app-js authentication strategies enforce a scope-change check on the Auth Code Flow path but intentionally skip it on the Token Exchange path. `Session.isActive(scopes, withinMillisecondsOfExpiry)` is defined as active only when the access token is present, not expired, **and** the requested scopes match the stored scopes [1](#0-0) . The Auth Code Flow strategy calls this with the real configured scopes, `session.isActive(config.scopes)` [2](#0-1) , so a session whose granted scopes no longer match the app's configured/requested scopes is treated as inactive and forces re-authorization. The Token Exchange strategy, used by every `shopify-app-remix`, `shopify-app-express`, and `shopify-app-react-router` app that enables the `tokenExchange` future flag, instead calls `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)`, passing `undefined` for scopes [3](#0-2) .

### Finding Description
`isActive` treats an `undefined` scopes argument as "no scope requirement", per `isScopeChanged`, which returns `false` (i.e., scopes are considered unchanged) whenever `scopes` is `undefined` [4](#0-3) . This means the token-exchange path can never detect a scope mismatch and will always accept a stored, unexpired session as "active" regardless of whether the app's declared scopes have since changed (e.g., merchant approved fewer scopes on reinstall, or the app owner reduced the scopes list in its configuration). The identical bug pattern occurs verbatim in the Express and React Router adapters: `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)` in `perform-token-exchange.ts` [5](#0-4)  and in the React Router `token-exchange.ts` strategy [6](#0-5) .

### Impact Explanation
Compared to the Auth Code Flow, which forces re-authorization whenever scopes have changed, requests authenticated through the token-exchange strategy silently keep using access tokens whose granted scope set may now exceed (or simply diverge from) what the app is currently configured/authorized to request. This undermines the scope-reduction/consent guarantee that `isActive(scopes)` is meant to enforce elsewhere in the same codebase, letting apps continue to operate with previously-granted, now-stale permissions instead of being forced through a fresh consent/OAuth cycle — the same "deactivation is bypassed via a sibling code path" pattern as the referenced Rolla finding.

### Likelihood Explanation
This triggers on every authenticated admin request that goes through the token-exchange strategy whenever there's a stored offline/online session for the shop — i.e., essentially all traffic once an app has `future.tokenExchange` enabled (the direction Shopify apps are moving toward) and any legitimate scope change occurs. No attacker action beyond normal use of App Bridge/session tokens is required.

### Recommendation
Pass the actual configured scopes into `isActive` on the token-exchange path in all three adapters (`shopify-app-remix`, `shopify-app-express`, `shopify-app-react-router`), mirroring the Auth Code Flow strategy's `session.isActive(config.scopes)` call, so that scope changes force a fresh token exchange/re-authorization instead of silently reusing a stale session.

### Proof of Concept
1. Install an app with scopes `read_products,write_products`; token exchange stores an offline session with `scope = "read_products,write_products"`.
2. App owner reduces `config.scopes` to `read_products` only (or merchant re-consents with fewer scopes via Shopify-managed installation), but the previously stored session/access token is still present and unexpired in session storage.
3. A subsequent authenticated request via the token-exchange strategy calls `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)`, which — because scopes is `undefined` — reports the session as active and reuses the old access token without checking that `write_products` is no longer an authorized scope, whereas the equivalent Auth Code Flow request (`session.isActive(config.scopes)`) would have detected the mismatch and forced re-authorization [7](#0-6) .

### Citations

**File:** packages/apps/shopify-api/lib/session/session.ts (L198-206)
```typescript
  public isActive(
    scopes: AuthScopes | string | string[] | undefined,
    withinMillisecondsOfExpiry = 500,
  ): boolean {
    const hasAccessToken = Boolean(this.accessToken);
    const isTokenNotExpired = !this.isExpired(withinMillisecondsOfExpiry);
    const isScopeChanged = this.isScopeChanged(scopes);
    return !isScopeChanged && hasAccessToken && isTokenNotExpired;
  }
```

**File:** packages/apps/shopify-api/lib/session/session.ts (L211-219)
```typescript
  public isScopeChanged(
    scopes: AuthScopes | string | string[] | undefined,
  ): boolean {
    if (typeof scopes === 'undefined') {
      return false;
    }

    return !this.isScopeIncluded(scopes);
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L85-90)
```typescript
    } else if (!session.isActive(config.scopes)) {
      logger.debug(
        'Found a session, but it has expired, redirecting to OAuth',
        {shop},
      );
      await redirectToAuthPage({config, logger, api}, request, shop);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-57)
```typescript
    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L84-84)
```typescript
    if (session && session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)) {
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts (L100-103)
```typescript
    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
```
