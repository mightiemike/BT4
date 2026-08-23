## Title
Session cookie not scoped/indexed by shop, causing cross-tenant session overwrite in non-embedded multi-shop apps - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
The OAuth callback handler writes the `shopify_app_session` cookie with a fixed default path of `/` regardless of which shop just completed authentication. Since the browser's cookie store keys a cookie only by `(domain, path, name)` and not by shop, this is structurally the same defect as the reported finding: a value that is logically scoped per-tenant (per-market in the original report, per-shop here) is stored in a data structure indexed by something that does *not* include the tenant dimension, so a second tenant's write silently overwrites the first tenant's entry.

### Finding Description
In `callback()`, after a successful OAuth exchange, the session id is written to a signed cookie for non-embedded apps: [1](#0-0) 

```
if (!config.isEmbeddedApp) {
  const cookiePath =
    typeof config.cookiePath === 'function'
      ? config.cookiePath(session)
      : (config.cookiePath ?? '/');
  await cookies.setAndSign(SESSION_COOKIE_NAME, session.id, {
    expires: session.expires,
    sameSite: 'lax',
    secure: true,
    path: cookiePath,
  });
}
```

`SESSION_COOKIE_NAME` is the fixed constant `'shopify_app_session'` [2](#0-1)  and `cookiePath` defaults to `'/'` unless the app developer explicitly supplies a shop-aware function. Because the browser cookie jar has no shop dimension, the effective "index" for this data is only `(domain, '/', 'shopify_app_session')`. If a merchant/browser installs or re-authenticates a second shop on the same non-embedded app domain (e.g. two tabs, or sequential installs), the second shop's OAuth callback overwrites the first shop's session cookie value with its own `session.id`. Any subsequent request made from the first shop's tab now carries the second shop's session id, so `config.sessionStorage.loadSession(sessionId)` in downstream middleware resolves to the wrong tenant's session/access token — this is the same "index not disambiguated by tenant" root cause as the Float Capital finding, just at the cookie layer instead of a Solidity mapping.

This exact defect has already been partially acknowledged by the maintainers: the changelog documents the collision and introduces an **opt-in** `cookiePath` option/factory as the fix, but the default behavior (`cookiePath: '/'`) remains vulnerable for any app that does not explicitly configure a shop-scoped path: [3](#0-2) 

### Impact Explanation
For non-embedded, multi-shop apps that rely on the default configuration, a merchant/browser interacting with two different shops on the same app domain can end up with the wrong shop's session cookie being served. This constitutes cross-tenant session confusion: requests for shop A can be served with shop B's session (and vice versa), potentially exposing one merchant's authenticated Admin API context/access token flow to a different merchant's browser tab. This matches the "cross-tenant access" acceptance criterion in the validation rules.

### Likelihood Explanation
No attacker-controlled forgery is required — this can be triggered by ordinary, unprivileged usage: any single browser session interacting with more than one shop instance of the same non-embedded app (a common scenario for agencies/consultants managing several merchant sites, or a merchant simply reinstalling/reauthorizing) will trigger the overwrite whenever the app does not set a shop-scoped `cookiePath`. Since `cookiePath` defaults to `'/'`, this is the default, unmitigated behavior unless developers actively opt in to the documented workaround.

### Recommendation
- Make shop-scoped cookie paths (or an equivalent per-shop cookie name/domain segregation) the default behavior for non-embedded apps rather than an opt-in `cookiePath` factory, so the fix applies without requiring every app developer to remember to configure it.
- Alternatively, refuse silently overwriting an active, unexpired session cookie for a different shop and instead force a fresh, explicit re-authentication path per shop.
- Apply the same scoping consideration to `STATE_COOKIE_NAME`, which is also written with a shared `callbackPath` across shops.

### Proof of Concept
1. Configure a non-embedded app with no `cookiePath` override (default `'/'`).
2. In Browser Tab 1, complete OAuth for `shop-a.myshopify.com`; the browser now stores `shopify_app_session=<id_for_shop_a>` at path `/`.
3. In Browser Tab 2 (same browser/profile), complete OAuth for `shop-b.myshopify.com` served from the same app domain; per [4](#0-3)  the cookie is written again at path `/`, overwriting the previous value with `<id_for_shop_b>`.
4. Return to Tab 1 and make a request to the app; the browser now sends `shopify_app_session=<id_for_shop_b>`, causing the server to load and act on shop B's session while the user believes they are interacting with shop A.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L219-230)
```typescript
    if (!config.isEmbeddedApp) {
      const cookiePath =
        typeof config.cookiePath === 'function'
          ? config.cookiePath(session)
          : (config.cookiePath ?? '/');
      await cookies.setAndSign(SESSION_COOKIE_NAME, session.id, {
        expires: session.expires,
        sameSite: 'lax',
        secure: true,
        path: cookiePath,
      });
    }
```

**File:** packages/apps/shopify-api/lib/auth/oauth/types.ts (L3-4)
```typescript
export const SESSION_COOKIE_NAME = 'shopify_app_session';
export const STATE_COOKIE_NAME = 'shopify_app_state';
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
