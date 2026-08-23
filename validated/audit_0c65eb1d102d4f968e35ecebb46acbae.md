### Title
OAuth cookie signing/verification relies on a single, mutable current `apiSecretKey`, causing DoS of the callback auth handler when the key changes mid-flow - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Summary
`FluidEPProgramManager.cancelProgram()`'s root cause is recomputing a critical value (`initialDeposit`) using a *currently-read* mutable external parameter instead of the value that was fixed/used at creation time, causing later operations against that value to fail or diverge. The closest unprivileged analog in shopify-app-js is the OAuth `begin()`/`callback()` flow, where the state cookie is signed and later verified using only the single, current `config.apiSecretKey` value — with no mechanism to accept the key that was actually in effect when the cookie was created — so any change to that parameter between `begin()` and `callback()` breaks verification of otherwise legitimate, in-flight requests.

### Finding Description
In `begin()`, the state nonce cookie is signed using the currently configured secret: [1](#0-0) 

In `callback()`, the cookie is verified using `config.apiSecretKey` read again at verification time: [2](#0-1) 

The underlying `Cookies` class does support an *array* of keys for rotation (`isSignedCookieValid` iterates `this.keys.map(...)` and accepts a match against any of them): [3](#0-2) 

However, both `begin()` and `callback()` only ever construct `Cookies` with a single-element array, `keys: [config.apiSecretKey]`: [4](#0-3) [5](#0-4) 

There is no `previousApiSecretKey`/`apiSecretKeys` concept in the config surface — a grep of the codebase found no such field, confirming the app always re-derives the signing/verification key from whatever `config.apiSecretKey` currently holds, rather than persisting the exact key value used to sign the cookie. This exactly mirrors the audited bug's root cause: recomputing a value from a mutable "current" parameter instead of storing/using the original one.

The HMAC on the callback query string is also computed and re-validated using the live `config.apiSecretKey`: [6](#0-5) [7](#0-6) 

### Impact Explanation
If `config.apiSecretKey` is changed (e.g., during a credential rotation, config reload, or multi-instance deployment where instances briefly run with different secret values) between the time a shop/customer calls `begin()` and the time they complete `callback()`, the previously-signed `shopify_app_state` cookie will fail `isSignedCookieValid`, causing `getAndVerify` to return `undefined`, which throws `ShopifyErrors.CookieNotFound`: [8](#0-7) 

This is a denial-of-service of the OAuth `callback` auth handler for any user whose flow spans the rotation window — every legitimate install/reauth attempt in flight is forced to fail and restart the OAuth dance, with no graceful fallback to the prior key. This matches the accepted impact class ("DoS of an auth handler") from the analog bug pattern.

### Likelihood Explanation
This requires an external precondition analogous to the original report's "governance changes a parameter" — here, the app operator/host environment changing or reloading `apiSecretKey` (a normal, foreseeable operational event: secret rotation, blue/green deploys with different config, or credential vaulting refresh) while OAuth flows are in flight. It does not require a privileged attacker; any anonymous merchant/customer whose OAuth session happens to span the rotation window is affected. The state cookie's own short TTL (`expires: new Date(Date.now() + 60000)`) makes the exploitation window narrow but non-zero, and it is fully reachable from an unauthenticated request path.

### Recommendation
Support key rotation properly for the OAuth state cookie and HMAC validation, the same way the mitigation for the analog bug proposes storing the original value instead of recomputing from mutable state:
- Allow `config.apiSecretKey` to optionally be an ordered list (`[current, previous]`), and pass the full list into `new Cookies(request, response, {keys: [...]})` in both `begin()` and `callback()`, matching the `isSignedCookieValid` multi-key support that already exists in `Cookies`.
- Similarly allow `validateHmac`/HMAC generation to try the previous key as a fallback during a defined rotation grace period.
- Document that changing `apiSecretKey` should go through a two-phase rotation (add new key, wait out max cookie TTL, remove old key) instead of an atomic swap.

### Proof of Concept
Conceptual (not exploit code, since this requires an environment change, not attacker input):
1. Shop calls `shopify.auth.begin(...)`; cookie `shopify_app_state` is signed with `apiSecretKey = K1` at [1](#0-0) .
2. Before the shop's browser redirects back, the app's `apiSecretKey` config is rotated to `K2` (e.g., secret manager refresh, restart with new env var).
3. Shopify redirects the browser to `callback()`; `Cookies` is now constructed with `keys: [K2]` at [5](#0-4) , so `isSignedCookieValid` fails against the cookie signed with `K1`.
4. `getAndVerify` returns `undefined`, and `callback()` throws `ShopifyErrors.CookieNotFound`, aborting the OAuth flow for every in-flight user — a DoS of the auth handler until they manually retry after the rotation completes.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L88-100)
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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L163-176)
```typescript
    const cookies = new Cookies(request, response, {
      keys: [config.apiSecretKey],
      secure: true,
    });

    const stateFromCookie = await cookies.getAndVerify(STATE_COOKIE_NAME);
    cookies.deleteCookie(STATE_COOKIE_NAME);
    if (!stateFromCookie) {
      log.error('Could not find OAuth cookie', {shop});

      throw new ShopifyErrors.CookieNotFound(
        `Cannot complete OAuth process. Could not find an OAuth cookie for shop url: ${shop}`,
      );
    }
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L187-192)
```typescript
    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      code: query.get('code'),
      expiring: expiring ? '1' : '0',
    };
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

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L205-236)
```typescript
  async isSignedCookieValid(cookieName: string): Promise<boolean> {
    const signedCookieName = `${cookieName}.sig`;
    if (
      !this.cookieExists(cookieName) ||
      !this.cookieExists(signedCookieName)
    ) {
      this.deleteInvalidCookies(cookieName, signedCookieName);
      return false;
    }
    const cookieValue = this.get(cookieName);
    const signature = this.get(signedCookieName);

    if (!cookieValue || !signature) {
      this.deleteInvalidCookies(cookieName, signedCookieName);
      return false;
    }

    const allCheckSignatures = await Promise.all(
      this.keys.map((key) => createCookieSignature(key, cookieValue)),
    );

    const validSignature = allCheckSignatures.some((checkSignature) =>
      safelyCompareSignatures(checkSignature, signature),
    );

    if (!validSignature) {
      this.deleteInvalidCookies(cookieName, signedCookieName);
      return false;
    }

    return true;
  }
```
