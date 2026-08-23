### Title
OAuth callback shop-parameter desync: HMAC validates the last duplicate `shop` value while token exchange/session creation act on the first duplicate value - ([File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts])

### Summary
In `callback()` in `packages/apps/shopify-api/lib/auth/oauth/oauth.ts`, the `shop` value used for HMAC validation (`authQuery.shop`, derived via `Object.fromEntries(query.entries())`, which keeps the **last** occurrence of a duplicated key) is different from the `shop` value used to perform the actual OAuth token exchange and session creation (`query.get('shop')`, a `URLSearchParams` lookup that returns the **first** occurrence). An attacker who duplicates the `shop` query parameter on a genuine Shopify-issued callback URL can make the HMAC check validate against the real, correctly-signed `shop` while the app actually performs the token-exchange POST and creates the session against an attacker-chosen `shop` value.

### Finding Description
`generateLocalHmac`/`validateHmac` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` are called from `validQuery()` with `authQuery`, built in `callback()` as: [1](#0-0) 

```
const query = new URL(request.url, ...).searchParams;
const shop = query.get('shop')!;
...
const authQuery: AuthQuery = Object.fromEntries(query.entries());
if (!(await validQuery({config, query: authQuery, stateFromCookie}))) { ... }
...
const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;
``` [2](#0-1) 

`Object.fromEntries()` on a `URLSearchParams.entries()` iterator sets each key sequentially, so for a repeated `shop` param the **last** value wins in `authQuery`. `URLSearchParams.get('shop')`, however, returns the **first** value. Both derive from the same raw `query`, but `validateHmac` (via `normalizeQuery`) only inspects the already-collapsed plain object `authQuery`, and — critically — for the admin `signator` path, `normalizeQuery` only rejects duplicate/array values when the input `query` is itself a `URLSearchParams` instance; when it's a plain object (as `authQuery` is here after `Object.fromEntries`) no duplicate detection happens at all: [3](#0-2) 

Exploit flow: an attacker takes a genuine, Shopify-signed callback URL of the form `?code=...&hmac=<real>&shop=VICTIM.myshopify.com&state=...&timestamp=...` and prepends a duplicate `shop=ATTACKER.myshopify.com` parameter before the genuine one: `?shop=ATTACKER.myshopify.com&shop=VICTIM.myshopify.com&code=...&hmac=<real>&state=...&timestamp=...`.
- `authQuery.shop` (from `Object.fromEntries`) resolves to the **last** value, `VICTIM.myshopify.com` — identical to what Shopify originally signed, so `validateHmac` and the `state` check both **pass**.
- `query.get('shop')` (used for `cleanShop`) resolves to the **first** value, `ATTACKER.myshopify.com`.
- The library then POSTs the authorization `code` and `config.apiSecretKey` to `https://ATTACKER.myshopify.com/admin/oauth/access_token`, and (if that succeeds) creates a `Session` with `shop: cleanShop = ATTACKER.myshopify.com`.

This is a clear violation of the AUTHENTICITY invariant: the exact `shop` value that passed HMAC validation is not the `shop` value the app subsequently acts on for the token exchange and session shop.

### Impact Explanation
This is an SSRF/secret-disclosure-adjacent bug: the app is induced to send its OAuth `client_id`/`client_secret`/authorization `code` to an attacker-chosen `*.myshopify.com`/`*.shopify.com` hostname (constrained by `sanitizeShop`'s domain allow-list) rather than the shop whose signature was actually validated, and (if the POST succeeded) would create a session tagged with the attacker-controlled shop instead of the HMAC-validated one — a session/tenant desync. Full compromise (token theft) additionally depends on Shopify's own OAuth backend enforcing (or not) code-to-shop binding at `/admin/oauth/access_token`, which is outside this repository and cannot be verified here; that external check would likely cause the POST to fail. Regardless, the library-level desync between the HMAC-validated `shop` and the acted-upon `shop` is a concrete, reproducible logic flaw in this codebase.

### Likelihood Explanation
The attacker needs only to intercept/modify a query string on a request they themselves send to the app's `/auth/callback` endpoint (trivial, no MITM or secret needed) and to control (or merely name) a second `*.myshopify.com`/`*.shopify.com`-pattern string; no privileged role or secret is required. The only external unknown is whether Shopify's token endpoint itself blocks the mismatched code/shop combination.

### Recommendation
Derive `shop` for all downstream OAuth-callback logic (`cleanShop`, session creation, error/log messages) from the same normalized/validated object that `validateHmac` verified (`authQuery.shop`), never from a separate raw `query.get('shop')` call. Additionally, harden `normalizeQuery` for the `admin` signator to reject duplicate/array values for security-critical parameters (`shop`, `hmac`, `code`, `state`, `timestamp`) for plain-object inputs, mirroring the existing `APP_PROXY_SINGLE_VALUE_PARAMS` protection already applied to `appProxy`.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/callback-shop-desync.test.ts
import {URL} from 'url';

test('authQuery.shop (HMAC-validated) diverges from query.get("shop") (acted-upon)', () => {
  const url = new URL(
    'https://app.example.com/auth/callback' +
      '?shop=attacker.myshopify.com' +
      '&shop=victim.myshopify.com' +
      '&code=abc123&state=xyz&timestamp=1700000000&hmac=deadbeef',
  );
  const query = url.searchParams;

  // Used by oauth.ts for cleanShop / token-exchange target / session.shop:
  const rawShop = query.get('shop');
  expect(rawShop).toBe('attacker.myshopify.com');

  // Used by oauth.ts as the object passed into validateHmac (validQuery):
  const authQuery = Object.fromEntries(query.entries());
  expect(authQuery.shop).toBe('victim.myshopify.com');

  // Desync: the HMAC-validated shop and the acted-upon shop differ.
  expect(authQuery.shop).not.toBe(rawShop);
});
```
Expected: both assertions pass, demonstrating that `oauth.ts`'s `callback()` validates HMAC against one `shop` value while performing the token exchange/session creation against a different, attacker-influenced `shop` value drawn from the same request.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L141-194)
```typescript
    const request = await abstractConvertRequest(adapterArgs);

    const query = new URL(
      request.url,
      `${config.hostScheme}://${config.hostName}`,
    ).searchParams;
    const shop = query.get('shop')!;

    const response = {} as NormalizedResponse;
    let userAgent = request.headers['User-Agent'];
    if (Array.isArray(userAgent)) {
      userAgent = userAgent[0];
    }
    if (isbot(userAgent)) {
      logForBot({request, log, func: 'callback'});
      throw new ShopifyErrors.BotActivityDetected(
        'Invalid OAuth callback initiated by bot',
      );
    }

    log.info('Completing OAuth', {shop});

    const cookies = new Cookies(request, response, {
      keys: [config.apiSecretKey],
      secure: true,
    });

    const stateFromCookie = await cookies.getAndVerify(STATE_COOKIE_NAME);
    cookies.deleteCookie(STATE_COOKIE_NAME);
    if (!stateFromCookie) {
      log.error('Could not find OAuth cookie', {shop});

      throw new ShopifyErrors.CookieNotFound(
        `Cannot complete OAuth process. Could not find an OAuth cookie for shop url: ${shop}`,
      );
    }

    const authQuery: AuthQuery = Object.fromEntries(query.entries());
    if (!(await validQuery({config, query: authQuery, stateFromCookie}))) {
      log.error('Invalid OAuth callback', {shop, stateFromCookie});

      throw new ShopifyErrors.InvalidOAuthError('Invalid OAuth callback.');
    }

    log.debug('OAuth request is valid, requesting access token', {shop});

    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      code: query.get('code'),
      expiring: expiring ? '1' : '0',
    };

    const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L118-131)
```typescript
function normalizeQuery(query: HmacQuery, signator: HMACSignator): AuthQuery {
  if (!(query instanceof URLSearchParams)) {
    if (signator === 'appProxy') {
      for (const key of APP_PROXY_SINGLE_VALUE_PARAMS) {
        if (Array.isArray(query[key])) {
          throw new ShopifyErrors.InvalidHmacError(
            `Query parameter "${key}" must not appear more than once.`,
          );
        }
      }
    }

    return query;
  }
```
