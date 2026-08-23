### Title
Backslash-based host swap bypasses `isSafe`'s slash-only regex checks, enabling open redirect - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts])

### Summary
`isSafe` in `validate-redirect-url.ts` filters malicious redirect targets using regexes that only look for literal forward slashes (`FILE_URI_MATCH`, `INVALID_RELATIVE_URL`) or whitespace (`WHITESPACE_CHARACTER`), but never inspects backslashes. Because the WHATWG `URL` parser (used internally by Node/browsers) treats `\` identically to `/` for special schemes like `https:`/`http:`, a string such as `/\evil.com/path` is silently resolved to host `evil.com` when parsed with a `myapp.com` base, completely bypassing all the checks in `isSafe`.

### Finding Description
`isSafe` performs its "is this attacker string dangerous" checks in this order [1](#0-0) :

1. `FILE_URI_MATCH = /\/\/\//` and `WHITESPACE_CHARACTER = /\s/` are tested against the *raw, attacker-supplied* `redirectUrl` string.
2. The string is then parsed with `new URL(redirectUrl, domain)`.
3. `INVALID_RELATIVE_URL = /[/\\][/\\]/` is tested against the *resulting* `url.pathname` (not the original string, and not `url.host`).
4. `url.protocol` is checked against `VALID_PROTOCOLS` and against `requireSSL`.

Critically, **no code path ever compares `url.hostname`/`url.host` to the app's own domain**. The function assumes that if the raw string doesn't look like an absolute authority (`///` triple slash) and the resulting pathname doesn't have doubled slashes, the parsed URL must still be scoped to `domain`. That assumption is false for inputs mixing `/` and `\`.

For a special scheme (`http`/`https`), the WHATWG URL parsing algorithm treats `\` as equivalent to `/` when determining whether an authority (host) section follows. Given `domain = 'https://myapp.com'` and `redirectUrl = '/\\evil.com/path'` (i.e. `/\evil.com/path`):
- `FILE_URI_MATCH` (three literal `/`) does not match — the string only has one real `/` plus a `\`.
- `WHITESPACE_CHARACTER` does not match.
- `new URL('/\\evil.com/path', 'https://myapp.com')` is parsed by the spec's relative-slash state: since the scheme is special and the second character is `\`, the parser enters "special authority ignore slashes" state and treats `evil.com` as the new **host**, producing `https://evil.com/path`.
- `INVALID_RELATIVE_URL` is tested against `url.pathname`, which is now just `/path` — no doubled slash/backslash remains, since the host-swap already consumed the leading `/\`.
- `url.protocol` is `https:`, which passes `VALID_PROTOCOLS` and any `requireSSL` check.

`isSafe` therefore returns `true`, and `sanitizeRedirectUrl` returns a `URL` object whose host is `evil.com`, not the app's domain, even though the exported contract of this helper is to sanitize a redirect target so it cannot escape the app's own origin.

### Impact Explanation
This is an open redirect / SSRF-style bypass in the core redirect-sanitization primitive relied upon elsewhere in `shopify-app-remix` (e.g. `render-app-bridge.ts` uses `sanitizeRedirectUrl` for post-auth/app-bridge redirects) [2](#0-1) . An attacker who can influence the redirect target parameter of a request handled by this library (e.g. a crafted link sent to a merchant, or a query parameter in the OAuth/exit-iframe flow) can cause the app to redirect the victim's browser to an attacker-controlled origin instead of staying on the app's own domain, matching Shopify's "open redirect with real consequence" bounty class (e.g. phishing following an OAuth-adjacent flow, or leaking sensitive query parameters/tokens carried in the redirect).

### Likelihood Explanation
No privileged access is required — any unprivileged actor who can get a string containing a backslash into the `redirectUrl` argument passed to `sanitizeRedirectUrl` (via whatever request parameter the host app forwards into this function) can trigger the bypass. The exploit only depends on default library behavior (`isSafe`'s regex set) and standard `URL` parsing semantics; it needs no secret, no MITM, and is fully reproducible with a unit test.

### Recommendation
After parsing the URL, explicitly verify `url.hostname` (and ideally `url.protocol` + `url.port`) matches the expected app domain's hostname for any redirect that is supposed to stay same-origin, rather than relying solely on regex pattern matching of the raw string/pathname. Additionally, extend `FILE_URI_MATCH`/`INVALID_RELATIVE_URL` to treat `\` and `/` interchangeably (or reject any backslash in the raw input outright), consistent with how the WHATWG URL parser normalizes them for special schemes.

### Proof of Concept
```ts
import {sanitizeRedirectUrl} from '../validate-redirect-url';

it('BUG: backslash host swap bypasses isSafe checks', () => {
  const APP_URL = 'https://myapp.com';

  const result = sanitizeRedirectUrl(APP_URL, '/\\evil.com/path');

  // Expected (safe) behavior: hostname should remain myapp.com
  // Actual (vulnerable) behavior: hostname becomes evil.com
  expect(result.hostname).toBe('evil.com'); // demonstrates the bypass
});
```
Running this against the current `isSafe`/`sanitizeRedirectUrl` implementation returns a `URL` whose `hostname` is `evil.com` instead of throwing `ShopifyError` as it does for the `///`-style file-URI test case already covered in the existing test suite [3](#0-2) .

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L11-30)
```typescript
const FILE_URI_MATCH = /\/\/\//;
const INVALID_RELATIVE_URL = /[/\\][/\\]/;
const WHITESPACE_CHARACTER = /\s/;
const VALID_PROTOCOLS = ['https:', 'http:'];

function isSafe(
  domain: string,
  redirectUrl: unknown,
  requireSSL: boolean | undefined = true,
): redirectUrl is string {
  if (typeof redirectUrl !== 'string') {
    return false;
  }

  if (
    FILE_URI_MATCH.test(redirectUrl) ||
    WHITESPACE_CHARACTER.test(redirectUrl)
  ) {
    return false;
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts (L1-1)
```typescript
import {BasicParams} from '../../../types';
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts (L12-17)
```typescript
  it('throws ShopifyError with file URLs', () => {
    // THEN
    expect(() => sanitizeRedirectUrl(APP_URL, '///path/to/a/file')).toThrow(
      ShopifyError,
    );
  });
```
