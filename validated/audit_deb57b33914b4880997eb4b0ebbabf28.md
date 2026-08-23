This confirms a directly analogous, already-documented bug class in shopify-app-js: the OAuth session cookie for non-embedded apps is written with a domain-wide `path: '/'` by default, so authenticating a second shop overwrites the first shop's session cookie in the browser, mirroring the report's "second grab overwrites the previous owner record, and the original state cannot be recovered."

### Title
Non-embedded OAuth session cookie collision causes cross-tenant session overwrite - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Summary
In the `callback` function of `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`, the `shopify_app_session` cookie is signed and set with a `path` that defaults to `/` for non-embedded apps unless the app developer explicitly configures `cookiePath`. Because the cookie is scoped browser-wide (not per shop), completing OAuth for a second shop in a different tab overwrites the cookie tied to the first shop, causing the first tab/session's "identity" to be silently replaced — analogous to the reported Witch.sol issue where a second `grab` overwrites `vaultOwners[vaultId]` and the original owner information is unrecoverable.

### Finding Description
`begin()` sets a signed `STATE_COOKIE_NAME` cookie scoped to `callbackPath` [1](#0-0) , and `callback()` later writes the `SESSION_COOKIE_NAME` cookie using `cookiePath`, which defaults to `'/'` when not configured: [2](#0-1) . The `cookiePath` config field itself documents this exact collision: "By default the cookie is written with `path: '/'`, making it domain-wide. This means that when a user authenticates multiple shops in separate tabs, each OAuth callback overwrites the previous cookie, causing all tabs to use the most-recently-authenticated shop." [3](#0-2)  This is also captured verbatim in the changelog describing the fix option: [4](#0-3) 

Because `getCurrentSessionId` for non-embedded apps derives the session identity solely from this cookie (`cookies.getAndVerify(SESSION_COOKIE_NAME)`), whichever shop authenticated most recently "wins" the shared cookie: [5](#0-4) . A tab that had a legitimate session for Shop A silently starts using Shop B's session id after Shop B completes OAuth in another tab, with no error or state recorded to recover Shop A's prior session binding — the same "information of the original owner is lost" root cause as the Witch.sol report.

### Impact Explanation
This is a cross-tenant session/identity confusion issue in a merchant-facing HTTP flow, reachable by any user with two shop installs of the same non-embedded app open in separate tabs (or, more concerning, an attacker who can induce/observe a victim completing OAuth for a different shop context on shared infrastructure). The victim's browser tab retains stale application state bound to Shop A but the authentication cookie now points at Shop B's session id, and vice versa, which can result in a user unknowingly performing app actions (or being shown data) under the wrong shop's authenticated session once redirected/reloaded. This is opt-in mitigated only if the developer sets `cookiePath`; by default the library ships with the collision-prone behavior.

### Likelihood Explanation
Requires only a legitimate, unprivileged flow: a merchant or app installer completing OAuth for a second shop in a non-embedded app while another tab retains a session for a first shop — no privileged access or secret leakage needed. Likelihood is moderate and scoped to non-embedded, multi-shop scenarios (embedded apps are unaffected since `cookiePath` write is gated by `!config.isEmbeddedApp`) [6](#0-5) .

### Recommendation
Default `cookiePath` to a per-shop derivation (e.g., encode the shop in the path or use a shop-namespaced cookie name) rather than defaulting to `/`, or require the setup to explicitly acknowledge the multi-shop tradeoff before allowing non-embedded OAuth without a `cookiePath` scoping function, similar to the report's recommendation to either track a stack of prior owners or explicitly guard against overwriting existing state.

### Proof of Concept
1. Non-embedded app configured without `cookiePath` (default `'/'`).
2. Tab 1: user completes OAuth for `shop-a.myshopify.com` → browser receives `shopify_app_session` cookie signed for Shop A's session id at `path=/`.
3. Tab 2 (or same browser, different flow): user completes OAuth for `shop-b.myshopify.com` → `callback()` in `oauth.ts` again sets `shopify_app_session` at `path=/`, overwriting the cookie value with Shop B's session id: [7](#0-6) .
4. Returning to Tab 1 and reloading, `getCurrentSessionId` resolves the (now Shop B) session id from the shared cookie [8](#0-7) , silently mixing shop contexts in what the user believes is their Shop A tab, with no mechanism to recover the original binding.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L93-100)
```typescript
    const state = nonce();

    await cookies.setAndSign(STATE_COOKIE_NAME, state, {
      expires: new Date(Date.now() + 60000),
      sameSite: 'lax',
      secure: true,
      path: callbackPath,
    });
```

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

**File:** packages/apps/shopify-api/lib/base-types.ts (L140-149)
```typescript
   * The path to use for the OAuth session cookie in non-embedded apps.
   *
   * By default the cookie is written with `path: '/'`, making it domain-wide.
   * This means that when a user authenticates multiple shops in separate tabs,
   * each OAuth callback overwrites the previous cookie, causing all tabs to use
   * the most-recently-authenticated shop.
   *
   * Set this to a string or a function returning a string to scope the cookie to
   * a URL path prefix that is unique per shop. The browser will then maintain
   * one cookie per shop and deliver only the matching one per request.
```

**File:** packages/apps/shopify-api/CHANGELOG.md (L129-138)
```markdown
- c439dab: Add `cookiePath` config option for multi-shop non-embedded apps

  Non-embedded apps that need to support multiple shops simultaneously in
  separate browser tabs were affected by a cookie collision: all shops shared
  a single `shopify_app_session` cookie at `path=/`, so authenticating a new
  shop would silently overwrite the previous shop's session.

  The new optional `cookiePath` config option lets you scope the session
  cookie to a shop-specific URL prefix, so each shop's cookie coexists
  independently in the browser.
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
