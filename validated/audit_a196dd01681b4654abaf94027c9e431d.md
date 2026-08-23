### Title
Token-exchange session-reuse check omits scope validation, allowing under-scoped stale sessions to bypass re-authorization - (Files: `packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts`, `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts`, `packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts`)

### Summary
This is the same bug class as the LidoVault `vaultEndedWithdraw` finding: a finalize/short-circuit path skips a check that the "canonical" code path (here, `validateWithAuthCodeFlow`) still performs, silently accepting a state that should instead trigger a fresh action. In LidoVault the shortcut skipped claiming pending fixed withdrawals before finalizing state; here the shortcut skips the scope-match check before treating a cached session as authoritative and calling `next()`/returning it.

### Finding Description
All three token-exchange implementations reuse a cached session with:
```
session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
``` [1](#0-0) [2](#0-1) 

`Session.isActive` treats an `undefined` scopes argument as "scopes unchanged" (`isScopeChanged` returns `false` immediately when `scopes === undefined`), so no scope comparison is performed at all: [3](#0-2) 

Contrast this with the legacy Auth Code Flow path in the same Express package, which deliberately passes `api.config.scopes` to require a scope match before reusing a session: [4](#0-3) 

and `ensure-installed-on-shop.ts`, which also calls `session.isActive(api.config.scopes)` before treating a session as valid: [5](#0-4) 

The `isActive()` scopes parameter exists specifically to detect "app has been restarted with new [required] scopes" per the library's own migration docs, and its absence means a session is deemed active regardless of scope drift: [6](#0-5) 

As a result, when the token-exchange path (`config.future.tokenExchange`) is used, a previously-stored offline/online session whose granted scope no longer matches the app's currently configured `scopes` is accepted as-is and handed to the route handler — the token-exchange flow that would otherwise re-request an access token with the correct/updated scope set is skipped entirely.

### Impact Explanation
If a merchant partially reduces or the developer increases required scopes (a normal operational event, e.g. app publishes a new version requiring an additional scope), requests that go through the token-exchange middleware will continue to use the stale, under/mis-scoped access token indefinitely rather than being forced to re-authorize, because the only gate (`isActive`) is never told what scopes to check. This is an authorization-state integrity gap: code paths that depend on a session's granted scope being current (billing, GraphQL calls requiring newly required scopes, etc.) can silently operate against a token whose actual permission set no longer matches what the app believes it has, and re-consent/re-auth that should occur on scope changes never triggers via this route.

### Likelihood Explanation
High for occurrence, moderate for security-relevant impact: this executes on every authenticated request through the `tokenExchange` future-flag path (an anonymous request bearing any previously-issued session token for the shop can hit it), and requires no special conditions besides the app having changed its scope set at some point since the stored session was created — a normal event in production app lifecycles.

### Recommendation
Pass the app's configured scopes into the `isActive` check in all three token-exchange implementations, mirroring the Auth Code Flow behavior:
```ts
if (session && session.isActive(api.config.scopes, WITHIN_MILLISECONDS_OF_EXPIRY)) { ... }
```
so a scope mismatch forces the offline/online token-exchange re-fetch instead of silently reusing the stale session.

### Proof of Concept
1. Install the app while it requires scopes `read_products`.
2. Developer updates the app to require `read_products,write_orders` and redeploys; no new OAuth/token-exchange occurs for existing merchants yet.
3. A request arrives through `shopify.validateAuthenticatedSession()` (Express) or the Remix/React Router `authenticate.admin` token-exchange strategy with a valid session token for that shop.
4. `performTokenExchange`/`token-exchange.ts` loads the stored session (scope = `read_products`) and calls `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)`, which returns `true` purely because the token isn't expired — scope is never compared. [1](#0-0) 
5. The request proceeds to `next()`/handler with the old, under-scoped session instead of triggering `exchangeToken` to obtain a session reflecting the updated scope requirement.

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L84-89)
```typescript
    if (session && session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)) {
      logger.debug('Request is valid, session is active', {shop: session.shop});
      res.locals.shopify = {...res.locals.shopify, session};
      next();
      return;
    }
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/strategies/token-exchange.ts (L100-103)
```typescript
    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
```

**File:** packages/apps/shopify-api/lib/session/session.ts (L194-219)
```typescript
  /**
   * Whether the session is active. Active sessions have an access token that is not expired, and has has the given
   * scopes if scopes is equal to a truthy value.
   */
  public isActive(
    scopes: AuthScopes | string | string[] | undefined,
    withinMillisecondsOfExpiry = 500,
  ): boolean {
    const hasAccessToken = Boolean(this.accessToken);
    const isTokenNotExpired = !this.isExpired(withinMillisecondsOfExpiry);
    const isScopeChanged = this.isScopeChanged(scopes);
    return !isScopeChanged && hasAccessToken && isTokenNotExpired;
  }

  /**
   * Whether the access token includes the given scopes if they are provided.
   */
  public isScopeChanged(
    scopes: AuthScopes | string | string[] | undefined,
  ): boolean {
    if (typeof scopes === 'undefined') {
      return false;
    }

    return !this.isScopeIncluded(scopes);
  }
```

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L131-149)
```typescript
  if (session) {
    config.logger.debug('Request session found and loaded', {
      shop: session.shop,
    });

    if (session.isActive(api.config.scopes)) {
      config.logger.debug('Request session exists and is active', {
        shop: session.shop,
      });

      let hasValidToken: boolean;
      try {
        hasValidToken = await hasValidAccessToken(api, session);
      } catch (error) {
        config.logger.error(`Could not check if session was valid: ${error}`, {
          shop: session.shop,
        });
        hasValidToken = false;
      }
```

**File:** packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts (L143-156)
```typescript
async function sessionHasValidAccessToken(
  api: Shopify,
  config: AppConfigInterface,
  session: Session | undefined,
): Promise<boolean> {
  if (!session) {
    return false;
  }

  try {
    return (
      session.isActive(api.config.scopes) &&
      (await hasValidAccessToken(api, session))
    );
```

**File:** packages/apps/shopify-api/docs/migrating-to-v6.md (L326-335)
```markdown
1. The `isActive()` method of `Session` now takes a `scopes` parameter. If the scopes of the session don't match the scopes of the application (e.g., app has been restarted with new scopes), the session is deemed to be inactive and OAuth should be initiated again.
   <div>Before

   ```ts
   const session = await Shopify.Utils.loadCurrentSession(req, res);

   if (!session.isActive()) {
     // current session is not active - either expired or scopes have changed
   }
   ```
```
