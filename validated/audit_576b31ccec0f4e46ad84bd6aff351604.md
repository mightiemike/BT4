### Title
Unhandled exception in `sanitizeHost` crashes auth/session-token flows on malformed `host` query parameter - (File: packages/apps/shopify-api/lib/utils/shop-validator.ts)

### Summary
This is an analog of the reported bug class: a validation function has an implicit, unchecked assumption (here, "the string is valid, decodable base64") that a caller-controlled input can violate, causing the function to throw instead of returning a rejection value. This unhandled throw propagates out of what is meant to be a safe boolean-returning sanitizer and crashes request handling for a completely anonymous, unauthenticated caller.

### Finding Description
`sanitizeHost` validates the `host` query parameter using a loose regex before decoding it: [1](#0-0) 

The regex `^[0-9a-zA-Z+/]+={0,2}$` only checks the character set and trailing `=` padding count — it does not validate that the string length is a multiple of 4 or that the padding is structurally correct base64. Strings such as `"A"`, `"AA"`, or other malformed-but-character-valid base64 fragments pass this regex.

`decodeHost` is a thin wrapper around Node's global `atob`: [2](#0-1) 

`atob` throws (`DOMException: The string to be decoded is not correctly encoded.`) for base64 strings with invalid length/padding, even though they satisfy the regex above. `sanitizeHost` calls `decodeHost` (and then `new URL(...)`) with no `try/catch`, so this exception is unhandled and propagates directly out of `sanitizeHost`.

`sanitizeHost` is invoked directly on untrusted, anonymous request query parameters in multiple auth entry points that run before any session/HMAC verification, e.g.:
- `getEmbeddedAppUrl`/`buildEmbeddedAppUrl` [3](#0-2) 
- `validateShopAndHostParams` in shopify-app-react-router, called at the start of `authenticate.admin` [4](#0-3) 
- The equivalent remix helper [5](#0-4) 
- `redirectToAuth`'s client-side redirect branch in shopify-app-express [6](#0-5) 

None of these call sites wrap the `sanitizeHost` call in a try/catch — they only handle the `null`-return "invalid" case, not a thrown exception.

The existing test suite's `INVALID_HOSTS` fixtures only include hosts that fail the *regex* check (illegal characters or the wrong shop domain), not the "passes-regex-but-invalid-base64" case, so this gap is untested and unguarded: [7](#0-6) 

### Impact Explanation
An anonymous attacker can send a request to any route that calls `sanitizeHost`/`getEmbeddedAppUrl` (e.g., the app's login path, the embedded-app redirect helper, or the OAuth begin/callback-adjacent redirect helpers) with a `host` query parameter crafted to pass the regex but be invalid base64. This throws an uncaught exception inside the request-handling pipeline. Depending on the runtime/adapter's error handling, this can manifest as an unhandled promise rejection / 500 crash of that request, and in frameworks/adapters without global error boundaries, potentially destabilize the process. This is a denial-of-service of an authentication-adjacent handler triggered by a single unauthenticated request — no privileged actor, secret leakage, or MITM is required.

### Likelihood Explanation
High likelihood of reachability: `host` is a normal, attacker-controlled query parameter on public-facing routes (login, auth begin/callback redirect helpers, embedded bounce pages) that are hit before any session or HMAC validation. Constructing a base64-alphabet string of invalid length (e.g., a single character, or removing/adding one character from a legitimately-encoded host) is trivial and requires no authentication, tokens, or shop installation.

### Recommendation
Wrap the `decodeHost`/`new URL` call inside `sanitizeHost` in a `try/catch`, treating any decode/parse failure the same as a regex mismatch (i.e., set `sanitizedHost = null` and only throw `InvalidHostError` when `throwOnInvalid` is true and the caller expects a thrown error). Additionally, tighten the regex to require valid base64 length semantics (length multiple of 4) or verify with a round-trip encode/decode check before trusting the value, and add regression tests using malformed-but-character-valid base64 strings (e.g., `"A"`, `"AB"`, `"AAAAA"`).

### Proof of Concept
1. Send a request to any endpoint that calls `shopify.utils.sanitizeHost`/`getEmbeddedAppUrl` with `?host=A` (or any base64-alphabet string whose length breaks base64 padding rules, e.g. `AAAAA`).
2. `base64regex.test('A')` returns `true` (single alphanumeric character, no `=` required).
3. `decodeHost('A')` calls `atob('A')`, which throws `DOMException: The string to be decoded is not correctly encoded.`
4. This exception is not caught anywhere in `sanitizeHost`, `getEmbeddedAppUrl`, or the `validateShopAndHostParams`/`redirectToAuth` call sites, causing an unhandled exception in the request-handling flow before any authentication has occurred.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-60)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);

      const originsRegex = [
```

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-api/lib/auth/get-embedded-app-url.ts (L44-53)
```typescript
export function buildEmbeddedAppUrl(
  config: ConfigInterface,
): BuildEmbeddedAppUrl {
  return (host: string): string => {
    sanitizeHost(config)(host, true);
    const decodedHost = decodeHost(host);

    return `https://${decodedHost}/apps/${config.apiKey}`;
  };
}
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L1-21)
```typescript
import {BasicParams, AppDistribution} from '../../../types';

import {renderAppBridge} from './render-app-bridge';

export function validateShopAndHostParams(
  params: BasicParams,
  request: Request,
) {
  const {api, config, logger} = params;

  if (config.distribution !== AppDistribution.ShopifyAdmin) {
    const url = new URL(request.url);
    const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!);
    if (!shop) {
      logger.debug('Missing or invalid shop, rendering App Bridge', {
        shop,
      });
      throw renderAppBridgeOrError(request, params);
    }

    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L5-21)
```typescript
export function validateShopAndHostParams(
  params: BasicParams,
  request: Request,
) {
  const {api, config, logger} = params;

  if (config.isEmbeddedApp) {
    const url = new URL(request.url);
    const shop = api.utils.sanitizeShop(url.searchParams.get('shop')!);
    if (!shop) {
      logger.debug('Missing or invalid shop, redirecting to login path', {
        shop,
      });
      throw redirectToLoginPath(request, params);
    }

    const host = api.utils.sanitizeHost(url.searchParams.get('host')!);
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L30-42)
```typescript
function clientSideRedirect(
  api: Shopify,
  config: AppConfigInterface,
  req: Request,
  res: Response,
  shop: string,
): void {
  const host = api.utils.sanitizeHost(req.query.host as string);
  if (!host) {
    res.status(500);
    res.send('No host provided');
    return;
  }
```

**File:** packages/apps/shopify-api/lib/utils/__tests__/shop-validator.test.ts (L33-50)
```typescript
const INVALID_HOSTS = [
  {
    testhost: 'plain-string-is-not-base64',
    base64host: 'plain-string-is-not-base64',
  },
  {
    testhost: "valid host but ending with '-nope'",
    base64host: `${Buffer.from('my-other-host.myshopify.com/admin').toString(
      'base64',
    )}-nope`,
  },
  {
    testhost: 'my-fake-host.notshopify.com/admin',
    base64host: Buffer.from('my-fake-host.notshopify.com/admin').toString(
      'base64',
    ),
  },
];
```
