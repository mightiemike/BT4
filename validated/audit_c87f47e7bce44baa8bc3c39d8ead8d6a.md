### Title
Missing cross-validation of `shop` argument against session token's `dest`/`iss` claim in `tokenExchange()` - ([File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts])

### Summary
`tokenExchange()` calls `decodeSessionToken(config)(sessionToken)` purely to verify the JWT's signature and audience, but discards the returned payload entirely instead of checking its `dest`/`iss` claim against the caller-supplied `shop` argument. `sanitizeShop(config)(shop, true)` only validates that `shop` matches a Shopify domain pattern (`*.myshopify.com`, `*.shopify.com`, etc.) — it does not verify that the shop belongs to the same tenant as the verified session token. The resulting `Session` is created with `shop: cleanShop` (the caller-supplied value), not the shop bound in the token.

### Finding Description
In `tokenExchange()` [1](#0-0) , the call `await decodeSessionToken(config)(sessionToken);` verifies the JWT's HS256 signature and `aud` claim inside `decodeSessionToken()` [2](#0-1) , but the returned `payload` (which contains `dest`/`iss`, the shop the token was actually minted for) is never captured or compared against the `shop` parameter. `sanitizeShop()` only enforces a domain-format regex [3](#0-2) , with no tenant binding to the token. The library then issues the outbound OAuth POST to `https://${cleanShop}/admin/oauth/access_token` using the caller-supplied shop, and — assuming a successful response — builds the resulting `Session` with `shop: cleanShop` via `createSession()` [4](#0-3) , i.e. the session's shop is fully attacker-controlled and independent of the shop actually encoded in the verified token.

Notably, this is not merely a hypothetical misuse: the library's own documentation for this API demonstrates sourcing `shop` from `req.query.shop` independently of the session token used for `sessionToken` [5](#0-4) . In contrast, the shopify-app-express implementation avoids this pitfall by deriving `shop` directly from the verified token's `dest` claim rather than from request input: `const shop = new URL(payload.dest).hostname;` [6](#0-5) . This shows the safe pattern is possible but not enforced by the core library itself — `tokenExchange()` places the entire burden of tenant binding on the caller and on Shopify's remote OAuth server, with no local defense-in-depth check.

### Impact Explanation
If a consumer follows the documented pattern (or any pattern that sources `shop` independently of the token), a caller who possesses a valid session token for shop A can force the library to attempt a token exchange against, and create a local session object under, an attacker-chosen shop B (any valid `*.myshopify.com` domain). Locally, this results in a `Session` record whose `shop` field does not match the tenant actually authenticated by the token, which is a tenant-isolation/authenticity violation (`TENANT_ISOLATION`/`AUTHENTICITY` invariant) at the library level, matching Shopify's "cross-tenant data/state access" bounty impact class. Whether this results in a genuinely usable access token for shop B depends on Shopify's own OAuth server validating the `dest` claim against the request's shop path server-side — behavior external to this repository and not verifiable from the codebase, so the ultimate real-world blast radius is bounded by that external check, but the library itself provides no defense-in-depth against it.

### Likelihood Explanation
Exploitation requires only an unprivileged actor with a valid session token for any shop and the ability to invoke the app's token-exchange endpoint with an arbitrary `shop` query value — both are attacker-reachable in the documented usage pattern shown in `tokenExchange.md`, with no special privileges, secrets, or MITM required. The core function will not throw a `ShopifyError` for this mismatch, so it is fully deterministic and repeatable.

### Recommendation
In `tokenExchange()`, capture the payload returned by `decodeSessionToken()` and cross-check the shop encoded in `dest`/`iss` against the `sanitizeShop()`-normalized `shop` argument, throwing an `InvalidShopError`/`InvalidJwtError` on mismatch before making the outbound OAuth request. This removes reliance on the caller (or Shopify's remote server) to enforce tenant binding.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/auth/oauth/__tests__/token-exchange.test.ts (illustrative)
import * as jose from 'jose';
import {tokenExchange, RequestedTokenType} from '../token-exchange';
import {getHMACKey} from '../../../utils/get-hmac-key';

test('tokenExchange does not validate shop against session token dest claim', async () => {
  const config = testConfig(); // apiKey, apiSecretKey configured
  const forgedToken = await new jose.SignJWT({
    iss: 'https://shopA.myshopify.com/admin',
    dest: 'https://shopA.myshopify.com',
    aud: config.apiKey,
    sub: '1',
    exp: Math.floor(Date.now() / 1000) + 300,
    nbf: Math.floor(Date.now() / 1000) - 10,
    iat: Math.floor(Date.now() / 1000),
    jti: '1',
    sid: '1',
  })
    .setProtectedHeader({alg: 'HS256'})
    .sign(getHMACKey(config.apiSecretKey));

  mockTokenExchangeResponse(); // mocks fetch to return 200 with a valid access_token JSON

  const {session} = await tokenExchange(config)({
    sessionToken: forgedToken,
    shop: 'shopB.myshopify.com', // mismatched shop
    requestedTokenType: RequestedTokenType.OfflineAccessToken,
  });

  expect(session.shop).toBe('shopB.myshopify.com'); // no error thrown despite dest=shopA
});
```
Expected result: no `ShopifyError` is thrown, and `session.shop` equals the attacker-supplied `shopB.myshopify.com` despite the verified token being minted for `shopA.myshopify.com`, confirming the missing cross-check.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-51)
```typescript
export function tokenExchange(config: ConfigInterface): TokenExchange {
  return async ({
    shop,
    sessionToken,
    requestedTokenType,
    expiring,
  }: TokenExchangeParams) => {
    await decodeSessionToken(config)(sessionToken);

    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      grant_type: TokenExchangeGrantType,
      subject_token: sessionToken,
      subject_token_type: IdTokenType,
      requested_token_type: requestedTokenType,
      expiring: expiring ? '1' : '0',
    };

    const cleanShop = sanitizeShop(config)(shop, true)!;
```

**File:** packages/apps/shopify-api/lib/session/decode-session-token.ts (L15-43)
```typescript
export function decodeSessionToken(config: ConfigInterface) {
  return async (
    token: string,
    {checkAudience = true}: DecodeSessionTokenOptions = {},
  ): Promise<JwtPayload> => {
    let payload: JwtPayload;
    try {
      payload = (
        await jose.jwtVerify(token, getHMACKey(config.apiSecretKey), {
          algorithms: ['HS256'],
          clockTolerance: JWT_PERMITTED_CLOCK_TOLERANCE,
        })
      ).payload as unknown as JwtPayload;
    } catch (error) {
      throw new ShopifyErrors.InvalidJwtError(
        `Failed to parse session token '${token}': ${error.message}`,
      );
    }

    // The exp and nbf fields are validated by the JWT library

    if (checkAudience && payload.aud !== config.apiKey) {
      throw new ShopifyErrors.InvalidJwtError(
        'Session token had invalid API key',
      );
    }

    return payload;
  };
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

**File:** packages/apps/shopify-api/lib/auth/oauth/create-session.ts (L62-73)
```typescript
  return new Session({
    shop,
    state,
    isOnline,
    accessToken: accessTokenResponse.access_token,
    scope: accessTokenResponse.scope,
    ...(isOnline
      ? getOnlineSessionProperties(accessTokenResponse as OnlineAccessResponse)
      : getOfflineSessionProperties(
          accessTokenResponse as OfflineAccessResponse,
        )),
  });
```

**File:** packages/apps/shopify-api/docs/reference/auth/tokenExchange.md (L14-27)
```markdown
```ts
app.get('/auth', async (req, res) => {
  const shop = shopify.utils.sanitizeShop(req.query.shop, true);
  const headerSessionToken = getSessionTokenHeader(request);
  const searchParamSessionToken = getSessionTokenFromUrlParam(request);
  const sessionToken = (headerSessionToken || searchParamSessionToken)!;

  await shopify.auth.tokenExchange({
    sessionToken,
    shop,
    requestedTokenType: RequestedTokenType.OfflineAccessToken, // or RequestedTokenType.OnlineAccessToken
    expiring: true, // Optional, defaults to false
  });
});
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L66-67)
```typescript
    const payload = await api.session.decodeSessionToken(sessionToken);
    const shop = new URL(payload.dest).hostname;
```
