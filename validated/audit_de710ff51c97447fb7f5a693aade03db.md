### Title
Open redirect via backslash/mixed-slash trick bypassing `sanitizeRedirectUrl`'s `FILE_URI_MATCH`/`INVALID_RELATIVE_URL` checks - ([File: packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-redirect-url.ts])

### Finding Description
`isSafe`/`sanitizeRedirectUrl` never validates that the resolved URL's origin matches the trusted `domain` (the app URL). It only rejects (a) literal triple forward-slashes in the raw string via `FILE_URI_MATCH` [1](#0-0) , (b) whitespace, and (c) two consecutive `/` or `\` characters in `url.pathname` *after* WHATWG `URL` parsing has already happened [2](#0-1) . There is no check comparing `url.host`/`url.origin` against `domain`'s host/origin anywhere in the function — the code implicitly assumes that any string resolved with `new URL(redirectUrl, domain)` that doesn't match those raw-string/pathname regexes must remain scoped to `domain`.

The WHATWG URL spec (implemented by Node's/browsers' `URL` parser) treats backslashes as path separators for "special" schemes like `http`/`https`. This means a redirect string beginning with `/\` or `\/` is parsed as a scheme-relative authority marker, causing `new URL('/\\evil.com', 'https://app.example.com')` to resolve with `host = evil.com` rather than staying on `app.example.com`. Because the host is consumed during parsing, the resulting `url.pathname` no longer contains a literal `//` or `\\` sequence, so `INVALID_RELATIVE_URL` (checked only against `url.pathname`) does not fire, and `FILE_URI_MATCH` (checked against the raw string, which contains only one visible slash plus one backslash, not `///`) also does not fire. The function returns/uses the URL as "safe" even though its origin is now `evil.com`, not the app's origin.

This is reachable via `renderAppBridge`, which passes an attacker-influenced `redirectTo.url` directly into `sanitizeRedirectUrl(config.appUrl, redirectTo.url)` and then injects the resulting `destination.toString()` into a `window.open(...)` script embedded in the exit-iframe HTML response [3](#0-2) .

### Impact Explanation
If the parsed `URL`'s origin is not re-validated against the intended `domain`, `sanitizeRedirectUrl` can return a URL pointing at an attacker-controlled host while still being treated as "safe". Combined with the exit-iframe/App Bridge redirect flow, this could produce an open redirect out of the merchant admin's iframe context toward an attacker's domain, which matches Shopify's open-redirect impact class (session/embedded-context leakage risk).

### Likelihood Explanation
I could not fully verify the exploitability of this specific path within the current investigation because:
1. I was unable to inspect the exact call sites that pass raw, attacker-controlled query parameters (e.g. an `exitIframe` query param) into `sanitizeRedirectUrl` for the React Router package's `authenticate.ts`/`redirect-to-shopify-or-app-root.ts` — I found references to `exitIframe` in `authenticate.ts` but did not get to read that file's contents to confirm the parameter flows unmodified from the request into `sanitizeRedirectUrl`.
2. I could not run the actual `new URL('/\\evil.com', 'https://app.example.com')` parse in this environment to empirically confirm Node's exact host-resolution behavior for this payload (behavior for backslash-leading relative references can vary slightly between parser versions/edge cases), so the described bypass is based on WHATWG URL spec semantics rather than a confirmed runtime trace in this repo.
3. Existing unit tests only cover the literal `///path` case and `//path` (double-forward-slash) case, not backslash/percent-encoded variants, which is consistent with the regexes being raw-string/pathname based, but this does not conclusively prove the payload reaches this function unaltered from an HTTP-level attacker in the react-router package.

Given the incomplete verification of reachability from an actual unprivileged HTTP request path (query param plumbing into `sanitizeRedirectUrl` for the exit-iframe route), and inability to execute the PoC in this environment, I cannot confirm this to the standard of "exact file/function support and a reproducible PoC" required.

### Recommendation
Regardless of the above uncertainty, the fix is straightforward and worth doing defensively: after `const url = new URL(redirectUrl, domain)`, explicitly verify `url.origin === new URL(domain).origin` (or that `url.host === new URL(domain).host` and `url.protocol` matches) before treating the URL as safe, rather than relying solely on regex checks against the raw string/pathname.

### Proof of Concept
Not able to produce a verified, reproducible PoC in this session — recommend a Devin session with terminal access to run:
```js
console.log(new URL('/\\evil.com', 'https://app.example.com').origin);
```
and to trace `exitIframe`/redirect query-param handling in `packages/apps/shopify-app-react-router/src/server/authenticate/admin/authenticate.ts` to confirm end-to-end reachability before treating this as confirmed.

### Citations

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L11-12)
```typescript
const FILE_URI_MATCH = /\/\/\//;
const INVALID_RELATIVE_URL = /[/\\][/\\]/;
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-redirect-url.ts (L34-42)
```typescript
  try {
    url = new URL(redirectUrl, domain);
  } catch (_error) {
    return false;
  }

  if (INVALID_RELATIVE_URL.test(url.pathname)) {
    return false;
  }
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/render-app-bridge.ts (L14-28)
```typescript
export function renderAppBridge(
  {api, config}: BasicParams,
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
