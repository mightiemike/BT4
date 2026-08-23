### Title
Non-embedded, multi-shop apps share a single `shopify_app_session`/`shopify_app_state` cookie key at `path=/`, allowing one shop's OAuth session to silently overwrite/collide with another - ([File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts])

### Summary
This is a direct analog of the M02 bug class: an identifier that is supposed to uniquely pin an in-flight transition to a specific entity (there: pre-state root + tx hash → State Transitioner; here: a fixed cookie name/path → per-shop OAuth session) does not actually vary per entity by default, so a legitimate flow for one entity can be silently clobbered or confused with another concurrently in-flight flow for a different entity.

### Finding Description
The OAuth `begin`/`callback` flow signs and stores the state nonce and the resulting session id in cookies named `shopify_app_state` and `shopify_app_session` [1](#0-0) . These cookies are written with `path: callbackPath` [2](#0-1) , which is the app's single, shop-agnostic OAuth callback route — not scoped per shop. Because browsers key cookies by (domain, path, name) and not by any application-level "shop" identifier, the cookie value is not a unique identifier of "this particular shop's OAuth transition"; it is shared across every shop that authenticates through the same app in the same browser.

The maintainers themselves acknowledge this exact collision in the changelog: "Non-embedded apps that need to support multiple shops simultaneously in separate browser tabs were affected by a cookie collision: all shops shared a single `shopify_app_session` cookie at `path=/`, so authenticating a new shop would silently overwrite the previous shop's session," and introduced an **opt-in** `cookiePath` option (`base-types.ts`) to scope cookies per shop [3](#0-2) . Since this remediation is optional and not the default, apps that do not explicitly configure `cookiePath` remain exposed to the underlying collision by default — exactly like the OVM_FraudVerifier bug, where the correct fix (indexing by `stateTransitionIndex`) was acknowledged but not implemented, leaving the non-unique identifier in production use.

### Impact Explanation
When a single browser (e.g., an agency/support user, or a merchant managing several stores) has concurrent or sequential OAuth flows open for different shops against the same non-embedded app instance, the state/session cookie for the second shop's flow overwrites the first shop's cookie value before it completes. This can cause: (1) the first shop's legitimate OAuth callback to fail (`CookieNotFound`/`InvalidOAuthError`) because its state was replaced, forcing OAuth to restart — the exact "prevents the original flow from completing/finalizing" pattern described in M02; and (2) after both flows nominally succeed, a tab still pointed at shop A silently operates using the cookie/session id that now resolves to shop B's session, i.e., session identity mixing across tenants within the same browser context.

### Likelihood Explanation
This requires only unprivileged/normal usage — any non-embedded, multi-shop-capable app (a common architecture for apps installed on many merchant domains and accessed by staff managing multiple shops) that has not opted into the new `cookiePath` factory function is affected by default. No attacker privilege escalation, secret leakage, or MITM is needed; ordinary use of multiple tabs/shops in one browser triggers it, which is why Shopify shipped a fix, confirming the report is exploitable in default configurations.

### Recommendation
Make shop-scoped cookie paths (or otherwise shop-namespaced cookie/session identifiers) mandatory rather than opt-in for non-embedded configurations that support multiple shops, or automatically derive a safe default (e.g., derive path from the shop domain) instead of relying on app developers to set `cookiePath` themselves. At minimum, detect and warn at configuration time when a non-embedded app has no `cookiePath` configured, since silent cross-shop session overwrite/collision has security implications beyond UX breakage.

### Proof of Concept
1. Deploy the app as non-embedded (`isEmbeddedApp: false`) without setting `cookiePath`.
2. In Browser Tab 1, start `shopify.auth.begin` for `shop-a.myshopify.com`; note the `shopify_app_state` cookie value is set at `path=callbackPath` for the app's single domain [4](#0-3) .
3. In Browser Tab 2 (same browser/profile), start `shopify.auth.begin` for `shop-b.myshopify.com` before completing Tab 1's flow; this overwrites the cookie set in step 2 since it shares the same name+path.
4. Complete OAuth in Tab 1 for `shop-a`: `stateFromCookie` no longer matches, and `validQuery` fails `safeCompare`, throwing `InvalidOAuthError`, aborting shop A's legitimate installation/session finalization [5](#0-4) .
5. Alternatively, if Tab 1 completes first and Tab 2 completes second, the browser's `shopify_app_session` cookie now stores shop B's session id, so any subsequent unauthenticated request from Tab 1 (still displaying shop A's UI) resolves to shop B's stored session via `getCurrentSessionId`/cookie lookup [6](#0-5) , demonstrating cross-tenant session confusion in a single browser context.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L25-35)
```typescript
import {
  SESSION_COOKIE_NAME,
  STATE_COOKIE_NAME,
  BeginParams,
  CallbackParams,
  AuthQuery,
  AccessTokenResponse,
} from './types';
import {nonce} from './nonce';
import {safeCompare} from './safe-compare';
import {createSession} from './create-session';
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L88-101)
```typescript
    const cookies = new Cookies(request, response, {
      keys: [config.apiSecretKey],
      secure: true,
    });

    const state = nonce();

    await cookies.setAndSign(STATE_COOKIE_NAME, state, {
      expires: new Date(Date.now() + 60000),
      sameSite: 'lax',
      secure: true,
      path: callbackPath,
    });

```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L242-255)
```typescript
async function validQuery({
  config,
  query,
  stateFromCookie,
}: {
  config: ConfigInterface;
  query: AuthQuery;
  stateFromCookie: string;
}): Promise<boolean> {
  return (
    (await validateHmac(config)(query)) &&
    safeCompare(query.state!, stateFromCookie)
  );
}
```

**File:** packages/apps/shopify-api/CHANGELOG.md (L129-150)
```markdown
- c439dab: Add `cookiePath` config option for multi-shop non-embedded apps

  Non-embedded apps that need to support multiple shops simultaneously in
  separate browser tabs were affected by a cookie collision: all shops shared
  a single `shopify_app_session` cookie at `path=/`, so authenticating a new
  shop would silently overwrite the previous shop's session.

  The new optional `cookiePath` config option lets you scope the session
  cookie to a shop-specific URL prefix, so each shop's cookie coexists
  independently in the browser.

  ```ts
  // Static path (default behaviour, unchanged)
  cookiePath: '/';

  // Factory function — recommended for multi-shop apps
  cookiePath: (session) => `/shops/${session.shop}/`;
  ```

  **Requirement:** the configured path must match your app's URL structure.
  Each shop must be served under a distinct URL prefix for the browser to
  deliver the correct cookie per request.
```

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L71-80)
```typescript
    } else {
      log.debug('App is not embedded, looking for session id in cookies', {
        isOnline,
      });

      const cookies = new Cookies(request, {} as NormalizedResponse, {
        keys: [config.apiSecretKey],
      });
      return cookies.getAndVerify(SESSION_COOKIE_NAME);
    }
```
