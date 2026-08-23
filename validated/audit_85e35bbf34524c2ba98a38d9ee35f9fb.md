### Title
Missing host/origin validation in `sanitizeRedirectUrl` allows open redirect via protocol-relative and absolute cross-origin URLs - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts)

### Summary
`sanitizeRedirectUrl`/`isSafe` never validates that the resolved URL's hostname matches the intended `domain`. It only checks for triple-slash file URIs, whitespace, doubled slashes in `pathname`, and protocol scheme. A protocol-relative value like `//evil.example.com/x` (or even a fully-qualified `https://evil.example.com/x`) passes all checks and resolves to an attacker-controlled origin while `sanitizeRedirectUrl` returns it without throwing.

### Finding Description
`isSafe` in [1](#0-0)  performs these checks in order: `FILE_URI_MATCH` (`/\/\/\//`) on the raw string, `WHITESPACE_CHARACTER`, then constructs `new URL(redirectUrl, domain)`, then tests `INVALID_RELATIVE_URL` (`/[/\\][/\\]/`) against `url.pathname` only, then checks `url.protocol` against `VALID_PROTOCOLS = ['https:', 'http:']`. At no point does the code compare `url.hostname`/`url.origin` to the `domain` argument.

For input `//evil.example.com/x`:
- `FILE_URI_MATCH` requires three consecutive slashes; the input has only two consecutive slashes at the start, so it does not match.
- No whitespace present.
- `new URL('//evil.example.com/x', domain)` resolves per the WHATWG URL spec as a protocol-relative URL, inheriting the base's scheme (e.g. `https:`) and producing `https://evil.example.com/x`.
- `url.pathname` is `/x`, which contains no doubled slashes, so `INVALID_RELATIVE_URL` does not match (this regex is applied to `pathname`, not `host`).
- `url.protocol` is `https:`, which is in `VALID_PROTOCOLS`.

All checks pass and `sanitizeRedirectUrl` returns a `URL` object whose `hostname` is `evil.example.com` instead of throwing `ShopifyError`. In fact the flaw is broader than just protocol-relative input: a fully qualified absolute URL such as `https://evil.example.com/path` passes the exact same checks, since the function has no origin allow-listing logic at all — it only restricts scheme, not host.

This function is consumed by `renderAppBridge` at [2](#0-1) , where `redirectTo.url` is sanitized via `sanitizeRedirectUrl(config.appUrl, redirectTo.url)` and then embedded into a `window.open(...)` script rendered in an authenticated response.

### Impact Explanation
This is an open-redirect / host-validation bypass: the library's stated invariant — that redirect destinations are constrained to the app's own domain — is violated. Depending on the caller supplying attacker-influenced `redirectTo.url` (e.g., via a query parameter propagated into `renderAppBridge`), this can be used to redirect a merchant's authenticated embedded-admin browser context (`window.open`) to an attacker-controlled origin, enabling phishing or token/session leakage through the browser's referrer/redirect chain. The severity depends on which call sites in host apps pass attacker-controlled values into `redirectTo.url`; within this library itself the guard that is supposed to prevent exactly this class of redirect is broken.

### Likelihood Explanation
The bypass requires no privileges: any actor who can influence the string passed as `redirectUrl`/`redirectTo.url` (e.g., an unprivileged request parameter forwarded by a host app to `renderAppBridge`) can trigger it. The technique (`//host/path` protocol-relative URL, or a plain absolute cross-origin URL) is trivial and fully reproducible, with no dependency on secrets, MITM, or non-default configuration.

### Recommendation
In `isSafe`, after constructing `url`, explicitly compare `url.hostname` (and ideally `url.protocol`+`url.host`, i.e. full origin) against the parsed `hostname`/`origin` of `domain`, and reject if they differ. Also apply `INVALID_RELATIVE_URL`/protocol-relative detection against the raw `redirectUrl` string prefix (e.g., reject strings starting with `//`) in addition to checks against `url.pathname`.

### Proof of Concept
```ts
import {sanitizeRedirectUrl} from 'packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url';

const APP_URL = 'https://myapp.example.com';

test('protocol-relative URL bypasses host validation', () => {
  const result = sanitizeRedirectUrl(APP_URL, '//evil.example.com/x');
  expect(result.hostname).toBe('evil.example.com'); // should have thrown ShopifyError instead
});

test('absolute cross-origin URL also bypasses host validation', () => {
  const result = sanitizeRedirectUrl(APP_URL, 'https://evil.example.com/path');
  expect(result.hostname).toBe('evil.example.com'); // should have thrown ShopifyError instead
});
```
Both assertions pass against current code, confirming no `ShopifyError` is thrown and the returned URL's `hostname` is attacker-controlled rather than being restricted to `APP_URL`'s domain. [1](#0-0) [2](#0-1)

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L16-53)
```typescript
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts (L14-27)
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
```
