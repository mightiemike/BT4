### Title
Backslash-based host swap bypasses `sanitizeRedirectUrl`'s allowlist checks, enabling open redirect - ([File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts])

### Summary
`isSafe` validates the raw `redirectUrl` string with `FILE_URI_MATCH` (`/\/\/\//`) and `WHITESPACE_CHARACTER` (`/\s/`), and validates `INVALID_RELATIVE_URL` (`/[/\\][/\\]/`) only against the *parsed* `url.pathname`, never against the raw input for backslash sequences. Because the WHATWG URL parser treats `\` as `/` for special schemes (http/https) when resolving a relative reference against a base, an input like `/\evil.com/path` is parsed by `new URL(redirectUrl, domain)` as an absolute reference that swaps out the base host entirely, producing `https://evil.com/path`. None of the three checks catch this: the raw string has no `///` and no whitespace, and the resulting pathname (`/path`) contains no double-slash/backslash pair to trip `INVALID_RELATIVE_URL`.

### Finding Description
`isSafe` (packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts:16-53) is intended to ensure a caller-supplied `redirectUrl` resolves to the app's own `domain`. It runs two regex checks on the raw string (`FILE_URI_MATCH`, `WHITESPACE_CHARACTER`), then constructs `new URL(redirectUrl, domain)` and runs `INVALID_RELATIVE_URL` against `url.pathname` (not the raw input, and not `url.host`).

For a special scheme base like `https://app.example.com`, the WHATWG URL parsing algorithm treats `\` identically to `/` while walking the "relative slash state" / "special authority ignore slashes state". Given input `/\evil.com/path`:
- Raw string has one `/` then one `\` — `FILE_URI_MATCH` (`///`) does not match, `WHITESPACE_CHARACTER` does not match.
- `new URL('/\\evil.com/path', 'https://app.example.com')` parses the leading `/` then the `\` as an authority-starting marker, causing the parser to treat `evil.com` as a **new host**, discarding the base's host. The scheme is inherited from the base (`https:`), so the result is `https://evil.com/path`.
- `url.pathname` for this result is `/path` — no doubled slash/backslash, so `INVALID_RELATIVE_URL` does not match either.
- `VALID_PROTOCOLS` and `requireSSL` checks pass because the scheme is still `https:`.

`isSafe` therefore returns `true`, and `sanitizeRedirectUrl` (line 55-67) returns `new URL('/\\evil.com/path', domain)`, i.e., a `URL` object pointing at `evil.com`, not the app's domain — despite the function's entire purpose being to keep redirects scoped to `domain`.

This function is used directly by `renderAppBridge` (packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts:21-27), which embeds the resulting `destination.toString()` into a `<script>window.open(...)</script>` payload served to the merchant's browser inside the embedded admin app iframe context. An attacker who can influence the `redirectTo.url` value reaching `renderAppBridge` (e.g., via a query parameter or app-controlled redirect target that echoes user input) can force the merchant's browser to navigate/open an attacker-controlled origin from within the trusted app-bridge redirect flow.

### Impact Explanation
This is an open-redirect/SSRF-class bypass of an allowlist intended to restrict redirects to the app's own domain (`DESTINATION_ALLOWLIST` invariant). Concretely it allows redirecting an authenticated merchant session to an attacker-controlled host via the app-bridge redirect mechanism, which can be leveraged for phishing (e.g., a fake OAuth/login page) or token/parameter exfiltration if the redirect carries sensitive query parameters. It matches Shopify's "open redirect / URL validation bypass" bounty impact class.

### Likelihood Explanation
No special privileges are required — any caller (unprivileged/anonymous or a merchant-triggered flow) that can control the string passed as `redirectTo.url` into `renderAppBridge`/`sanitizeRedirectUrl` can trigger this with a single crafted string (`/\evil.com/path`). The vulnerable code path (`isSafe`) is invoked unconditionally whenever `sanitizeRedirectUrl` is called, and the bypass depends only on default WHATWG `URL` parsing behavior in Node.js — no non-default configuration is needed. It is fully deterministic and repeatable.

### Recommendation
Perform the double-slash/backslash check against the **raw input string** (or explicitly reject any backslash character in the raw `redirectUrl`) before parsing, not against the post-parse `url.pathname`. Additionally, after constructing `url`, explicitly verify `url.host === new URL(domain).host` (or hostname equality) rather than relying solely on protocol/path heuristics — this directly enforces the intended "must stay within `domain`" invariant regardless of parser quirks. Example fix: add `if (/\\/.test(redirectUrl)) return false;` early in `isSafe`, and add a hostname-equality check against `domain`.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts
import {APP_URL} from '../../../../__test-helpers';
import {sanitizeRedirectUrl} from '../validate-redirect-url';

it('bypasses host restriction via backslash host swap', () => {
  const result = sanitizeRedirectUrl(APP_URL, '/\\evil.com/path');
  // Expected (safe) behavior: should throw ShopifyError, since host != APP_URL host
  // Actual (vulnerable) behavior:
  expect(result.hostname).toBe('evil.com'); // demonstrates bypass
  expect(result.hostname).not.toBe(new URL(APP_URL).hostname);
});
```
Running this against the current implementation shows `result.hostname === 'evil.com'`, confirming that `sanitizeRedirectUrl(APP_URL, '/\\evil.com/path')` returns a URL pointing to an attacker-controlled host instead of throwing/rejecting. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L55-67)
```typescript
export function sanitizeRedirectUrl<OptionsArg extends Options>(
  domain: string,
  redirectUrl: unknown,
  options: OptionsArg = {} as OptionsArg,
): SanitizedRedirectUrl<OptionsArg> {
  if (isSafe(domain, redirectUrl, options.requireSSL)) {
    return new URL(redirectUrl, domain) as SanitizedRedirectUrl<OptionsArg>;
  } else if (options.throwOnInvalid === false) {
    return undefined as SanitizedRedirectUrl<OptionsArg>;
  } else {
    throw new ShopifyError('Invalid URL. Refusing to redirect');
  }
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
