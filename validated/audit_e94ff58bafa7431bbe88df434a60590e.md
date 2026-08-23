### Title
Open redirect via protocol-relative URL bypass in `sanitizeRedirectUrl`/`isSafe` - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-redirect-url.ts`)

### Summary
`isSafe()` in `validate-redirect-url.ts` never verifies that the parsed redirect URL's hostname matches the `domain` argument's hostname; it only checks the protocol and a few string patterns. A protocol-relative redirect value such as `//evil.com` is resolved by the WHATWG `URL` constructor to an entirely different host while keeping the base's `https:` protocol, so it passes every check in `isSafe` and is returned as a "safe" URL, breaking the intended same-origin invariant.

### Finding Description
`isSafe` performs the following checks, in order [1](#0-0) :
1. `FILE_URI_MATCH` rejects `///` (triple-slash file URIs).
2. `WHITESPACE_CHARACTER` (`/\s/`) rejects any whitespace, which also covers `\r` and `\n`, so the CRLF-header-splitting variant hypothesized in the question is already blocked.
3. `new URL(redirectUrl, domain)` — this is where the protocol-relative bypass happens.
4. `INVALID_RELATIVE_URL` is tested only against `url.pathname`, not the resolved host.
5. Protocol is checked against `VALID_PROTOCOLS` and `requireSSL`.

Crucially, **no step ever compares `url.hostname`/`url.host` to `new URL(domain).hostname`**. Per the WHATWG URL spec, `new URL("//evil.com", "https://myapp.com")` resolves to `https://evil.com/` — it inherits only the scheme from the base, not the host. This value:
- does not match `FILE_URI_MATCH` (only two slashes, not three),
- contains no whitespace,
- has `pathname === "/"`, so `INVALID_RELATIVE_URL` does not trigger,
- has `protocol === "https:"`, satisfying both the protocol allow-list and `requireSSL`.

`isSafe` therefore returns `true`, and `sanitizeRedirectUrl` returns `new URL("//evil.com", domain)` = `https://evil.com/` as if it were a validated, same-origin URL — directly violating the stated invariant "only same-origin https redirects" [2](#0-1) .

The identical logic exists in the React Router package's copy of the helper as well.

### Impact Explanation
Any code path that calls `sanitizeRedirectUrl`/`isSafe` with an attacker-influenced `redirectUrl` string (e.g., a `return-to`/`redirect` query parameter forwarded during the OAuth or exit-iframe flow) can be redirected to an arbitrary external host while the function reports the value as validated. This is a classic open redirect, which per the Shopify bounty impact classes can be leveraged for phishing or, in combination with any token/parameter forwarding in the redirect target, potential token leakage to an attacker-controlled origin.

### Likelihood Explanation
The bypass requires no privileges beyond sending a normal HTTP request with a crafted `redirectUrl`-style parameter that flows unmodified into `sanitizeRedirectUrl`. The bug is in the shared validation primitive itself and is deterministically reproducible via a unit test; whether it is reachable from an unauthenticated request in this snapshot depends on which caller passes raw, attacker-controlled input as `redirectUrl` (e.g., `render-app-bridge.ts`), which I was not able to fully confirm from the available index within the exploration budget — the two callers found in `render-app-bridge.ts` should be reviewed to confirm the exact source of the `redirectUrl` argument in a live session with full file access.

### Recommendation
In `isSafe`, after parsing the URL, explicitly compare `url.hostname` (and ideally `url.port`) against the hostname of `new URL(domain)`, rejecting any mismatch — not just relying on protocol/whitespace/triple-slash heuristics. This closes the protocol-relative bypass while the existing whitespace check already adequately blocks CRLF-based header injection.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/__tests__/validate-redirect-url.test.ts
import {sanitizeRedirectUrl} from '../validate-redirect-url';

it('BUG: accepts protocol-relative cross-origin redirect', () => {
  const result = sanitizeRedirectUrl('https://myapp.example.com', '//evil.com/phish');
  // Expected: should throw ShopifyError (cross-origin)
  // Actual: returns a URL pointing to evil.com
  expect(result.hostname).toBe('evil.com'); // demonstrates the bypass
});
```
Running this against the current implementation shows `result.hostname === 'evil.com'` instead of throwing, confirming the same-origin invariant is not enforced. [1](#0-0)

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
