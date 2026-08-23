## Analysis

The `withdrawal.rs` bug is a "blind trust" pattern: a message/object (`Transfer`) is accepted and acted upon by a critical operation (fund withdrawal) without verifying that its `source`/`target` fields are actually consistent with what was cryptographically authenticated. The closest analog in `shopify-app-js` is in the token-exchange flow.

### Title
Token exchange discards the verified `dest` claim and trusts a caller-supplied `shop` parameter, breaking the binding between the authenticated session token and the shop used for the OAuth exchange - (File: `packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts`)

### Summary
`shopify.auth.tokenExchange()` decodes and cryptographically verifies the session token, but then throws away the verified payload instead of using its `dest` (shop) claim. It uses the separately-supplied `shop` parameter — which callers can populate from unauthenticated input such as a raw query string — for both the outbound request to Shopify's OAuth endpoint and for the `shop` field written into the newly created `Session` object that is persisted to session storage.

### Finding Description
In `tokenExchange`, the session token is decoded purely to validate signature/expiry/audience, and its result is discarded: [1](#0-0) 

The function never cross-checks that the `dest` hostname embedded in the verified token matches the caller-supplied `shop` argument before using `shop` to build the token-exchange URL and the resulting `Session`: [2](#0-1) 

This is unlike the library's own vetted call sites (`performTokenExchange` in `shopify-app-express`, and the React Router/Remix `getSessionTokenContext`/token-exchange strategy), which correctly derive `shop` from the verified `payload.dest` before calling `tokenExchange`: [3](#0-2) [4](#0-3) 

However, the public `shopify.auth.tokenExchange` API and its own documentation instruct developers to build the `shop` value from `req.query.shop` — an unauthenticated, attacker-controllable parameter — independently of the session token: [5](#0-4) 

Because `tokenExchange()` itself performs no assertion that `shop === new URL(payload.dest).hostname`, the security-critical binding between "the shop this session token proves the caller belongs to" and "the shop this access token/session will be issued/stored for" is left entirely to caller discipline rather than being enforced by the library — the exact class of defect described in the report: an authenticated object (`Transfer` / session token) is accepted and consumed by a privileged operation without validating that its bound identity fields (`source`/`target` / `shop`) match what the operation is about to act on.

### Impact Explanation
If an app built directly on `shopify.auth.tokenExchange` (following the documented pattern) passes a `shop` value taken from request input rather than from the verified token, an attacker who possesses a validly-signed session token for a shop they control could attempt to have the library issue a token-exchange request and construct/store a `Session` object keyed to a different, victim shop string. Whether this becomes a full account/session takeover depends on the far-end Shopify OAuth endpoint additionally validating the `subject_token`'s `dest` against the shop in the request URL, but the shopify-app-js library provides no such defense-in-depth on its own, and the resulting `createSession(...)` call directly stores attacker-influenced `shop` into whatever `SessionStorage` the caller wires up: [6](#0-5) 

This is a session-storage injection / cross-tenant risk in the sense that the shop identity written into the session record is not verified against the authenticated subject of the token used to obtain it.

### Likelihood Explanation
Exploitability depends on Shopify's server-side `/admin/oauth/access_token` endpoint strictly enforcing `dest`-to-shop consistency for token-exchange grants (which is expected to be robust), so this is best characterized as a missing defense-in-depth check in the library rather than a demonstrated end-to-end account takeover from this repo alone. It is reachable from a single authenticated merchant/app-install (their own valid session token) combined with attacker-chosen input feeding the `shop` parameter, matching the documented (and therefore foreseeable) integration pattern.

### Recommendation
Have `tokenExchange()` retain the decoded payload and assert that `new URL(payload.dest).hostname === sanitizeShop(shop, true)` before proceeding, mirroring what `performTokenExchange` and the React Router/Remix strategies already do correctly. Alternatively, derive `shop` exclusively from the verified `dest` claim inside `tokenExchange` itself and remove the separate `shop` parameter from the public API, so the binding cannot be broken by a caller.

### Proof of Concept
Conceptual PoC (library-level, not full end-to-end against Shopify's servers):
1. Attacker installs the app on `attacker.myshopify.com` and obtains a legitimately signed App Bridge session token whose `dest` is `https://attacker.myshopify.com`.
2. Attacker-controlled or naive integration code calls:
```ts
await shopify.auth.tokenExchange({
  sessionToken, // dest = attacker.myshopify.com, but signature/aud valid
  shop: 'victim.myshopify.com', // attacker-supplied, e.g., from req.query.shop
  requestedTokenType: RequestedTokenType.OfflineAccessToken,
});
```
3. `tokenExchange` validates only the signature/expiry/audience of `sessionToken` [7](#0-6) , then issues the POST to `https://victim.myshopify.com/admin/oauth/access_token` using that token as `subject_token` [8](#0-7)  and, on any success response, stores a `Session` keyed to `victim.myshopify.com` [6](#0-5) , entirely because the library never re-validated `shop` against the token's own `dest`.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-77)
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/authenticate.ts (L207-217)
```typescript
  if (config.distribution !== AppDistribution.ShopifyAdmin) {
    const payload = await validateSessionToken(params, request, sessionToken);
    const dest = new URL(payload.dest);
    const shop = dest.hostname;

    logger.debug('Session token is valid - authenticated', {shop, payload});
    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, payload.sub)
      : api.session.getOfflineId(shop);

    return {shop, payload, sessionId, sessionToken};
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
