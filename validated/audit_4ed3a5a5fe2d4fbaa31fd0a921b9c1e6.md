## Analysis: parallel access-control paths in `validateAuthenticatedSession` (shopify-app-express)

I found a genuine analog to the "parallel mechanisms with inconsistent checks" bug class, in the same spirit as the Neptune Mutual finding (two independent code paths performing conceptually the same authorization decision, but enforcing different rules).

### Title
Scope-downgrade / stale-session bypass via inconsistent `isActive()` scope checks between the Auth-Code and Token-Exchange authentication paths - (File: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts` and `packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts`)

### Summary
`validateAuthenticatedSession` explicitly forks into two "fully independent" branches depending on whether token exchange is enabled, as noted in the code's own comment: [1](#0-0) . Both branches ultimately decide whether an existing stored `Session` is still valid enough to authorize a request, but they call `Session.isActive()` with different arguments, producing different security outcomes for the same underlying condition.

### Finding Description
In the legacy Auth-Code-flow branch (`validateWithAuthCodeFlow`), an existing session is treated as valid only if the currently configured scopes are still satisfied: [2](#0-1) 
This passes `api.config.scopes` into `isActive`, so `isScopeChanged` returns `true` when the app's required scopes have been changed/expanded and the stored session's `scope` no longer includes them — forcing re-authorization via `redirectOutOfApp`.

In the Token-Exchange branch (`performTokenExchange`), the equivalent check omits the scopes argument entirely: [3](#0-2) 
`Session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)` calls `isScopeChanged(undefined)`, which unconditionally returns `false`: [4](#0-3) 
So in this path, a session is considered "active" purely based on token expiry, regardless of whether its granted `scope` matches the app's currently configured scopes.

### Impact Explanation
If a merchant/customer session token is valid but was issued when the app required a narrower scope set than currently configured (e.g., the developer added new required scopes, or per-shop scope requirements were tightened), the token-exchange path will happily reuse the stale, under-scoped access token and hand it to `next()`/downstream handlers as an authenticated session — silently bypassing the re-authorization/consent step that the auth-code-flow path enforces for the exact same condition. This is a scope-downgrade/stale-authorization bypass reachable purely by presenting a session token (Authorization header) from any single merchant/customer to an app configured with `future.tokenExchange` and `isEmbeddedApp: true` — no privileged actor or secret leak is required.

### Likelihood Explanation
This is triggered automatically any time an app using the token-exchange strategy changes its required scopes after a merchant has already installed the app with a previously-issued (now stale-scope) access token still present in session storage. No attacker action beyond making a normal authenticated request with an existing App Bridge session token is needed; the two branches are selected purely by `config.future?.tokenExchange && api.config.isEmbeddedApp`, which is a config toggle, not something an external actor influences, but every app that has adopted token exchange (the currently recommended flow) is affected.

### Recommendation
Make the token-exchange path enforce the same scope-consistency check as the auth-code-flow path: call `session.isActive(api.config.scopes, WITHIN_MILLISECONDS_OF_EXPIRY)` in `performTokenExchange` (or otherwise unify the two "fully independent" branches into a single authorization decision function) so that scope changes always force re-exchange/re-consent, consistent with the auth-code flow's behavior.

### Proof of Concept
1. Configure a shopify-app-express app with `future.tokenExchange = true` and `isEmbeddedApp = true`, with `scopes = 'read_products'`.
2. Merchant installs; an offline session is stored with `scope: 'read_products'` and a valid `accessToken`/`expires`.
3. Developer updates the app config to require `scopes = 'read_products,write_orders'` and redeploys, without forcing merchants to re-consent.
4. A request arrives with a still-unexpired session token (`Authorization: Bearer <JWT>`).
5. `performTokenExchange` loads the stored offline session, calls `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)` — this returns `true` because scopes are not checked — and the request proceeds with the old under-scoped token as if it were fully authorized, per [3](#0-2) , whereas the same session under `validateWithAuthCodeFlow` would be rejected and redirected to reauthorize per [5](#0-4) .

**Caveat / uncertainty:** I could not find a CHANGELOG entry or test explicitly documenting this as an intentional design decision versus an oversight, and I could not verify via the index whether `has-valid-access-token.ts` (used only in the auth-code-flow branch) independently re-validates scopes against Shopify's Admin API for the token-exchange branch as a compensating control — the file wasn't retrieved in my searches. If such a compensating check exists elsewhere in the token-exchange flow, the practical impact would be reduced. I recommend a Devin session with full repo access to confirm this and check whether `shopify-app-remix`/`shopify-app-react-router`'s `token-exchange.ts` strategies (which also call `session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)`, per [6](#0-5) ) have the same gap.

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L25-36)
```typescript
      // One branch, up front: embedded apps that opted into token exchange use
      // it; everything else uses the legacy Auth Code flow. The two paths are
      // fully independent below.
      const useTokenExchange =
        config.future?.tokenExchange && api.config.isEmbeddedApp;

      if (useTokenExchange) {
        return validateWithTokenExchange({req, res, next, api, config});
      }

      return validateWithAuthCodeFlow({req, res, next, api, config});
    };
```

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L131-163)
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

      if (hasValidToken) {
        config.logger.debug('Request session has a valid access token', {
          shop: session.shop,
        });

        res.locals.shopify = {
          ...res.locals.shopify,
          session,
        };
        return next();
      }
    }
  }
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L84-89)
```typescript
    if (session && session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)) {
      logger.debug('Request is valid, session is active', {shop: session.shop});
      res.locals.shopify = {...res.locals.shopify, session};
      next();
      return;
    }
```

**File:** packages/apps/shopify-api/lib/session/session.ts (L198-219)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-57)
```typescript
    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
```
