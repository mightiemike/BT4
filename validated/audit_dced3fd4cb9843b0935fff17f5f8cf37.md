### Title
Non-embedded app session cookie collision across shops due to fixed cookie path/name ("storage slot" overlap) - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
The OAuth callback handler for non-embedded apps stores the session id in a single, fixed-name cookie (`SESSION_COOKIE_NAME`) with a `path` that defaults to `/` unless the app developer explicitly opts in to a shop-scoped `cookiePath`. This is directly analogous to the reported storage-slot-collision bug class: two logically distinct "storage regions" (session data for shop A and shop B) are placed at the same address/slot (the same cookie name+path in the browser's cookie jar) by default, so writing one overwrites the other, producing data corruption/confusion rather than isolation.

### Finding Description
In `callback()`, after a successful OAuth exchange, the session id is written into a single cookie keyed by `SESSION_COOKIE_NAME` and a `path` that is `'/'` by default: [1](#0-0) 

This cookie is later read back for *any* shop context to resolve "the current session" without re-validating that the cookie's session actually belongs to the shop being requested: [2](#0-1) 

Because the cookie name/path is a single shared "slot", if the same browser/user context authenticates against a second shop (e.g., opens a second tab for a different store, or an attacker-controlled shop), the second `setAndSign(SESSION_COOKIE_NAME, ...)` call silently overwrites the first shop's session-id cookie at the same path — the library's own changelog documents this exact defect ("all shops shared a single `shopify_app_session` cookie at `path=/`, so authenticating a new shop would silently overwrite the previous shop's session") and introduces an *opt-in* `cookiePath` option to fix it: [3](#0-2) 

Critically, the mitigation is optional (`config.cookiePath` defaults to `'/'`), so any non-embedded app that has not explicitly configured a per-shop `cookiePath` factory remains vulnerable to this collision by default — exactly like the audited contract's default `__gap`/slot layout silently overlapping unless manually resized.

### Impact Explanation
When the cookie collides, a legitimate user browsing Shop A can have their session cookie silently replaced by Shop B's session id (e.g., due to a second tab, an iframe/link to another install, or an attacker luring the same browser to install/auth a malicious shop). Because `getCurrentSessionId` trusts the cookie value without cross-checking it against the shop in the current request, the app may subsequently load and act on Shop B's session/access token in a context the user or app logic believes belongs to Shop A, leading to cross-tenant session confusion and potential data leakage/actions against the wrong store. This is a genuine session-storage/cookie namespace collision, matching the "High impact" class of the original slot-collision report (data corruption/overwrite due to incorrect layout sizing — here, an incorrect default cookie scope).

### Likelihood Explanation
Likelihood is Medium: it requires a non-embedded app (embedded apps use JWT session tokens and are unaffected, since the cookie path only applies to the `!config.isEmbeddedApp` branch) that has not set a shop-specific `cookiePath`, and a scenario where the same browser authenticates more than one shop (multi-shop usage, or an attacker inducing the victim to complete OAuth for an attacker-controlled shop in the same browser). This is a realistic pattern for B2B/multi-store merchants and is explicitly called out by Shopify's own changelog as a real-world issue that shipped and had to be patched with an opt-in flag.

### Recommendation
Make shop-scoped cookie isolation the default rather than opt-in: derive the session cookie's `path` (or name) from the shop domain automatically in `callback()` (`packages/apps/shopify-api/lib/auth/oauth/oauth.ts`) unless a single-tenant deployment is explicitly configured, instead of defaulting to `path: '/'`. Additionally, `getCurrentSessionId`/`getCurrentSessionId` consumers should validate that the session id resolved from the cookie actually belongs to the shop in the current request context before treating it as authoritative, to prevent cross-tenant session use even if a collision occurs.

### Proof of Concept
1. Deploy a non-embedded app without setting `cookiePath` (default `'/'`).
2. In one browser, complete OAuth for `shop-a.myshopify.com`; the browser stores `shopify_app_session` (path `/`) = session id for shop A.
3. In the same browser, open a link/flow that completes OAuth for `shop-b.myshopify.com` (or is redirected there, e.g. via a crafted install link). The callback in `oauth.ts` overwrites `shopify_app_session` at the same path with shop B's session id.
4. The user returns to the shop A tab; the app's `getCurrentSessionId` reads the cookie, which now returns shop B's session id, and the app loads shop B's session/access token while operating in a URL/UI context the user believes is shop A — demonstrating the cross-tenant collision. [1](#0-0) [2](#0-1)

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L219-229)
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
