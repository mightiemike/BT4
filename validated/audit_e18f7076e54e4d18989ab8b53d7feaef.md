### Title
Open redirect via protocol-relative / backslash URL bypass in `isSafe` (validate-redirect-url.ts) - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts])

### Summary
`isSafe` in `validate-redirect-url.ts` never validates that the resolved URL's host matches the app's own domain; it only checks the raw string for `///` / whitespace and checks `url.pathname` for doubled separators. A protocol-relative input such as `//evil.com/path`, or a backslash-based equivalent like `/\evil.com`, is parsed by the WHATWG `URL` constructor as a network-path reference that replaces the authority (host) while keeping the base scheme, producing an external origin (`https://evil.com/...`). None of the existing regex checks inspect the resulting `url.host`/`url.hostname`, so `isSafe` returns `true` and `sanitizeRedirectUrl` returns a `URL` pointing to the attacker's domain.

### Finding Description
`isSafe` performs these checks, in order:
1. `FILE_URI_MATCH = /\/\/\//` and `WHITESPACE_CHARACTER` tested against the raw `redirectUrl` string.
2. `new URL(redirectUrl, domain)` — this is where WHATWG URL parsing/normalization happens.
3. `INVALID_RELATIVE_URL = /[/\\][/\\]/` tested against `url.pathname` (not the raw input, not the host).
4. Protocol allow-list and optional SSL requirement. [1](#0-0) 

Root cause: at no point does `isSafe` compare `url.host` (or `url.origin`) against the `domain` argument's host. It relies entirely on regexes applied to the pre-parse string and to `url.pathname`, both of which are blind to the authority component.

Exploit flow:
- Input `//evil.com/path`: raw string has only two consecutive slashes, so `FILE_URI_MATCH` (`///`) does not match, and there's no whitespace. `new URL('//evil.com/path', 'https://my-app.com')` resolves per WHATWG rules to `https://evil.com/path` because a leading `//` is a network-path reference that replaces the host but inherits the base scheme. `url.pathname` is `/path`, which contains no doubled separator, so `INVALID_RELATIVE_URL` does not match. `url.protocol` is `https:`, which is in `VALID_PROTOCOLS`, and satisfies `requireSSL`. `isSafe` returns `true`.
- Input `/\evil.com` (or `\\evil.com`): the WHATWG URL parser normalizes backslashes to forward slashes for special schemes (http/https), so this is treated identically to `//evil.com`, again resolving to host `evil.com`. The raw string contains no forward-slash sequence matching `FILE_URI_MATCH`, and `url.pathname` (`/`) doesn't trigger `INVALID_RELATIVE_URL`.

In both cases `sanitizeRedirectUrl` returns `new URL(redirectUrl, domain)` pointing to the external host, and `renderAppBridge` embeds it directly into a `window.open(...)` script served to the authenticated admin session: [2](#0-1) 

The reachable attacker-controlled path is via the app's `redirect()` helper (`redirectFactory`/`parseURL`), which forwards a caller-provided `url` string through `new URL(url, base)` and then into `renderAppBridge` when the request is embedded or a bounce request: [3](#0-2) 
Note: whether `url` here is directly attacker-controlled depends on how the host app wires a query/redirect parameter into `redirect()`; if the host app passes a request-derived value (e.g. a `redirectTo` query param) straight into `redirect()`/`renderAppBridge`, this is exploitable end-to-end. This library-level function offers no protection against that pattern regardless.

### Impact Explanation
This is an open redirect reachable from an authenticated embedded-admin context. Because `renderAppBridge` performs `window.open` in the top-level admin iframe/session context after `sanitizeRedirectUrl` "validates" the destination, a bypass allows redirecting a merchant's authenticated browsing session to an attacker-controlled origin, enabling phishing, and — depending on what parameters/tokens the host app appends to the redirect target (`redirect.ts` forwards same-origin query params only, which limits token leakage for cross-origin cases) — potential exposure of the `host`/other embedded-app context parameters if a host app appends them to the URL before validation. This matches Shopify's "open redirect from an authenticated session" impact class, though severity is somewhat mitigated by the fact that `redirect.ts` only forwards existing query params when `isSameOrigin` is true, which is false for `evil.com`. Still, the returned `URL` value handed back to callers is silently pointed at an external host despite the function's contract of restricting destinations to the app's own domain, which is a meaningful validation-logic bug.

### Likelihood Explanation
Requires a host app to pass request/query-derived data into `sanitizeRedirectUrl`/`redirect()`/`renderAppBridge`'s `url` argument — a plausible and common integration pattern (e.g., post-login redirect, deep-linking). No secrets, privileged roles, or non-default configuration are needed; a single crafted string is sufficient and the bug is deterministic and repeatable.

### Recommendation
In `isSafe`, after parsing, explicitly verify that the resolved URL's origin/host matches the trusted `domain`'s origin/host (e.g., `new URL(domain).host === url.host`), rather than relying solely on regex checks against the raw string and `pathname`. This closes both the plain `//` and backslash-normalization bypasses in one fix.

### Proof of Concept
```ts
import {sanitizeRedirectUrl} from '../validate-redirect-url';

const APP_URL = 'https://my-app.com';

it('does NOT throw for protocol-relative URL (bug)', () => {
  const result = sanitizeRedirectUrl(APP_URL, '//evil.com/path');
  expect(result.host).toBe('evil.com'); // currently true - should have thrown
});

it('does NOT throw for backslash-based protocol-relative URL (bug)', () => {
  const result = sanitizeRedirectUrl(APP_URL, '/\\evil.com');
  expect(result.host).toBe('evil.com'); // currently true - should have thrown
});
```
Both assertions currently pass against the shown implementation, confirming `sanitizeRedirectUrl` returns a `URL` pointing to `evil.com` instead of throwing `ShopifyError`, i.e. `isSafe` incorrectly returns `true` for cross-origin protocol-relative inputs. [4](#0-3)

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect.ts (L105-131)
```typescript
function parseURL({params, base, init, shop, url}: ParseURLOptions): ParsedURL {
  let target: RedirectTarget | undefined =
    typeof init !== 'number' && init?.target ? init.target : undefined;

  if (isAdminRemotePath(url)) {
    const {config} = params;

    const adminPath = getAdminRemotePath(url);
    const cleanShopName = shop.replace('.myshopify.com', '');

    if (!target) {
      target = config.isEmbeddedApp ? '_parent' : '_self';
    }

    return {
      url: new URL(
        `https://admin.shopify.com/store/${cleanShopName}${adminPath}`,
      ),
      target,
    };
  } else {
    return {
      url: new URL(url, base),
      target: target ?? '_self',
    };
  }
}
```
