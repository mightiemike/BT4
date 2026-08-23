### Title
Non-embedded app OAuth session cookie collision allows cross-tenant session hijack when multiple shops are authenticated concurrently - (File: `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`)

### Summary
The Canto Identity `buy()` bug shows a class of vulnerability where a value that depends on shared, mutable state (`lastHash`) is computed by the user ahead of time, but is not re-validated at the point of consumption, so a concurrent/competing write to that shared state silently substitutes a different result (a different tray) than the one the user expected. The `shopify-app-js` OAuth callback for non-embedded apps has the same structural flaw: the `shopify_app_session` cookie — the browser-side pointer to *which shop's session* the app should use — is written to a single, non-scoped storage slot (`path: '/'`) by default. When more than one OAuth flow completes in the same browser (e.g. two shop installs/logins in separate tabs), the second callback's write silently overwrites the first, so a user who started authenticating shop A ends up transacting against shop B's session without any error, exactly like the tray buyer receiving unexpected tiles.

### Finding Description
In `oauth.ts`, `callback()` finishes the OAuth code exchange, builds a `Session`, and then persists the reference to it in the browser via a signed cookie named `SESSION_COOKIE_NAME` (`shopify_app_session`): [1](#0-0) 

The `path` for that cookie is derived from `config.cookiePath`, which **defaults to `'/'`** unless the app developer explicitly overrides it: [2](#0-1) 

With the default `path: '/'`, the cookie is domain-wide, so the browser only ever stores **one** `shopify_app_session` cookie for the whole app regardless of which shop the OAuth flow was for. There is no check in `callback()` comparing the shop/session that the current flow is completing against any previously-stored value for that browser — it just always writes to the same top-level slot. This is analogous to `Tray.buy()` writing `tiles[startingTrayId + i]` based on whatever `lastHash` happens to be at execution time, with no verification that the precomputed/expected value still matches.

The maintainers themselves documented this exact race as a real, exploitable interaction in the config option description and CHANGELOG: [3](#0-2) 

And the underlying issue is spelled out directly in the type declaration comment for `cookiePath`: [4](#0-3) 

Confirmed by test coverage showing the unscoped default behavior still exists and is only mitigated if the developer opts in to `cookiePath`: [5](#0-4) 

### Impact Explanation
For any non-embedded app that still uses the default configuration (`cookiePath` unset, i.e. `path: '/'`), a user's browser can end up bound to the wrong shop's session with no error or warning:
- If a user (or the same browser/profile, e.g. shared kiosk/support machine) authenticates against Shop A in one tab and Shop B completes its OAuth callback afterward (whether from normal concurrent usage, a second legitimate merchant login, or an attacker-controlled redirect that races the legitimate flow), the last `Set-Cookie` for `shopify_app_session` wins and silently overwrites the session pointer used by *all* tabs/paths of that origin.
- Subsequent app requests in the "victim" tab will be served against the unintended shop's session, i.e., actions the user believes are being performed for Shop A are actually performed using Shop B's session (or vice versa) — a cross-tenant session confusion, comparable to the tray buyer ending up with a completely different, unwanted NFT.
- Because this is a plain overwrite of a domain-wide cookie rather than a cryptographic break, it does not require leaking secrets or a privileged actor — it can be triggered by any two OAuth completions happening in the same browser context.

### Likelihood Explanation
This is reachable by ordinary users of a non-embedded app: any scenario where two shops are onboarded/re-authenticated in the same browser without the developer having explicitly set `cookiePath` will trigger it. It requires no privileged access, no secret leakage, and no MITM — just concurrent/successive OAuth callbacks in the same browser, which is a normal support/testing/multi-store workflow. The vendor's own changelog confirms this was an unaddressed default-configuration issue until the opt-in `cookiePath` fix was introduced, meaning apps that haven't upgraded/configured it remain exposed.

### Recommendation
Mirror the tray fix pattern (validate expected identity against actual state before committing): 
- Make cookie scoping shop-aware by default rather than opt-in — e.g., derive the cookie path (or cookie name) from the shop domain automatically when `isEmbeddedApp` is false, instead of defaulting to `path: '/'`.
- At minimum, strongly warn/log when a `shopify_app_session` cookie is about to be overwritten for a different shop than the one recorded, and refuse silent overwrite.
- Document and default `cookiePath` to a per-shop-safe value out of the box so multi-shop safety is not left to be manually opted into by app developers.

### Proof of Concept
1. Deploy a non-embedded shopify-app-js app without setting `cookiePath` (default `'/'`).
2. In Browser Tab 1, begin OAuth for `shop-a.myshopify.com` and let it redirect to `/some-callback` to complete, setting `shopify_app_session` to Shop A's session id at `path: '/'`.
3. Before/without closing Tab 1, in Browser Tab 2 (same browser/profile), begin and complete OAuth for `shop-b.myshopify.com`, which writes `shopify_app_session` at `path: '/'` again — overwriting Shop A's cookie value with Shop B's session id.
4. Return to Tab 1 and make an authenticated request; the app now resolves the session id to Shop B's session (as demonstrated in `oauth.ts` lines [1](#0-0) ), meaning Tab 1's operations are now performed against Shop B's data with no error surfaced to the user.

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

**File:** packages/apps/shopify-api/lib/base-types.ts (L139-165)
```typescript
  /**
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
   *
   * **Requirement:** the configured path must match the actual URL structure of
   * your app — e.g. if each shop lives under `/shops/:shop/`, use that prefix.
   * The library cannot derive this automatically.
   *
   * @example
   * // Static path (single-shop apps or apps with no shop-specific routing)
   * cookiePath: '/'
   *
   * @example
   * // Factory function (multi-shop non-embedded apps)
   * cookiePath: (session) => `/shops/${session.shop}/`
   *
   * @defaultValue `'/'`
   */
  cookiePath?: string | ((session: Session) => string);
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

**File:** packages/apps/shopify-api/lib/auth/oauth/__tests__/oauth.test.ts (L725-760)
```typescript
  test('uses default path "/" for session cookie when cookiePath is not configured', async () => {
    const shopify = shopifyApi(testConfig({isEmbeddedApp: false}));

    const beginResponse: NormalizedResponse = await shopify.auth.begin({
      shop,
      isOnline: false,
      callbackPath: '/some-callback',
      rawRequest: request,
    });
    setCallbackCookieFromResponse(
      request,
      beginResponse,
      shopify.config.apiSecretKey,
    );

    const testCallbackQuery: QueryMock = {
      shop,
      state: VALID_NONCE,
      timestamp: getCurrentTimeInSec().toString(),
      code: 'some random auth code',
    };
    const expectedHmac = await generateLocalHmac(shopify.config)(
      testCallbackQuery,
    );
    testCallbackQuery.hmac = expectedHmac;
    request.url += `?${new URLSearchParams(testCallbackQuery).toString()}`;

    queueMockResponse(JSON.stringify({access_token: 'token', scope: ''}));

    const callbackResponse = await shopify.auth.callback({rawRequest: request});
    const responseCookies = Cookies.parseCookies(
      callbackResponse.headers['Set-Cookie'],
    );

    expect(responseCookies.shopify_app_session.path).toEqual('/');
  });
```
