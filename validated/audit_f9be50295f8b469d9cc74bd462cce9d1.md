### Title
Token exchange does not verify that the caller-supplied `shop` matches the shop embedded in the verified session token - (File: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`)

### Summary
`tokenExchange()` decodes and cryptographically verifies the session token (JWT) but then discards the verified payload entirely, using a separately supplied `shop` string parameter — instead of the `dest`/`iss` claim inside the verified token — to build the target shop domain for the OAuth token-exchange request.

### Finding Description
`tokenExchange()` calls `decodeSessionToken(config)(sessionToken)` purely to validate the JWT signature/expiry, and never inspects the returned payload's `dest` field to confirm it matches the `shop` argument passed into the function: [1](#0-0) 

This is structurally analogous to the reported `PheasantNetworkBridgeChild.acceptUpwardTrade()` bug: an auxiliary caller-supplied value (`_tokenTypeIndex` / here, `shop`) that is expected to correspond to a value already embedded and cryptographically bound inside a verified piece of evidence (the network id in the evidence struct / here, the `dest` claim in the session token) is never cross-checked against that embedded value.

In the shipped consumer packages (`shopify-app-express`'s `perform-token-exchange.ts`, `shopify-app-remix`/`shopify-app-react-router`'s `token-exchange.ts` strategies), the `shop` passed to `tokenExchange()` is always derived from the same verified `payload.dest`, so in the packaged, intended call paths the values do coincide: [2](#0-1) 

However, `shopify.auth.tokenExchange()` is a public, documented API on `@shopify/shopify-api` intended for direct use by any consumer building custom middleware: [3](#0-2) 

Because the library itself performs no internal consistency check between the verified token's `dest` and the caller-supplied `shop`, any custom integration that derives `shop` from an unauthenticated source (e.g., a request query parameter) rather than exclusively from the verified JWT payload would let an attacker supply a session token issued for shop A alongside a `shop` value for shop B. The library does not defend against this misuse at the point where trust boundaries are most naturally enforced.

### Impact Explanation
If a consumer of the public `tokenExchange` API sources `shop` independently from the request (a very natural mistake, since many other framework APIs in this same codebase pass `shop` from query strings, e.g. OAuth `begin`/`callback`), an attacker holding a valid session token for their own store could exchange it purportedly "as" a different shop domain. Depending on how Shopify's backend token-exchange endpoint enforces subject-token/shop binding, this could result in unintended cross-tenant token issuance or at minimum inconsistent session storage keyed by an unverified shop rather than the token's actual authenticated shop. This is a defense-in-depth failure: the verification step exists (`decodeSessionToken`) but its output is not used to constrain the very value (`shop`) that determines which store's credentials are requested.

### Likelihood Explanation
Low-to-moderate. All first-party call sites in this repository (`shopify-app-express`, `shopify-app-remix`, `shopify-app-react-router`) correctly derive `shop` from the verified `payload.dest` before calling `tokenExchange`, so the packaged strategies are not directly exploitable via anonymous HTTP requests today. The risk materializes for any external consumer of the documented `shopify.auth.tokenExchange()` API that does not itself enforce the shop/token binding — the library provides no safety net.

### Recommendation
Inside `tokenExchange()`, use the `dest` (or `iss`) claim from the already-verified session token payload as the authoritative shop value (after sanitization), or explicitly assert `sanitizeShop(config)(shop) === sanitizeShop(config)(new URL(payload.dest).hostname)` and throw an `InvalidJwtError`/`InvalidOAuthError` on mismatch, mirroring how `perform-token-exchange.ts` derives `shop` exclusively from `payload.dest`.

### Proof of Concept
1. Obtain a valid, unexpired session token for `attacker-shop.myshopify.com` (signed by the app's own API secret via legitimate embedded-app usage).
2. Build a custom handler (not using the packaged framework wrappers) that calls:
```ts
await shopify.auth.tokenExchange({
  shop: 'victim-shop.myshopify.com',
  sessionToken, // valid JWT for attacker-shop.myshopify.com
  requestedTokenType: RequestedTokenType.OfflineAccessToken,
});
```
3. `tokenExchange()` calls `decodeSessionToken(config)(sessionToken)` (line 39 of `token-exchange.ts`) purely for signature validation and discards the payload, then proceeds to send the token-exchange POST request to `https://victim-shop.myshopify.com/admin/oauth/access_token` using the attacker's session token as `subject_token`.
4. Whether this succeeds depends on Shopify's backend enforcement, but the shopify-app-js library itself performs no local check preventing this mismatched request from being constructed and sent — the gap the report calls out (`_tokenTypeIndex` vs. network id mismatch not validated against the verified evidence) is directly mirrored here (`shop` vs. `payload.dest` mismatch not validated against the verified JWT). [4](#0-3)

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L1-78)
```typescript
import {throwFailedRequest} from '../../clients/common';
import {decodeSessionToken} from '../../session/decode-session-token';
import {sanitizeShop} from '../../utils/shop-validator';
import {ConfigInterface} from '../../base-types';
import {Session} from '../../session/session';
import {DataType} from '../../clients/types';
import {fetchRequestFactory} from '../../utils/fetch-request';

import {createSession} from './create-session';
import {AccessTokenResponse} from './types';

export enum RequestedTokenType {
  OnlineAccessToken = 'urn:shopify:params:oauth:token-type:online-access-token',
  OfflineAccessToken = 'urn:shopify:params:oauth:token-type:offline-access-token',
}

const TokenExchangeGrantType =
  'urn:ietf:params:oauth:grant-type:token-exchange';
const IdTokenType = 'urn:ietf:params:oauth:token-type:id_token';

export interface TokenExchangeParams {
  shop: string;
  sessionToken: string;
  requestedTokenType: RequestedTokenType;
  expiring?: boolean;
}

export type TokenExchange = (
  params: TokenExchangeParams,
) => Promise<{session: Session}>;

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

    return {
      session: createSession({
        accessTokenResponse: await postResponse.json<AccessTokenResponse>(),
        shop: cleanShop,
        // We need to keep this as an empty string as our template DB schemas have this required
        state: '',
        config,
      }),
    };
  };
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L65-72)
```typescript
  try {
    const payload = await api.session.decodeSessionToken(sessionToken);
    const shop = new URL(payload.dest).hostname;
    const sub = payload.sub;

    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, sub)
      : api.session.getOfflineId(shop);
```

**File:** packages/apps/shopify-api/docs/reference/auth/tokenExchange.md (L1-19)
```markdown
# shopify.auth.tokenExchange

Begins the OAuth process by exchanging the current user's [session token](https://shopify.dev/docs/apps/auth/session-tokens) for an
[access token](https://shopify.dev/docs/apps/auth/access-token-types/online.md) to make authenticated Shopify API queries.

Learn more:

- [Token Exchange](../../guides/oauth.md#token-exchange)

## Examples

### Node.js

```ts
app.get('/auth', async (req, res) => {
  const shop = shopify.utils.sanitizeShop(req.query.shop, true);
  const headerSessionToken = getSessionTokenHeader(request);
  const searchParamSessionToken = getSessionTokenFromUrlParam(request);
  const sessionToken = (headerSessionToken || searchParamSessionToken)!;
```
