### Title
Open redirect via WHATWG URL host-switching (`//` / `/\`) bypassing `sanitizeRedirectUrl` host validation - (File: packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts)

### Summary
`isSafe`/`sanitizeRedirectUrl` never validates that the parsed `url.hostname` matches the intended `domain`'s hostname. Because `new URL(redirectUrl, domain)` follows the WHATWG URL spec, a `redirectUrl` such as `//evil.com` or `/\evil.com` is parsed as an absolute authority (host-switching) reference rather than a relative path, letting an attacker redirect the browser to an arbitrary origin while both regex guards (`FILE_URI_MATCH`, `INVALID_RELATIVE_URL`) fail to catch it.

### Finding Description
`isSafe` (packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts) performs three checks: [1](#0-0) 
1. `FILE_URI_MATCH = /\/\/\//` — only matches a literal *triple*-slash in the raw string.
2. `WHITESPACE_CHARACTER` — irrelevant here.
3. `INVALID_RELATIVE_URL = /[/\\][/\\]/` — tested only against `url.pathname` **after** parsing, not against the raw input or the resolved hostname: [2](#0-1) 

Crucially, `isSafe` never checks `url.hostname === new URL(domain).hostname`. It only validates `url.protocol` and the SSL requirement. For a WHATWG-spec URL parse, when the base is a special scheme (`http`/`https`), a leading `/` immediately followed by another `/` or `\` (per spec: "relative slash state" → "special authority ignore slashes state") switches parsing into authority mode and replaces the base's host entirely — it is not treated as a path segment. Consequently:
- `//evil.com` resolves to `https://evil.com/` — `pathname` is `/`, so `INVALID_RELATIVE_URL` never fires, and `FILE_URI_MATCH` (triple slash) doesn't match either.
- `/\evil.com` behaves identically because for special schemes `\` is normalized to `/` during parsing, so `/` + `\` triggers the same authority-switching state as `//`.

In both cases `isSafe` returns `true` and `sanitizeRedirectUrl` returns a `URL` object pointing at `evil.com`, even though the caller intended the result to stay within `domain`. This is used in `renderAppBridge` to build a same-origin-assumed redirect: [3](#0-2) 
where `redirectTo.url` can be derived from user-controlled query parameters processed during the admin authentication/exit-iframe flow, meaning any endpoint that forwards a `redirectUrl`/`host` parameter into `sanitizeRedirectUrl` can be coerced into emitting `window.open("https://evil.com/", "_top")` inside an authenticated admin session context.

### Impact Explanation
This is an open redirect from a trusted, embedded-admin execution context. An attacker can craft a link/query parameter that causes the app to issue a same-window navigation (`window.open(..., "_top")`) to attacker-controlled infrastructure, which can be leveraged for phishing (fake Shopify/App login pages), OAuth/token relay tricks, or chaining with other flows that trust the app's redirect target. This matches Shopify's open-redirect impact class.

### Likelihood Explanation
The only precondition is that some code path passes an unprivileged, attacker-influenced string into `sanitizeRedirectUrl(domain, redirectUrl)` — which is exactly what `renderAppBridge` does with `redirectTo.url`. No secrets, privileged roles, or non-default configuration are required; a single crafted link/query string is sufficient, and the bypass is 100% reproducible since it relies purely on standard, spec-compliant `URL` parsing behavior rather than any implementation quirk.

### Recommendation
After constructing `url = new URL(redirectUrl, domain)`, explicitly verify `url.hostname === new URL(domain).hostname` (and ideally `url.origin === new URL(domain).origin`) before considering the URL safe, rather than relying on regexes over the raw string or `url.pathname`. This closes both the `//` and backslash-normalization bypasses regardless of how WHATWG parsing interprets the input.

### Proof of Concept
```ts
import {sanitizeRedirectUrl} from '../validate-redirect-url';

const APP_URL = 'https://my-app.example.com';

describe('open redirect via host-switching bypass', () => {
  it.each([
    '//evil.com',
    '/\\evil.com',
    '\\/evil.com',
    '\\\\evil.com',
  ])('does NOT allow host-switching payload %s', (payload) => {
    const result = sanitizeRedirectUrl(APP_URL, payload, {throwOnInvalid: false});
    if (result) {
      expect(result.hostname).toBe(new URL(APP_URL).hostname);
    }
  });
});
```
Running this against the current implementation fails: `sanitizeRedirectUrl(APP_URL, '//evil.com')` and `sanitizeRedirectUrl(APP_URL, '/\\evil.com')` both return a `URL` whose `hostname` is `evil.com`, not `my-app.example.com`, demonstrating the bypass.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L11-14)
```typescript
const FILE_URI_MATCH = /\/\/\//;
const INVALID_RELATIVE_URL = /[/\\][/\\]/;
const WHITESPACE_CHARACTER = /\s/;
const VALID_PROTOCOLS = ['https:', 'http:'];
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L32-46)
```typescript
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/render-app-bridge.ts (L18-28)
```typescript
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
