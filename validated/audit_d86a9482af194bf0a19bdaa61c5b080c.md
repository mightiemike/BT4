### Title
CRLF-smuggling mismatch between `sanitizeHost` validation and `decodeHost`/`buildEmbeddedAppUrl` output enables header-value corruption on embedded-app redirects - (File: `packages/apps/shopify-api/lib/auth/decode-host.ts`, `packages/apps/shopify-api/lib/utils/shop-validator.ts`, `packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts`)

### Summary
`decodeHost` is a raw, unsanitizing `atob()` wrapper [1](#0-0) . `sanitizeHost` validates the *decoded* host only after passing it through `new URL(...)`, which per the WHATWG URL spec silently strips embedded CR/LF bytes anywhere in the string before parsing the hostname [2](#0-1) . `buildEmbeddedAppUrl`, however, re-decodes the same `host` with the raw `decodeHost` and interpolates the un-stripped result directly into a URL string used for a redirect [3](#0-2) , so a base64 `host` value can be crafted whose decoded form validates as a legitimate `*.myshopify.com` hostname (after CR/LF is stripped for validation) while the raw string that is actually used still contains the literal `\r\n` bytes.

### Finding Description
- `sanitizeHost` accepts `host` if it is base64 and, once decoded and fed to `new URL('https://' + decodeHost(host))`, the resulting `hostname` ends with an allowed domain [4](#0-3) .
- The `new URL()` constructor implements the WHATWG URL "basic URL parser" preprocessing step that removes all ASCII tab/CR/LF characters from the input *anywhere*, not just at the edges, before parsing. This means a string such as `abc.myshopify.c\r\nom` is treated identically to `abc.myshopify.com` for validation purposes, so `sanitizeHost` returns the original (truthy) base64 value — the check passes.
- `buildEmbeddedAppUrl` re-validates with `sanitizeHost(config)(host, true)` (same check, same bypass) and then calls `decodeHost(host)` a second time — but this call is a bare `atob()`, not passed through `new URL()`, so the literal `\r\n` bytes are preserved in `decodedHost` [3](#0-2) .
- The resulting string `https://${decodedHost}/apps/${config.apiKey}` (containing raw CR/LF) is returned by `getEmbeddedAppUrl` and consumed by `redirectToShopifyOrAppRoot` in both the Remix and React Router adapters, which pass it straight into `redirect(redirectUrl, ...)`, i.e., a `Location` header value [5](#0-4) [6](#0-5) .
- Neither `getEmbeddedAppUrl`, `buildEmbeddedAppUrl`, nor `redirectToShopifyOrAppRoot` re-validate or catch exceptions when constructing the `Response`/`Headers` with this value.
- Attacker input: the `host` query parameter, fully attacker-controlled, base64-encoded, reachable pre-authorization on any request that triggers the embedded redirect flow (`authenticate.admin` / `redirect-to-shopify-or-app-root` when `config.distribution !== AppDistribution.ShopifyAdmin`).
- Why existing checks fail: `sanitizeHost` validates a *URL-normalized* view of the decoded host (which strips CR/LF), while `decodeHost`/`buildEmbeddedAppUrl` use the *raw, un-normalized* decoded string for the actually-emitted URL. This "validate one representation, use another" pattern is the root cause.

### Impact Explanation
The practical consequence depends on the HTTP/Headers implementation of the host runtime:
- Fetch-spec-compliant `Headers` implementations (Node's `undici`, which backs `fetch`/`Response`/`Headers` in Remix and React Router server runtimes) reject header values containing raw `0x0D`/`0x0A` by throwing a `TypeError` when the `Location` header is set. In that case the result is an unhandled exception thrown from inside the `authenticate.admin`/redirect code path — a crash/DoS in an authentication-adjacent handler that can be triggered by any unauthenticated client with just a crafted `host` parameter.
- If a non-conforming runtime or an intermediate reverse proxy performs less strict header serialization, the embedded CR/LF could result in true HTTP response splitting on the redirect response (additional injected headers/response), which would match the "open redirect/host confusion" impact class referenced in the question.

Either way, this is a genuine defect: `sanitizeHost`'s security check is bypassable for the specific string that is actually used downstream, which breaks the intended "sanitize once, reuse safely" invariant for `host`.

### Likelihood Explanation
- Requires only an unprivileged attacker to send a request with a crafted base64 `host` param to any embedded-app route that resolves through `getEmbeddedAppUrl`/`buildEmbeddedAppUrl` (e.g., Remix/React Router `redirectToShopifyOrAppRoot`, or Express `redirect-to-shopify-or-app-root` middleware which also depends on the same `shopify-api` utilities).
- No secrets, no valid session, no non-default config needed — default `config.domainTransformations` is not required to exploit the base case (`*.myshopify.com`).
- Fully reproducible with a Jest unit test on `sanitizeHost` + `decodeHost`/`buildEmbeddedAppUrl` alone, no network access needed.
- The full end-to-end HTTP impact (actual header splitting vs. thrown exception) is runtime-dependent and was not independently verified against a live undici/Node HTTP stack in this analysis — this should be validated with an integration-level PoC against the actual adapter package in use.

### Recommendation
Make `sanitizeHost` and `decodeHost`/`buildEmbeddedAppUrl` operate on the same canonical value:
1. In `shop-validator.ts`, reject any decoded host that contains control characters (`\t`, `\r`, `\n`, or any code point < 0x20) *before* passing it to `new URL()`, instead of relying on `new URL()`'s silent stripping behavior.
2. In `get-embedded-app-url.ts`, have `buildEmbeddedAppUrl` build the final URL from the `hostname` (and other components) returned by the `URL` object used during validation, rather than re-decoding the raw base64 with a second, unsanitized `decodeHost` call — or have `decodeHost` itself reject/strip control characters.
3. Add explicit CR/LF and control-character checks as a shared invariant test in `shop-validator.test.ts` and `get-embedded-app-url.test.ts`.

### Proof of Concept
```javascript
// packages/apps/shopify-api/lib/utils/__tests__/host-crlf.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';

test('sanitizeHost accepts a host whose raw decode contains CR/LF', () => {
  const shopify = shopifyApi(testConfig());

  // Decodes (raw atob) to: "abc.myshopify.c\r\nom"
  // new URL() strips \r\n internally -> hostname becomes "abc.myshopify.com" -> passes validation
  const maliciousDecoded = 'abc.myshopify.c\r\nom';
  const base64Host = Buffer.from(maliciousDecoded).toString('base64');

  const sanitized = shopify.utils.sanitizeHost(base64Host);
  expect(sanitized).toBe(base64Host); // validation PASSES despite embedded CRLF

  // But buildEmbeddedAppUrl uses the raw decode directly:
  const rawDecoded = shopify.utils // decodeHost is internal; emulate via atob
    ? Buffer.from(base64Host, 'base64').toString('utf-8')
    : '';
  expect(rawDecoded).toContain('\r\n'); // raw CRLF survives, unlike the validated hostname

  // Constructing a Response with this value as Location throws in
  // fetch-spec-compliant runtimes (undici/Node fetch), confirming the mismatch
  // is actually reachable at the header-construction boundary:
  expect(() => {
    // eslint-disable-next-line no-new
    new Headers({Location: `https://${rawDecoded}/apps/fake-api-key`});
  }).toThrow();
});
```
This demonstrates that `sanitizeHost` (backed by `decodeHost` + `new URL()`) validates a *different string* than the one `buildEmbeddedAppUrl`/`decodeHost` actually emits, and that the emitted string contains raw CR/LF bytes that break header construction downstream.

### Citations

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-83)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);

      const originsRegex = [
        'myshopify\\.com',
        'shopify\\.com',
        'myshopify\\.io',
        'spin\\.dev',
        'shop\\.dev',
      ];

      if (config.domainTransformations) {
        const hostTransformationDomains = config.domainTransformations
          .filter((t) => t.includeHost !== false)
          .flatMap((t) =>
            getTransformationDomains({
              ...config,
              domainTransformations: [t],
            }),
          );
        originsRegex.push(...hostTransformationDomains);
      }

      const hostRegex = new RegExp(`\\.(${originsRegex.join('|')})$`);
      if (!hostRegex.test(hostname)) {
        sanitizedHost = null;
      }
```

**File:** packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts (L44-52)
```typescript
export function buildEmbeddedAppUrl(
  config: ConfigInterface,
): BuildEmbeddedAppUrl {
  return (host: string): string => {
    sanitizeHost(config)(host, true);
    const decodedHost = decodeHost(host);

    return `https://${decodedHost}/apps/${config.apiKey}`;
  };
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L13-20)
```typescript
  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
  const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!)!;

  const redirectUrl = api.config.isEmbeddedApp
    ? await api.auth.getEmbeddedAppUrl({rawRequest: request})
    : `/?shop=${shop}&host=${encodeURIComponent(host)}`;

  throw redirect(redirectUrl, {headers: responseHeaders});
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/redirect-to-shopify-or-app-root.ts (L14-24)
```typescript
  const host = api.utils.sanitizeHost(url.searchParams.get('host')!)!;
  const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!)!;

  let redirectUrl;
  if (config.distribution === AppDistribution.ShopifyAdmin) {
    redirectUrl = `/?shop=${shop}&host=${encodeURIComponent(host)}`;
  } else {
    redirectUrl = await api.auth.getEmbeddedAppUrl({rawRequest: request});
  }

  throw redirect(redirectUrl, {headers: responseHeaders});
```
