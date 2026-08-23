## Title
`tokenExchange` does not verify that the session token's shop (`dest`) matches the caller-supplied `shop` parameter before exchanging for an access token — ([File: packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts])

### Summary
The reported QuickSwap bug is that `safeTransfer` performs a low-level call and trusts its "success" result without checking that the actual referenced entity (the token contract) still exists/corresponds to what's expected — it defers correctness entirely to an external, unchecked condition. The `tokenExchange` function in `shopify-api` has the same structural flaw: it verifies that a session token is cryptographically valid (a local, narrow check) but never checks that the *shop encoded inside that token* actually matches the *shop parameter supplied by the caller*. It defers the shop/token correspondence check entirely to Shopify's remote `/admin/oauth/access_token` endpoint, exactly like `safeTransfer` defers contract-existence checking to the EVM's leniency around calls to non-existent accounts.

### Finding Description
`tokenExchange` decodes and validates the session token's signature/expiry/audience, but discards the decoded payload immediately without cross-checking it against the `shop` argument: [1](#0-0) 

Specifically:
```ts
await decodeSessionToken(config)(sessionToken);   // result discarded, payload.dest never used
...
const cleanShop = sanitizeShop(config)(shop, true)!;   // shop comes purely from caller input
const postResponse = await fetchRequestFactory(config)(
  `https://${cleanShop}/admin/oauth/access_token`, ...);
``` [2](#0-1) 

The library's own documented usage pattern makes the disconnect concrete: `shop` is meant to be taken directly from the request query string, independent of the token:
```ts
const shop = shopify.utils.sanitizeShop(req.query.shop, true);
...
await shopify.auth.tokenExchange({ sessionToken, shop, ... });
``` [3](#0-2) 

`decodeSessionToken` only validates signature/expiry/audience — it never validates `dest` against anything external, since `tokenExchange` doesn't pass it in: [4](#0-3) 

By contrast, the shipped `shopify-app-remix`/`shopify-app-react-router`/`shopify-app-express` middleware wrappers happen to derive `shop` from the verified token's own `dest`/`sub` claims before calling `tokenExchange`, so they are not directly exploitable through this path: [5](#0-4) [6](#0-5) 

However, this protection lives entirely in the *consumer* code, not in `tokenExchange` itself — any app built directly against `@shopify/shopify-api` (as the official docs literally instruct) that takes `shop` from a query/body parameter rather than the token is exposed to shop/token mismatch, exactly mirroring the analog's "unprivileged/permission-less protocol can't easily prevent it" character.

### Impact Explanation
If a caller mixes a validly-signed session token for shop A with a caller-supplied `shop=B`, `tokenExchange` will send `subject_token` (issued for A) to `https://B/admin/oauth/access_token` and, if that request happens to succeed, will construct and return a `Session` object keyed to shop B using an access token intended for a different tenant. This is a cross-tenant session-confusion risk class, structurally identical to the report's core issue: the local code does not itself enforce the invariant that would prevent an inconsistent/invalid state (token-shop correspondence), instead relying entirely on an external system's behavior to catch it — with no local safety net or explicit check that would "reduce the risk" as the C4 judge recommended for the original finding.

### Likelihood Explanation
Exploitability depends on: (1) an app author following the library's own documented pattern of sourcing `shop` from request input rather than from the token, and (2) Shopify's remote OAuth endpoint's actual leniency toward such mismatched exchanges, which is outside this repository's control. This mirrors the original report's "requires external factors" caveat that led the C4 judge to rate it Medium rather than Critical/High.

### Recommendation
Have `tokenExchange` decode the session token and locally verify that `payload.dest` (hostname) matches the sanitized `shop` argument before making the outbound request, throwing an `InvalidJwtError`/`InvalidOAuthError` on mismatch — analogous to adding an existence check before trusting a low-level call's result. This closes the gap regardless of what any individual consumer app does with the `shop` parameter.

### Proof of Concept
1. Attacker installs the app normally on their own shop `attacker.myshopify.com` and obtains a legitimately signed session token (`dest: attacker.myshopify.com`) via App Bridge.
2. Attacker calls the app's `/auth` (or equivalent) endpoint following the documented pattern, supplying `shop=victim.myshopify.com` as a query parameter alongside their own valid session token in the `Authorization` header.
3. `tokenExchange` calls `decodeSessionToken` (validates signature/exp/aud only, ignoring `dest`), then issues the exchange request to `https://victim.myshopify.com/admin/oauth/access_token` using the attacker's `subject_token`.
4. If Shopify's endpoint does not strictly enforce shop/token correspondence for a given edge case, the response is treated as success and a `Session` for `victim.myshopify.com` is created/stored locally, with no local check having ever verified the token was meant for that shop.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-63)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L207-218)
```typescript
  if (config.isEmbeddedApp) {
    const payload = await validateSessionToken(params, request, sessionToken);
    const dest = new URL(payload.dest);
    const shop = dest.hostname;

    logger.debug('Session token is valid - authenticated', {shop, payload});
    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, payload.sub)
      : api.session.getOfflineId(shop);

    return {shop, payload, sessionId, sessionToken};
  }
```
