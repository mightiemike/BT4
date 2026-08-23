### Title
OAuth callback shop-parameter parsing mismatch allows POSTing the OAuth code/client secret to a shop domain never covered by HMAC validation - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Finding Description
In `callback()`, the shop used for the HMAC-verified query and the shop used to build the token-exchange target/session are derived inconsistently from the same `URLSearchParams` object. `shop = query.get('shop')!` at [1](#0-0)  returns the **first** `shop` value per the `URLSearchParams.get()` spec, and this same first-value lookup is repeated at line 194 to compute `cleanShop`, which is used both as the POST target `https://${cleanShop}/admin/oauth/access_token` (carrying `client_id`, `client_secret`, and `code` in the body) and as the `shop` recorded on the resulting `Session` object [2](#0-1) . Meanwhile, `authQuery = Object.fromEntries(query.entries())` at line 178 keeps the **last** `shop` value (object key overwritten by iteration order), and it is `authQuery` — not `query.get('shop')` — that is passed into `validQuery`/`validateHmac` for authenticity checking [3](#0-2) . `validateHmac` recomputes the local HMAC over the exact object it is given via `stringifyQueryForAdmin` [4](#0-3) , so the value that is cryptographically verified (last `shop`) is not the value that is actually acted upon (first `shop`).

By supplying a callback URL with a duplicated `shop` parameter (`shop=<attacker-domain>&shop=<hmac-signed-shop>&code=...&state=...&hmac=...`), it is possible to make the code trust an HMAC that only vouches for the second `shop` value while sending the `client_secret` and authorization `code` (in the POST body) to the first, attacker-chosen `shop` value.

However, exploiting this to reach a genuinely attacker-controlled arbitrary domain is blocked by `sanitizeShop`, which restricts `cleanShop` to `*.myshopify.com`, `*.shopify.com`, `*.myshopify.io`, `*.shop.dev`, or configured domain-transformation domains via a strict regex [5](#0-4) . This means `cleanShop` cannot be an arbitrary attacker-owned host — it must still be a Shopify-family domain, which limits the SSRF/secret-exfiltration angle to Shopify-owned infrastructure rather than an attacker server. The remaining, verifiable impact is that the shop the code exchange is attempted against, and the shop recorded on the session, is not provably the same shop the HMAC validated — i.e., a `*.myshopify.com` domain different from the one whose signature was checked can be substituted as the effective target/session shop, as long as it also passes the `shopUrlRegex`. Because the exchanged `code` is itself scoped server-side by Shopify to a specific shop's installation, a token-exchange attempt against a mismatched-but-still-Shopify shop would be rejected by Shopify's OAuth endpoint (`!postResponse.ok` → `throwFailedRequest` throws), preventing session creation in that mismatched case. I was not able to fully verify whether any downstream Shopify-side check would allow the exchange to succeed under any circumstance (e.g., same merchant owning multiple shops with overlapping trust), since that depends on Shopify's server-side OAuth code binding, which is outside this repo.

### Impact Explanation
The parsing inconsistency genuinely violates the intended invariant that "the shop value driving action must be the same one cryptographically verified," and it does cause the app's `client_secret` and the OAuth authorization `code` to be sent to a shop domain that was not the one covered by the HMAC. But because `sanitizeShop` constrains the destination to Shopify-owned domain suffixes, and because Shopify's token endpoint independently validates that the `code` belongs to the target shop, there is no demonstrated way to achieve secret disclosure to an attacker-controlled host, cross-tenant token/session takeover, or a successful forged session under the attacker's control with only the capabilities available (no `apiSecretKey`, no ability to forge a valid HMAC for an arbitrary shop, no privileged access). The practical result of the mismatch, given current constraints, is a failed token exchange (`InvalidOAuthError`/failed request) rather than a working attacker session.

### Likelihood Explanation
Triggering the code path is trivial (just a crafted GET with a duplicate `shop` query parameter), but a valid `state` cookie from a prior `begin()` call and a genuinely Shopify-signed HMAC over the last `shop` value are still required to pass `validQuery`. The attacker can only obtain such a legitimately signed callback for a shop domain they actually control an installation flow for (their own store), and `sanitizeShop`'s domain allowlist prevents redirecting the secret/code POST to an arbitrary external host. Given these constraints, a concrete, working exploit chain that yields real forged-session or secret-exfiltration impact was not established.

### Recommendation
Regardless of exploitability limits, this is a code-correctness/defense-in-depth defect that should be fixed: derive the shop used for the token-exchange URL and session creation from the same normalized `authQuery.shop` value that is HMAC-validated, rather than re-reading `query.get('shop')` (first duplicate) separately. Additionally, reject requests containing duplicate single-value parameters (`shop`, `hmac`, `code`, `state`) in the admin OAuth callback the same way `normalizeQuery` already does for `appProxy` single-value params, to eliminate this class of parsing ambiguity entirely.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/oauth-dup-shop.test.ts
import {callback} from '../oauth';
import {generateLocalHmac} from '../../../utils/hmac-validator';

test('duplicate shop params: HMAC-checked shop differs from acted-upon shop', async () => {
  const signedShop = 'legit-shop.myshopify.com';
  const attackerFirstShop = 'other-shop.myshopify.com'; // must still pass sanitizeShop regex

  const authQueryForHmac = {
    shop: signedShop,
    code: 'abc123',
    state: 'nonce-value',
    timestamp: `${Math.trunc(Date.now() / 1000)}`,
  };
  const hmac = await generateLocalHmac(testConfig)(authQueryForHmac, 'admin');

  const url =
    `/callback?shop=${attackerFirstShop}&shop=${signedShop}` +
    `&code=abc123&state=nonce-value&timestamp=${authQueryForHmac.timestamp}&hmac=${hmac}`;

  const query = new URL(url, 'https://test.host').searchParams;

  // Demonstrates the mismatch directly:
  expect(query.get('shop')).toBe(attackerFirstShop); // used for POST target / session.shop
  expect(Object.fromEntries(query.entries()).shop).toBe(signedShop); // used for HMAC validation

  // i.e. HMAC validates `signedShop`, but oauth.ts's `cleanShop` (line 194) would be `attackerFirstShop`.
});
```
This confirms the divergence: the HMAC in `validateHmac` is computed/checked against `signedShop` (last value, from `authQuery`), while `cleanShop` used for the token POST and `session.shop` in `oauth.ts` lines 194/214 is `attackerFirstShop` (first value from `query.get('shop')`).

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L143-147)
```typescript
    const query = new URL(
      request.url,
      `${config.hostScheme}://${config.hostName}`,
    ).searchParams;
    const shop = query.get('shop')!;
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L178-183)
```typescript
    const authQuery: AuthQuery = Object.fromEntries(query.entries());
    if (!(await validQuery({config, query: authQuery, stateFromCookie}))) {
      log.error('Invalid OAuth callback', {shop, stateFromCookie});

      throw new ShopifyErrors.InvalidOAuthError('Invalid OAuth callback.');
    }
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L187-217)
```typescript
    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      code: query.get('code'),
      expiring: expiring ? '1' : '0',
    };

    const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;

    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      {
        method: 'POST',
        body: JSON.stringify(body),
        headers: {
          'Content-Type': DataType.JSON,
          Accept: DataType.JSON,
        },
      },
    );

    if (!postResponse.ok) {
      throwFailedRequest(await postResponse.json(), false, postResponse);
    }

    const session: Session = createSession({
      accessTokenResponse: await postResponse.json<AccessTokenResponse>(),
      shop: cleanShop,
      state: stateFromCookie,
      config,
    });
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L84-116)
```typescript
export function validateHmac(config: ConfigInterface) {
  return async (
    query: HmacQuery,
    {signator}: {signator: HMACSignator} = {signator: 'admin'},
  ): Promise<boolean> => {
    const normalizedQuery = normalizeQuery(query, signator);

    if (signator === 'admin' && !normalizedQuery.hmac) {
      throw new ShopifyErrors.InvalidHmacError(
        'Query does not contain an HMAC value.',
      );
    }

    if (signator === 'appProxy' && !normalizedQuery.signature) {
      throw new ShopifyErrors.InvalidHmacError(
        'Query does not contain a signature value.',
      );
    }

    validateHmacTimestamp(normalizedQuery);

    const hmac =
      signator === 'appProxy'
        ? normalizedQuery.signature
        : normalizedQuery.hmac;
    const localHmac = await generateLocalHmac(config)(
      normalizedQuery,
      signator,
    );

    return safeCompare(hmac as string, localHmac);
  };
}
```

**File:** packages/apps/shopify-api/lib/utils/shop-validator.ts (L11-49)
```typescript
export function sanitizeShop(config: ConfigInterface) {
  return (shop: string, throwOnInvalid = false): string | null => {
    let shopUrl = shop;
    const domainsRegex = [
      'myshopify\\.com',
      'shopify\\.com',
      'myshopify\\.io',
      'shop\\.dev',
    ];

    // Add domains from transformations (both source and target)
    if (config.domainTransformations) {
      domainsRegex.push(...getTransformationDomains(config));
    }

    const shopUrlRegex = new RegExp(
      `^[a-zA-Z0-9][a-zA-Z0-9-_]*\\.(${domainsRegex.join('|')})[/]*$`,
    );

    const shopAdminRegex = new RegExp(
      `^admin\\.(${domainsRegex.join('|')})/store/([a-zA-Z0-9][a-zA-Z0-9-_]*)$`,
    );

    const isShopAdminUrl = shopAdminRegex.test(shopUrl);
    if (isShopAdminUrl) {
      shopUrl = shopAdminUrlToLegacyUrl(shopUrl) || '';
    }

    const sanitizedShop = shopUrlRegex.test(shopUrl) ? shopUrl : null;
    if (!sanitizedShop && throwOnInvalid) {
      throw new InvalidShopError('Received invalid shop argument');
    }

    if (sanitizedShop && config.domainTransformations) {
      return applyDomainTransformations(sanitizedShop, config);
    }

    return sanitizedShop;
  };
```
