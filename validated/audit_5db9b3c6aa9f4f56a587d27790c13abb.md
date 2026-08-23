### Title
Non-Embedded App Session Cookie Collision Allows Cross-Shop Session Overwrite (Front-Running Analog) - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
For non-embedded apps, `shopify.auth.callback` writes the post-OAuth session identifier into a single, shop-agnostic cookie (`shopify_app_session`) scoped to a default path of `/`. Because the cookie is not bound to a specific shop unless the app opts in to the `cookiePath` configuration, any OAuth completion that happens in the same browser — including one triggered against a different, attacker-controlled shop — silently overwrites the cookie value that a genuine merchant's browser is relying on. This mirrors the `enrollCourier()` front-running pattern: whichever OAuth flow *finishes last* in that browser "claims" the shared cookie slot, denying/hijacking the legitimate flow that started (or was expected to be used) first.

### Finding Description
In `begin()`, the OAuth state nonce cookie is scoped to `callbackPath`, but in `callback()` the session cookie's path defaults to `/` unless a `cookiePath` function/string is explicitly configured: [1](#0-0) 

Because the cookie name (`SESSION_COOKIE_NAME` = `shopify_app_session`) and default path (`/`) are identical for every shop the app serves, the cookie is a single shared slot per browser rather than a per-shop slot. Any browser-tab OAuth completion — for any shop the attacker controls or can lure the victim into visiting — will overwrite this cookie's value with a different session id, exactly as an attacker in the `enrollCourier()` report front-runs a shared `courierId` slot to lock out the legitimate claimant.

This was acknowledged by the Shopify team itself in the changelog, which explicitly describes the "cookie collision" symptom and introduces `cookiePath` as an *opt-in* mitigation: [2](#0-1) 

Since `cookiePath` defaults to `/` when not configured, any app that has not explicitly adopted the new option remains exposed.

### Impact Explanation
- **Denial of Service**: A legitimate merchant's active `shopify_app_session` cookie can be silently replaced by a session id belonging to a different shop, causing subsequent authenticated requests to fail session lookup or resolve to the wrong shop context (`loadSession` returning a session tied to a different tenant).
- **Cross-tenant confusion**: If the app blindly trusts the cookie-derived session id to select tenant context, a victim's browser could end up operating against the attacker's shop session (or vice versa) until the mismatch is detected, since nothing in the cookie itself binds it to the shop the user believes they're interacting with.
- This is only reachable for **non-embedded apps** (`config.isEmbeddedApp === false`) that have not set the `cookiePath` option, matching the "unprivileged/no special access" reachability requirement — the attacker only needs to get any OAuth flow to complete in the victim's browser (e.g., via a lured link/redirect to the app's `/auth?shop=<attacker-shop>` endpoint).

### Likelihood Explanation
Medium: it requires a non-embedded app configuration that has not adopted `cookiePath`, and it requires getting a second OAuth completion to run in the same browser as the victim (e.g., via a crafted link or iframe navigation to the app's own `/auth` endpoint pointed at an attacker/free shop). No secrets or privileged access are needed; HMAC/state validation on the callback itself is not bypassed — the exploit abuses the shared cookie storage location rather than forging Shopify's callback.

### Recommendation
- Make cookie scoping per-shop by default (e.g., derive `cookiePath` from the shop domain automatically) rather than defaulting to `/`, so `cookiePath` is not an opt-in fix but the standard behavior.
- Alternatively, bind the session cookie value to the requested `shop` and validate that the shop in the current request context matches the shop encoded in the loaded session before treating it as authenticated, rejecting mismatches instead of proceeding.

### Proof of Concept
1. Deploy a non-embedded Shopify app without configuring `cookiePath` (default `'/'`).
2. Victim installs/logs into the app for `victim-shop.myshopify.com`; browser stores `shopify_app_session=offline_victim-shop...` at path `/`.
3. Attacker gets the victim's browser to hit the app's `/auth?shop=attacker-shop.myshopify.com` (e.g., via a link, or automatically if the app auto-redirects to auth on certain conditions) and completes OAuth for `attacker-shop` (attacker owns/controls this shop, so completing OAuth for it is trivial).
4. `callback()` executes `cookies.setAndSign(SESSION_COOKIE_NAME, session.id, {..., path: '/'})` for `attacker-shop`, overwriting the victim's cookie value at the shared path.
5. Victim's next request now carries a session id for `attacker-shop` instead of `victim-shop`, breaking the victim's session (DoS) or causing cross-tenant session confusion if the app doesn't independently verify shop-to-session binding.

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
