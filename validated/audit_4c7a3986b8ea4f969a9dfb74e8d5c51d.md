### Title
Missing length bound on `shop`/`host` query parameters before decode/regex processing enables resource-exhaustion DoS - ([File: packages/apps/shopify-api/lib/utils/shop-validator.ts])

### Summary
The CVE-2024-56374 root cause is Django's IPv6 validators (`clean_ipv6_address`, `is_valid_ipv6_address`) and `GenericIPAddressField` accepting attacker-controlled strings of unbounded length before running validation logic on them, enabling a DoS via oversized input. The `shopify-api` package has the same structural weakness in `sanitizeShop`/`sanitizeHost`: both take the raw `shop`/`host` query parameters straight from an anonymous request and run regex matching, base64 decoding, and `URL` construction on them with **no upper-bound length check** anywhere in the call chain.

### Finding Description
`sanitizeHost` in [1](#0-0)  takes the raw `host` value and:
1. Tests it against `/^[0-9a-zA-Z+/]+={0,2}$/` (unbounded length),
2. Calls `decodeHost(sanitizedHost)`, which is a straight `atob(host)` call with no length cap: [2](#0-1) 
3. Feeds the decoded value into `new URL('https://' + decoded)`.

`sanitizeShop` similarly runs unbounded-length input through two dynamically constructed regexes (`shopUrlRegex`, `shopAdminRegex`) and `shopAdminUrlToLegacyUrl`, which chains several more `.match()` calls on the same string: [3](#0-2) 

None of these functions validate the length of `shop`/`host` before doing this work, and callers pass the value straight from the request with no length gate. This is reachable pre-authentication from multiple embedded-app entry points:
- `validateShopAndHostParams`, invoked on every document request before a session exists: [4](#0-3) 
- The login route handler, reachable by any unauthenticated visitor: [5](#0-4) 
- `redirectToAuth`/`clientSideRedirect` in `shopify-app-express`, which sanitizes both `shop` and `host` from the query string before any auth check: [6](#0-5) 

### Impact Explanation
An anonymous client can send a `host` (or `shop`) query parameter of arbitrary size (e.g. megabytes of base64 data) on every request to unauthenticated app routes (login, `/auth`, `/auth/callback`, or any embedded document-request route). Each request forces regex evaluation, `atob` decoding, and `URL` parsing over the full attacker-controlled payload, with no size cap enforced by the library itself. Repeated requests amplify CPU/memory consumption per request and can degrade service availability, mirroring the DoS class described in CVE-2024-56374 (unbounded-length input reaching validation/decoding routines). Impact is capped by whatever length limits the underlying HTTP transport/framework imposes (which vary significantly across the many runtime adapters this library supports — Node, Cloudflare Workers, Deno, etc.), so severity is host-environment dependent.

### Likelihood Explanation
Reachable by any unauthenticated actor with no shop/merchant/session context — a single crafted HTTP request suffices, and the vulnerable code paths (`sanitizeShop`, `sanitizeHost`) sit directly on the OAuth/login/document-request entry points that all shopify-app-js embedded apps expose publicly. However, exploitability depends on the deployment's request/header/URL-size limits not already bounding the parameter, which reduces likelihood on default Node.js deployments (which enforce an ~16KB header/URL limit) but not necessarily on other supported runtimes.

### Recommendation
Add explicit maximum-length checks (e.g., reject `shop`/`host` values longer than the longest legitimate value, ~100–200 characters) at the top of `sanitizeShop` and `sanitizeHost` in `packages/apps/shopify-api/lib/utils/shop-validator.ts`, before any regex/base64/URL processing, returning `null`/throwing `InvalidShopError`/`InvalidHostError` immediately for oversized input.

### Proof of Concept
```
GET /auth?shop=test.myshopify.com&host=<5MB of base64 characters> HTTP/1.1
Host: victim-app.example.com
```
Sent repeatedly and concurrently, each request forces `sanitizeHost` to run regex matching, `atob` decoding, and `URL` construction over the full 5MB payload with no early rejection, on a route reachable without any session or authentication.

### Citations

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L52-58)
```typescript
export function sanitizeHost(config: ConfigInterface) {
  return (host: string, throwOnInvalid = false): string | null => {
    const base64regex = /^[0-9a-zA-Z+/]+={0,2}$/;

    let sanitizedHost = base64regex.test(host) ? host : null;
    if (sanitizedHost) {
      const {hostname} = new URL(`https://${decodeHost(sanitizedHost)}`);
```

**File:** packages/apps/shopify-api/lib/auth/decode-host.ts (L1-3)
```typescript
export function decodeHost(host: string): string {
  return atob(host);
}
```

**File:** packages/apps/shopify-api/lib/utils/shop-admin-url-helper.ts (L1-29)
```typescript
// Converts admin.shopify.com/store/my-shop to my-shop.myshopify.com
export function shopAdminUrlToLegacyUrl(shopAdminUrl: string): string | null {
  const shopUrl = removeProtocol(shopAdminUrl);

  const isShopAdminUrl = shopUrl.split('.')[0] === 'admin';

  if (!isShopAdminUrl) {
    return null;
  }

  const regex = new RegExp(`admin\\..+/store/([^/]+)`);
  const matches = shopUrl.match(regex);

  if (matches && matches.length === 2) {
    const shopName = matches[1];
    const isSpinUrl = shopUrl.includes('spin.dev/store/');
    const isLocalUrl = shopUrl.includes('shop.dev/store/');

    if (isSpinUrl) {
      return spinAdminUrlToLegacyUrl(shopUrl);
    } else if (isLocalUrl) {
      return localAdminUrlToLegacyUrl(shopUrl);
    } else {
      return `${shopName}.myshopify.com`;
    }
  } else {
    return null;
  }
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/helpers/validate-shop-and-host-params.ts (L1-30)
```typescript
import {redirect} from '@remix-run/server-runtime';

import {BasicParams} from '../../../types';

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
    if (!host) {
      logger.debug('Invalid host, redirecting to login path', {
        shop,
        host: url.searchParams.get('host'),
      });
      throw redirectToLoginPath(request, params);
    }
  }
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/login/login.ts (L8-36)
```typescript
  return async function login(request: Request): Promise<LoginError | never> {
    const url = new URL(request.url);
    const shopParam = url.searchParams.get('shop');

    if (request.method === 'GET' && !shopParam) {
      return {};
    }

    const shop: string | null =
      shopParam || ((await request.formData()).get('shop') as string);

    if (!shop) {
      logger.debug('Missing shop parameter', {shop});
      return {shop: LoginErrorType.MissingShop};
    }

    const shopWithoutProtocol = shop
      .replace(/^https?:\/\//, '')
      .replace(/\/$/, '');
    const shopWithDomain =
      shop?.indexOf('.') === -1
        ? `${shopWithoutProtocol}.myshopify.com`
        : shopWithoutProtocol;
    const sanitizedShop = api.utils.sanitizeShop(shopWithDomain);

    if (!sanitizedShop) {
      logger.debug('Invalid shop parameter', {shop});
      return {shop: LoginErrorType.InvalidShop};
    }
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L8-48)
```typescript
export async function redirectToAuth({
  req,
  res,
  api,
  config,
  isOnline = false,
}: RedirectToAuthParams) {
  const shop = api.utils.sanitizeShop(req.query.shop as string);
  if (!shop) {
    config.logger.error('No shop provided to redirect to auth');
    res.status(500);
    res.send('No shop provided');
    return;
  }

  if (req.query.embedded === '1') {
    clientSideRedirect(api, config, req, res, shop);
  } else {
    await serverSideRedirect(api, config, req, res, shop, isOnline);
  }
}

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

  const redirectUriParams = new URLSearchParams({shop, host}).toString();
  const redirectUri = `${api.config.hostScheme}://${api.config.hostName}${config.auth.path}?${redirectUriParams}`;

  redirectOutOfApp({config, api})({req, res, redirectUri, shop});
}
```
