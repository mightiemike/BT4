This confirms the analog. The OAuth `state` cookie set in `begin()` carries a `expires: new Date(Date.now() + 60000)` attribute [1](#0-0) , but the server-side verification path (`getAndVerify` → `isSignedCookieValid`) never inspects the `expires` field at all — it only checks that the cookie exists and that its HMAC signature matches [2](#0-1) [3](#0-2) . This is directly analogous to the Nouns DAO finding: a state (`Expired`) that appears final/enforced by a declared boundary value (`GRACE_PERIOD` / cookie `expires`) but whose *actual* enforcement is delegated to an external, bypassable mechanism (timelock's mutable config / the browser's cookie-eviction behavior) rather than being checked at the point of validation.

### Title
OAuth `state` (CSRF) cookie has no server-side expiration enforcement, allowing indefinite replay of the 60-second nonce window - (File: `packages/apps/shopify-api/runtime/http/cookies.ts`)

### Summary
The `begin()` OAuth handler creates a signed `state` cookie intended to be valid for only 60 seconds by setting its `expires` attribute to `Date.now() + 60000` [1](#0-0) . However, when the OAuth `callback()` handler later retrieves and validates that cookie via `cookies.getAndVerify(STATE_COOKIE_NAME)` [4](#0-3) , the underlying `getAndVerify`/`isSignedCookieValid` implementation only checks cookie presence and HMAC signature correctness — it never compares the cookie's `expires` timestamp against the current time [5](#0-4) .

### Finding Description
The `expires` field on a `Set-Cookie` header is purely a client/browser-side instruction telling the browser when to stop sending the cookie; it carries no meaning once a raw `Cookie` header reaches the server. `Cookies.parseCookies` for *incoming* requests does not even attempt to reconstruct or check an `expires` value from the `Cookie` header (there is none to parse — that attribute is Set-Cookie-only) [6](#0-5) . Consequently, the library's own server-side signature verification (`isSignedCookieValid`) — the only gate protecting the OAuth `state`/CSRF cookie — has no time-bound check at all: as long as an attacker (or a proxy, extension, replay tool, or any actor able to resend a previously-issued `Cookie` header) can present a validly-signed `shopify_app_state` cookie together with a matching `state` query parameter and valid HMAC on the callback URL, `validQuery()` will accept it regardless of how much time has elapsed since it was issued [7](#0-6) .

This mirrors the Nouns DAO root cause exactly: a value/state (`expires` / `Expired`) that is declared to bound validity is not actually enforced at the check site, and enforcement is silently delegated elsewhere (the timelock's mutable `GRACE_PERIOD` for Nouns; the browser's cookie garbage-collection for shopify-app-js). In both cases, an attacker who can keep the "expired" credential alive past its intended window (a malicious/compromised browser extension, cached request replay, a man-in-the-middle-adjacent proxy that doesn't purge cookies, or simply an app/browser that doesn't honor `expires`) can present it well past the intended 60-second OAuth window.

### Impact Explanation
The `state` cookie is the CSRF protection for the OAuth authorization-code flow: `validQuery` requires `safeCompare(query.state!, stateFromCookie)` to succeed [7](#0-6) . If the state cookie can be replayed indefinitely rather than being strictly time-boxed to 60 seconds, the effective attack window for OAuth state-fixation/CSRF attacks (where an attacker gets a victim to complete an OAuth flow using an attacker-known state value, then completes it themselves, or captures a state cookie via XSS/leak and reuses it later) is significantly larger than the developers intended. This weakens (but does not eliminate, since the HMAC on the callback query and shop domain checks remain) one layer of defense against OAuth CSRF.

### Likelihood Explanation
Exploitability requires an attacker to obtain a previously-issued, still-validly-signed `state` cookie value (e.g., via a leaked `Set-Cookie` header, a shared/misconfigured caching layer, browser extension, or an XSS that read `document.cookie` — note the cookie is `httpOnly` by default in this library only if explicitly set, and is not explicitly marked `httpOnly` in `begin()`) and replay it after the intended 60-second window. This is a low-to-moderate likelihood scenario in isolation, but it is a defense-in-depth gap in unprivileged, internet-facing OAuth flow code reachable by any anonymous request to the app's `/auth/begin` and `/auth/callback` endpoints.

### Recommendation
Enforce the `expires` (or a separate `maxAge`) value server-side inside `isSignedCookieValid`/`getAndVerify`, rejecting and deleting the cookie if `Date.now()` is past the value that was set when the cookie was signed. Since the raw `expires` attribute isn't transmitted back to the server in the `Cookie` header, this requires embedding an expiry timestamp inside the signed payload itself (e.g., sign `state:issuedAtOrExpiryTimestamp` instead of just `state`, and validate that timestamp during verification) so that the OAuth flow's 60-second intent is actually enforced by the server, not just requested of the browser.

### Proof of Concept
1. Call `shopify.auth.begin(...)`, capture the resulting `shopify_app_state` and `shopify_app_state.sig` `Set-Cookie` values.
2. Wait more than 60 seconds (past the declared `expires`).
3. Replay the exact same `Cookie: shopify_app_state=...; shopify_app_state.sig=...` header on a call to `shopify.auth.callback(...)` with a matching `state` query parameter and valid HMAC.
4. Observe that `cookies.getAndVerify(STATE_COOKIE_NAME)` in `oauth.ts` line 168 still returns the state value and `validQuery` still succeeds — the OAuth callback completes successfully despite the cookie's declared 60-second expiration having passed, because `isSignedCookieValid` in `cookies.ts` never checks the `expires` timestamp [3](#0-2) .

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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L168-169)
```typescript
    const stateFromCookie = await cookies.getAndVerify(STATE_COOKIE_NAME);
    cookies.deleteCookie(STATE_COOKIE_NAME);
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

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L80-108)
```typescript
  static parseCookies(hdrs: string[]): CookieJar {
    const entries = hdrs
      .filter((hdr) => hdr.trim().length > 0)
      .map((cookieDef) => {
        const [keyval, ...opts] = cookieDef.split(';');
        const [name, value] = splitN(keyval, '=', 2).map((value) =>
          value.trim(),
        );
        return [
          name,
          {
            name,
            value,
            ...Object.fromEntries(
              opts.map((opt) =>
                splitN(opt, '=', 2).map((value) => value.trim()),
              ),
            ),
          },
        ];
      });
    const jar = Object.fromEntries(entries) as CookieJar;
    for (const cookie of Object.values(jar)) {
      if (typeof cookie.expires === 'string') {
        cookie.expires = new Date(cookie.expires);
      }
    }
    return jar;
  }
```

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L168-236)
```typescript
  async getAndVerify(name: string): Promise<string | undefined> {
    const value = this.get(name);
    if (!value) return undefined;
    if (!(await this.isSignedCookieValid(name))) {
      return undefined;
    }
    return value;
  }

  private get canSign() {
    return this.keys?.length > 0;
  }

  set(name: string, value: string, opts: Partial<CookieData> = {}): void {
    this.outgoingCookieJar[name] = {
      ...opts,
      name,
      value,
    };
    this.updateHeader();
  }

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
