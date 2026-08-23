### Title
Protocol-relative URL bypass in `sanitizeRedirectUrl`/`isSafe` allows open redirect to attacker-controlled origin - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts` and the identical `shopify-app-react-router` counterpart)

### Summary
`isSafe()` validates the *parsed* `url.pathname` against `INVALID_RELATIVE_URL = /[/\\][/\\]/` instead of validating the resulting origin against the trusted `domain`. Because WHATWG `new URL()` treats a leading `//` (or `/\`, `\/`, `\\` for "special" schemes like http/https) in the reference as a network-path/authority marker, a payload such as `//evil.com` or `/\evil.com` gets resolved to a completely different host before the pathname check ever runs, so the check inspects an innocuous `pathname` of `/` and passes.

### Finding Description
In `isSafe` [1](#0-0) , the sequence of checks is:
1. `FILE_URI_MATCH.test(redirectUrl)` — only catches 3+ consecutive slashes in the *raw* input.
2. `new URL(redirectUrl, domain)` — parses the value relative to the app's own origin.
3. `INVALID_RELATIVE_URL.test(url.pathname)` — checked against the *already-parsed* `pathname`, not the raw input and not the resulting `origin`/`host`.
4. Protocol allow-list (`http:`/`https:`), optionally SSL enforcement.

There is no comparison of `url.origin` (or `url.host`) to `new URL(domain).origin` anywhere in the function [2](#0-1) .

For a payload like `//evil.com`, WHATWG URL parsing treats the leading `//` as a network-path reference, so `new URL('//evil.com', 'https://myapp.com')` resolves to `https://evil.com/` — the host is fully overridden by the attacker-supplied value, while `url.pathname` becomes just `/`. Because the "double slash/backslash" check runs against `pathname` (which no longer contains the offending slashes — they were consumed to build the host), the check silently passes. The same applies to backslash-mixed variants (`/\evil.com`, `\/\/evil.com`) because for "special" schemes (http/https) WHATWG normalizes `\` to `/` during authority detection. The protocol check also passes because the inferred scheme is inherited from the base (`https:`), which is on the allow-list.

Percent-encoded variants such as `/%2F%2Fevil.com` are **not** exploitable this way, because `%2F` is not decoded during the authority-detection phase of URL parsing — it is retained as literal path characters, so the result stays on the same origin as `domain`. Only the raw `/` and `\` leading-slash forms bypass the check.

This function is exposed to library consumers via `redirectFactory`'s public `redirect()` API and via `renderAppBridge` [3](#0-2) , which embeds the sanitized destination directly into a `window.open(...)` script served to the merchant's authenticated embedded-admin browser session. If a host app forwards a request-controlled value (e.g. a `return_to` query parameter) into `redirect()`/`renderAppBridge`'s `url`, the resulting script will `window.open` an attacker-chosen origin from the context of the authenticated admin session.

### Impact Explanation
This is an open redirect (CWE-601) reachable from the app's own authenticated, embedded browser context. Combined with `window.open`, it can be used for phishing (fake re-auth/login page) targeting merchants who trust the app's domain, and — depending on how the host app derives the redirect input — could facilitate session token/OAuth phishing flows. It maps to Shopify's "open redirect with real consequence" bounty impact class rather than a critical account-takeover, since exploitation still requires the host app to plumb an untrusted parameter into `redirect()`/`renderAppBridge`.

### Likelihood Explanation
Exploitability requires: (1) the underlying redirect string reaching `sanitizeRedirectUrl` to be attacker-influenced (e.g., a `return_to`/`redirect` query parameter forwarded by the host app, as posited in the question's precondition), and (2) `throwOnInvalid` not disabled (default is to throw, so this is the default-safe path that is being bypassed). Given that, the payload construction (`//evil.com`, `/\evil.com`) requires no special privileges — any unauthenticated party who can influence the query string of a link the merchant clicks can trigger it. This is a low-complexity, deterministic bug in the library's own regex logic, not merely a host-app misuse.

### Recommendation
Replace the pathname-based heuristic with an explicit origin allow-list check: after constructing `url = new URL(redirectUrl, domain)`, reject unless `url.origin === new URL(domain).origin` (or an explicit, documented allow-list of external origins is intended and separately vetted). Do not attempt to special-case slash/backslash patterns in `pathname`; the authoritative check should be on `url.host`/`url.origin`, since by the time that field exists the WHATWG parser has already resolved any authority-override attempt.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts
import {sanitizeRedirectUrl} from '../validate-redirect-url';
import {APP_URL} from '../../../../__test-helpers';

describe('protocol-relative bypass', () => {
  it.each([
    '//evil.com',
    '/\\evil.com',
    '\\/\\/evil.com',
  ])('rejects protocol-relative payload %s', (payload) => {
    // Current behavior: this does NOT throw and returns a URL whose
    // origin is attacker-controlled, violating the DESTINATION_ALLOWLIST
    // invariant that origin must equal APP_URL's origin.
    const result = sanitizeRedirectUrl(APP_URL, payload);
    expect(result.origin).toEqual(new URL(APP_URL).origin); // FAILS today
  });
});
```
Expected (secure) behavior: `sanitizeRedirectUrl` should throw `ShopifyError` for all three payloads, matching its behavior for `///path` and other malformed inputs. Currently it returns a `URL` object pointing at `evil.com`.

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
