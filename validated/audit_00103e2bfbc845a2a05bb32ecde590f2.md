### Title
Open redirect via `sanitizeRedirectUrl`/`isSafe` accepting arbitrary cross-origin absolute URLs - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts)

### Summary
The `isSafe` function used by `sanitizeRedirectUrl` never checks that the resolved URL's host matches the app's own domain/host. It only blocks `file:///`-style triple slashes, whitespace, double-slash relative paths, disallowed protocols, and (optionally) non-HTTPS. A fully-qualified, same-protocol absolute URL pointing at an attacker-controlled host (e.g. `https://attacker.example/phish`) passes every check and is returned as "safe."

### Finding Description
`isSafe` at `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts:16-53` performs these checks in order: reject non-strings, reject `FILE_URI_MATCH` (`///`) and whitespace, parse `new URL(redirectUrl, domain)`, reject `INVALID_RELATIVE_URL` (`//` or `\\` in `pathname`), reject protocols outside `['https:', 'http:']`, and optionally enforce HTTPS. At no point does it compare `url.host`/`url.origin` against `new URL(domain).host`/`origin`. Because `new URL()` accepts absolute URLs as its first argument regardless of the `base` passed as the second argument, calling `sanitizeRedirectUrl('https://my-app.example', 'https://attacker.example/phish')` resolves to `https://attacker.example/phish`, which has a valid `https:` protocol, no `///`/whitespace, and a clean pathname — so `isSafe` returns `true` and `sanitizeRedirectUrl` returns that attacker-controlled URL unchanged.

This function is consumed by `renderAppBridge` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts:21`), which passes `redirectTo.url` straight into `sanitizeRedirectUrl(config.appUrl, redirectTo.url)` and then injects the resulting URL into a `window.open(...)` script served to the browser. If any code path in a host app forwards request-derived data (e.g., a `redirectTo`/`return_to` query parameter) into `redirectTo.url`, an attacker fully controls the destination, and this library's sanitizer provides no protection against it, contradicting its apparent purpose (restricting redirects to the app's own domain).

The existing regression tests (`validate-redirect-url.test.ts`) only cover relative paths, `file:///`, whitespace, invalid protocol, and SSL requirement — there is no test asserting that an absolute cross-origin URL is rejected, which is consistent with this gap not being caught.

### Impact Explanation
This is an open-redirect primitive in the authenticated app-bridge redirect flow. If a host application (or a future library helper) passes user/request-controlled data as `redirectTo.url`, an attacker can redirect an authenticated merchant/session to an arbitrary external origin, enabling phishing, token/session theft via a look-alike page, or use as an SSRF-adjacent primitive in flows that fetch/follow the "safe" URL. Impact class: Open Redirect (Shopify bounty: "Open Redirect" / lower-severity redirect issues, potentially escalating if chained with OAuth/session flows).

### Likelihood Explanation
The vulnerability is reachable purely through library logic — no privileged access or secret needed. However, exploitability depends on a host app path that forwards attacker-supplied data into `redirectTo.url` passed to `renderAppBridge`; within the audited repo, direct callers found (`redirect-to-*` helpers, billing helpers) appear to use developer-configured URLs rather than raw request input, so a concrete zero-config exploitable HTTP path within this library alone was not conclusively identified. The core library-level flaw (missing host-allowlist check in `isSafe`) is confirmed and directly demonstrable via unit test.

### Recommendation
In `isSafe`, after parsing `url`, explicitly verify that `url.host` (or `origin`) equals `new URL(domain).host`/`origin` before returning `true`, rejecting any absolute URL whose host differs from the app's domain — restoring the intended `DESTINATION_ALLOWLIST` invariant.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts
import {sanitizeRedirectUrl} from '../validate-redirect-url';

it('rejects absolute cross-origin URLs (open redirect)', () => {
  expect(() =>
    sanitizeRedirectUrl('https://my-app.example', 'https://attacker.example/phish'),
  ).toThrow(); // FAILS currently: returns new URL('https://attacker.example/phish')
});
```
Running this against the current implementation shows `sanitizeRedirectUrl` does not throw and instead returns a `URL` object pointing at `attacker.example`, confirming the missing host-allowlist check. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts (L1-84)
```typescript
import {ShopifyError} from '@shopify/shopify-api';

import {APP_URL} from '../../../../__test-helpers';
import {sanitizeRedirectUrl} from '../validate-redirect-url';

describe('sanitizeRedirectUrlFactory', () => {
  it('throws ShopifyError with non-string types', () => {
    // THEN
    expect(() => sanitizeRedirectUrl(APP_URL, 123)).toThrow(ShopifyError);
  });

  it('throws ShopifyError with file URLs', () => {
    // THEN
    expect(() => sanitizeRedirectUrl(APP_URL, '///path/to/a/file')).toThrow(
      ShopifyError,
    );
  });

  it('throws ShopifyError if URL contains whitespaces', () => {
    // THEN
    expect(() =>
      sanitizeRedirectUrl(APP_URL, '/fine/url/but/it has spaces'),
    ).toThrow(ShopifyError);
  });

  it('throws ShopifyError with invalid URLs', () => {
    // THEN
    expect(() => sanitizeRedirectUrl('not a domain', '/valid/path')).toThrow(
      ShopifyError,
    );
  });

  it('throws ShopifyError with invalid relative URLs', () => {
    // THEN
    expect(() => sanitizeRedirectUrl(APP_URL, '/valid//path')).toThrow(
      ShopifyError,
    );
  });

  it('throws ShopifyError with invalid protocol', () => {
    // THEN
    expect(() =>
      sanitizeRedirectUrl(APP_URL, 'javascript:alert("nope")'),
    ).toThrow(ShopifyError);
  });

  it('throws ShopifyError when SSL is required and an HTTP address is given', () => {
    // THEN
    expect(() =>
      sanitizeRedirectUrl(APP_URL, 'http://example.com', {requireSSL: true}),
    ).toThrow(ShopifyError);
  });

  it('returns undefined if not set to throw', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, 'http://example.com', {
        requireSSL: true,
        throwOnInvalid: false,
      }),
    ).toBeUndefined();
  });

  it('succeeds on a valid URL', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, '/my/app/path', {requireSSL: true}),
    ).toEqual(new URL(`${APP_URL}/my/app/path`));
  });

  it('succeeds on a valid URL when not throwing', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, '/my/app/path', {throwOnInvalid: false}),
    ).toEqual(new URL(`${APP_URL}/my/app/path`));
  });

  it('succeeds on a valid HTTP URL when not requiring SSL', () => {
    // THEN
    expect(
      sanitizeRedirectUrl(APP_URL, 'http://my/app/path', {requireSSL: false}),
    ).toEqual(new URL('http://my/app/path'));
  });
});
```
