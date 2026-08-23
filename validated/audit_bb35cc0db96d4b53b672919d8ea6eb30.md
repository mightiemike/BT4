Based on my research, I found a genuine but narrow analog: `Cookies.encodeCookie` in `packages/apps/shopify-api/runtime/http/cookies.ts` builds the `Set-Cookie` header by directly interpolating the cookie `name` and `value` (and any other attribute values) into a string with no escaping of `;`, `=`, quotes, or CR/LF, exactly the same root cause class as the Nextcloud bug (unescaped, attacker-influenceable data injected into a raw header string).

### Title
Unescaped cookie value interpolation in `Cookies.encodeCookie` enables Set-Cookie attribute injection - (File: packages/apps/shopify-api/runtime/http/cookies.ts)

### Summary
`Cookies.encodeCookie` builds the `Set-Cookie` header value by directly string-concatenating `name`, `value`, and every other `CookieData` property (`path`, `domain`, `sameSite`, etc.) with `;` and `=` separators, with no escaping of special characters (`;`, `,`, quotes, or control characters). [1](#0-0) 

### Finding Description
This is the same bug class as the reported Nextcloud issue: a value that ends up in an HTTP header is not properly escaped before being embedded in the header syntax, allowing an attacker-controlled value to inject additional header semantics (attribute smuggling / cookie splitting) rather than a benign quoted value. `set()`/`setAndSign()` accept an arbitrary `value` and `opts` and pass them straight to `encodeCookie` without sanitization. [2](#0-1) 

In the library's own usage, the cookie values that flow through this path in the OAuth begin/callback handlers are internally generated (`nonce()`, `session.id`) rather than raw user input, so within `oauth.ts` itself the sink is not directly reachable with attacker data. [3](#0-2) [4](#0-3) 

However, `Cookies` is a general-purpose, exported building block in the `runtime/http` module that any adapter or downstream app could call with request-derived data (e.g., a custom `cookiePath` function value, or any consumer code that calls `cookies.set()`/`setAndSign()` with data influenced by query parameters), and there is no guard in the shared primitive itself preventing header-breaking characters from being written into the `Set-Cookie` header.

### Impact Explanation
If a value containing `;`, `\r`, or `\n` reaches `Cookies.set`/`setAndSign` (e.g., via a consumer-supplied dynamic `cookiePath` or a custom integration built on this primitive), an attacker could inject extra `Set-Cookie` attributes (`HttpOnly`, `Secure`, `SameSite`, `Domain`, `Path`) or, on runtimes that don't split headers by newline internally, splice in an entirely separate header/cookie. This could weaken cookie security attributes (e.g., strip `Secure`/`HttpOnly`) or set an additional cookie under attacker control, which is a session/cookie-integrity issue analogous to the original disclosed weakness (unescaped data corrupting header structure).

### Likelihood Explanation
Low-to-Medium. The current callers within shopify-app-js (`oauth.ts` state/session cookies) only pass internally generated values (nonce, session id), so exploitation requires a downstream consumer or future code path that feeds attacker-controlled strings into `Cookies.set`/`setAndSign` — which is plausible given `cookiePath` is configurable as a function of the session and the class is a public, reusable primitive in the `runtime/http` surface used across adapters.

### Recommendation
Harden `Cookies.encodeCookie` (and `set`/`setAndSign`) to reject or percent/escape characters that break `Set-Cookie` syntax (`;`, `,`, control characters including CR/LF) in `name`, `value`, and all `CookieData` attribute values before interpolation, consistent with RFC 6265 cookie-octet restrictions, rather than relying on callers to pre-sanitize.

### Proof of Concept
1. As a consumer of the shared `runtime/http` `Cookies` class (or a future/plugin code path that reuses it), call:
```ts
cookies.set('session', 'abc\r\nSet-Cookie: evil=1; Domain=attacker.example', {path: '/'});
```
2. `encodeCookie` concatenates this into the outgoing `Set-Cookie` header verbatim: [1](#0-0) 
3. Depending on the underlying HTTP server/runtime's handling of the header array, this can inject additional cookie attributes or a second cookie not intended by the calling code, undermining the intended `Secure`/`HttpOnly`/`SameSite` protections that the OAuth flow relies on for the state/session cookies. [5](#0-4) 

**Caveat**: I could not find a currently reachable, anonymous-attacker-controlled path in this repo where request/query data is passed directly into `Cookies.set`/`setAndSign` — the only in-repo callers (`oauth.ts`) use internally generated nonces/session IDs. This finding documents a root-cause weakness in the shared primitive itself rather than a fully proven, presently-exploitable end-to-end path from an anonymous request.

### Citations

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L110-122)
```typescript
  static encodeCookie(data: CookieData): string {
    let result = '';
    result += `${data.name}=${data.value};`;
    result += Object.entries(data)
      .filter(([key]) => !['name', 'value', 'expires'].includes(key))
      .map(([key, value]) => `${key}=${value}`)
      .join('; ');
    if (data.expires) {
      result += ';';
      result += `expires=${data.expires.toUTCString()}`;
    }
    return result;
  }
```

**File:** packages/apps/shopify-api/runtime/http/cookies.ts (L181-203)
```typescript
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
```

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
