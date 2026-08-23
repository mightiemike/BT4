### Title
Open redirect via protocol-relative URL bypass in `isSafe`/`sanitizeRedirectUrl` - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts)

### Summary
`sanitizeRedirectUrl`/`isSafe` in `validate-redirect-url.ts` is intended to restrict app redirects (e.g. in `renderAppBridge`) to the app's own domain, but it fails to reject protocol-relative redirect targets like `//evil.com`. Because `new URL("//evil.com", appDomain)` resolves the authority from the input rather than the base, the resulting URL points to an attacker-controlled host while still passing all of the function's checks.

### Finding Description
`isSafe` performs the following checks on an untrusted `redirectUrl` string, using `domain` (the app's own URL, e.g. `config.appUrl`) as the base for `new URL()`: [1](#0-0) 

- `FILE_URI_MATCH = /\/\/\//` only rejects strings containing **three** consecutive slashes (file URIs like `///path`), not the standard protocol-relative form with **two** slashes.
- `WHITESPACE_CHARACTER` rejects whitespace, which `//evil.com` doesn't contain.
- `new URL('//evil.com', 'https://myapp.com')` is valid per the WHATWG URL spec: a network-path reference (`//host`) replaces the **authority** of the base URL while keeping its **scheme**. The result is `https://evil.com/`, i.e., the host changes to `evil.com` even though `domain` was `https://myapp.com`.
- `INVALID_RELATIVE_URL` checks `url.pathname` (which is just `/` here), not the original string, so it doesn't catch this case.
- `VALID_PROTOCOLS.includes(url.protocol)` passes because the protocol is inherited as `https:`.
- The `requireSSL` check passes because the protocol is `https:`.

As a result, `isSafe('https://myapp.com', '//evil.com')` returns `true`, and `sanitizeRedirectUrl` returns a `URL` object for `https://evil.com/` — a host completely different from the app's own domain that this function is supposed to enforce.

This is reachable via `renderAppBridge`, which calls `sanitizeRedirectUrl(config.appUrl, redirectTo.url)` and then embeds the resulting URL into a `window.open(...)` script served to the merchant's browser: [2](#0-1) 
If `redirectTo.url` is derived from an attacker-influenced query parameter (a common pattern for post-auth "return to" redirects), an attacker can smuggle `//evil.com` through it and cause `renderAppBridge` to redirect/open the merchant's browser to an external attacker-controlled site while under the app's session — a classic open redirect, applicable to the "Websites and Apps" impact class (open redirect leading to token theft / phishing).

Note: the target named in the question, `sanitizeHost` in `packages/apps/shopify-api/lib/utils/shop-validator.ts`, is not vulnerable to this — its input must first pass a strict base64 regex (`^[0-9a-zA-Z+/]+={0,2}$`) that rejects the `.` character, so `//evil.com` cannot pass that filter to reach the vulnerable code path there. The actual vulnerable logic is in the separate `sanitizeRedirectUrl`/`isSafe` implementation shown above.

### Impact Explanation
An attacker who controls the `redirectUrl`/`redirectTo.url` value passed into `sanitizeRedirectUrl` can force the resulting "sanitized" URL to point to an arbitrary external host while the protocol/SSL checks still pass, defeating the entire purpose of the function (restricting redirects to the app's own domain). This maps to Shopify's open-redirect impact class, which can be leveraged for phishing or token-theft chains in embedded app flows.

### Likelihood Explanation
No privileged access, secrets, or non-default configuration is required — only the ability to influence the string passed as `redirectUrl`/`redirectTo.url` to `sanitizeRedirectUrl` (a common pattern for return-to/redirect query parameters in OAuth or app-bridge flows). The bypass string `//evil.com` is trivial to construct and repeatable in every call.

### Recommendation
Reject protocol-relative inputs explicitly, e.g., by checking `redirectUrl.startsWith('//')` before parsing, or by comparing `url.hostname`/`url.host` (or origin) of the resolved URL against the hostname parsed from `domain`, rejecting any mismatch instead of only checking `protocol`.

### Proof of Concept
```ts
import {sanitizeRedirectUrl} from './validate-redirect-url';

it('incorrectly accepts a protocol-relative redirect to a foreign host', () => {
  const result = sanitizeRedirectUrl('https://myapp.com', '//evil.com');
  // BUG: expected this to throw ShopifyError (foreign host), but it succeeds:
  expect(result.hostname).toBe('evil.com'); // demonstrates open redirect
});
```
Running this against the current implementation shows `sanitizeRedirectUrl` returns `https://evil.com/` instead of throwing, confirming the bypass.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L11-53)
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

  let url: URL;

  try {
    url = new URL(redirectUrl, domain);
  } catch (_error) {
    return false;
  }

  if (INVALID_RELATIVE_URL.test(url.pathname)) {
    return false;
  }

  if (!VALID_PROTOCOLS.includes(url.protocol)) {
    return false;
  }

  if (requireSSL && url.protocol !== 'https:') {
    return false;
  }

  return true;
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts (L14-28)
```typescript
export function renderAppBridge(
  {config}: BasicParams,
  request: Request,
  redirectTo?: RedirectToOptions,
): never {
  let redirectToScript = '';
  if (redirectTo) {
    const destination = sanitizeRedirectUrl(config.appUrl, redirectTo.url);

    const target = redirectTo.target ?? '_top';

    redirectToScript = `<script>window.open(${JSON.stringify(
      destination.toString(),
    )}, ${JSON.stringify(target)})</script>`;
  }
```
