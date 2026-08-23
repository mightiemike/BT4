### Title
Signed cookie MAC does not bind cookie name, allowing cross-cookie signature confusion - (File: `packages/apps/shopify-api/runtime/http/cookies.ts`)

### Summary
Analogous to the MagnetarV2 `_permit` bug — where the permit-verification routine checked only that decoded parameters had the right *shape* (msg.sender match, argument length) but never checked *which operation* (function selector) the calldata actually represented, letting an attacker substitute a different call under the "permit" label — the `Cookies` signed-cookie mechanism in `shopify-app-js` verifies only that `HMAC(key, value) == signature`, without ever binding the cookie's *name/purpose* into the signed material.

### Finding Description
`createCookieSignature` computes the MAC over the cookie **value alone**: [1](#0-0) [2](#0-1) 

`setAndSign` stores `value` under `name` and `signature = createCookieSignature(key, value)` under `${name}.sig`: [3](#0-2) 

`isSignedCookieValid`/`getAndVerify` only check that some key produces `HMAC(key, cookieValue) == signature`; the cookie's `cookieName` is read from the jar but is **never included as MAC input** — it is used purely to *look up* the value/signature pair, not to authenticate that the value was signed for that specific purpose: [4](#0-3) [5](#0-4) 

This class of signing is used by two distinct security-critical cookies sharing the same key derivation (`COOKIE_SIGNING_INFO = 'shopify-app-js/cookie-signing/v1'`, i.e. no per-purpose domain separation beyond the single derived key):
- `STATE_COOKIE_NAME` (`shopify_app_state`) — the OAuth CSRF nonce, set in `begin()` and checked against the callback `state` query param in `callback()`: [6](#0-5) [7](#0-6) 
- `SESSION_COOKIE_NAME` (`shopify_app_session`) — the non-embedded session id cookie, read back via `getCurrentSessionId`/`cookies.getAndVerify(SESSION_COOKIE_NAME)`: [8](#0-7) 

Because the MAC never binds the cookie name/purpose, a `(value, signature)` pair legitimately produced by the server for one cookie (e.g. the state nonce) is cryptographically indistinguishable from — and will be accepted as valid for — any *other* cookie name whose `getAndVerify`/`isSignedCookieValid` call uses the same value, since verification never checks "was this value signed as a *state* cookie" vs "signed as a *session* cookie". This mirrors the root cause in H-49: `MagnetarV2._permit` decoded calldata and validated shape/constraints but never confirmed the calldata's *declared purpose* (the function selector) matched what was actually executed.

An existing regression test explicitly documents developers' awareness that a *cookie signature* value can be replayed as a *webhook* HMAC and demonstrates it correctly fails only because webhook HMAC uses a completely different algorithm/keying (raw-body HMAC-SHA256 vs derived-key cookie HMAC), not because the cookie signature itself binds to a name/purpose: [9](#0-8) 

### Impact Explanation
Exploitation requires the attacker to control (or predict) the *value* that gets signed under one cookie name and then present that exact `(value, signature)` pair under a different cookie name that the app later trusts via `getAndVerify`. In the current codebase, the two cookies that use this mechanism carry server-generated random values (`nonce()` for state, `session.id` UUID for session), which limits practical cross-cookie substitution today. However, the underlying primitive (`Cookies.setAndSign`/`getAndVerify`) is a general-purpose, exported building block used by downstream adapters (`shopify-app-express`, custom integrations) and any future/custom usage that signs cookies containing attacker-influenced or shared values would be silently vulnerable to type confusion between cookies, since nothing in the API enforces or documents that the cookie name is part of the authenticated context. This is a structural weakness in the signing primitive rather than a proven, currently-reachable full session/CSRF bypass in the shipped OAuth/session code paths I could verify.

### Likelihood Explanation
Low-to-moderate as currently wired (both consuming cookies use unpredictable, server-generated random values, so an attacker cannot force overlap), but the missing domain separation is a latent defect in a shared, reusable primitive (`Cookies`) that any current or future consumer (first-party packages or third-party apps built on `shopify-app-js`) could unknowingly rely on unsafely, exactly as the original report's underlying primitive (`_permit`) was reused as a generic "call anything with an approved shape" primitive across the periphery.

### Recommendation
Bind the cookie's `name` (and ideally a version/purpose tag) into the MAC input, e.g. compute `createCookieSignature(key, `${name}:${value}`)` (or use the name as HMAC associated data / AAD) in both `setAndSign` and `isSignedCookieValid`, so a signature produced for one cookie name can never validate for another cookie name even if the underlying value happens to match.

### Proof of Concept
Not independently reproducible against the shipped OAuth/session flows because both consuming cookies use unpredictable server-generated values, preventing an attacker from forcing the state and session cookie values to collide. The unit test `cookie.integrity.test.ts` confirms the verification logic: `isSignedCookieValid` accepts `(cookieValue, signature)` purely based on `HMAC(key, cookieValue) === signature`, with no reference to `cookieName` in the MAC computation, demonstrating the missing name binding at the primitive level: [10](#0-9)

### Citations

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L48-56)
```typescript
const COOKIE_SIGNING_INFO = 'shopify-app-js/cookie-signing/v1';

async function createCookieSignature(
  key: string,
  value: string,
): Promise<string> {
  const cookieSigningKey = await deriveSHA256HMACKey(key, COOKIE_SIGNING_INFO);
  return createSHA256HMAC(cookieSigningKey, value);
}
```

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L168-175)
```typescript
  async getAndVerify(name: string): Promise<string | undefined> {
    const value = this.get(name);
    if (!value) return undefined;
    if (!(await this.isSignedCookieValid(name))) {
      return undefined;
    }
    return value;
  }
```

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L190-203)
```typescript
  async setAndSign(
    name: string,
    value: string,
    opts: Partial<CookieData> = {},
  ): Promise<void> {
    if (!this.canSign) {
      throw Error('No keys provided for signing.');
    }
    this.set(name, value, opts);
    const sigName = `${name}.sig`;
    const signature = await createCookieSignature(this.keys[0], value);
    this.set(sigName, signature, opts);
    this.updateHeader();
  }
```

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L205-230)
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
```

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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L163-183)
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

    const authQuery: AuthQuery = Object.fromEntries(query.entries());
    if (!(await validQuery({config, query: authQuery, stateFromCookie}))) {
      log.error('Invalid OAuth callback', {shop, stateFromCookie});

      throw new ShopifyErrors.InvalidOAuthError('Invalid OAuth callback.');
    }
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

**File:** packages/apps/shopify-api/lib/webhooks/__tests__/validate.test.ts (L97-125)
```typescript
  it('returns false when a cookie signature is replayed as the webhook HMAC', async () => {
    const shopify = shopifyApi(testConfig());
    const app = getTestApp(shopify);
    const cookieValue = 'oauth-state-nonce';
    const cookieResponse = {} as NormalizedResponse;
    const cookieJar = new Cookies(
      {headers: {}} as NormalizedRequest,
      cookieResponse,
      {keys: [shopify.config.apiSecretKey]},
    );
    await cookieJar.setAndSign('shopify_app_state', cookieValue);

    const response = await request(app)
      .post('/webhooks')
      .set(
        headers({
          hmac: cookieJar.outgoingCookieJar['shopify_app_state.sig'].value,
          topic: 'app/uninstalled',
          domain: 'victim-shop.myshopify.io',
        }),
      )
      .send(cookieValue)
      .expect(200);

    expect(response.body.data).toEqual({
      valid: false,
      reason: WebhookValidationErrorReason.InvalidHmac,
    });
  });
```

**File:** packages/apps/shopify-api/runtime/http/__tests__/cookie.integrity.test.ts (L68-81)
```typescript
    it('should return true if signature matches', async () => {
      (cookies.get as jest.Mock).mockImplementation((name) => {
        if (name === 'testCookie') return 'cookieValue';
        if (name === 'testCookie.sig') return 'validSignature';
        return undefined;
      });

      (createSHA256HMAC as jest.Mock).mockResolvedValue('validSignature');

      const result = await cookies.isSignedCookieValid('testCookie');

      expect(result).toBe(true);
      expect(cookies.deleteCookie).not.toHaveBeenCalled();
    });
```
