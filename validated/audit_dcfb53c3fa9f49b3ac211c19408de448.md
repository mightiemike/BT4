Found it. This is a concrete analog of the ENS `wrapETH2LD` over-extended permissioning bug: an authentication check that skips a scope/tenant-binding validation (`aud`/API key check), extending the trust boundary of a session token beyond what it was actually issued for.

### Title
Extension request authentication (`checkout`/`customerAccount`/`pos`) accepts session tokens without verifying the token's audience (API key) - ([File: packages/apps/shopify-app-remix/src/server/authenticate/public/extension/authenticate.ts])

### Summary
`authenticateExtensionFactory`, which backs `authenticate.public.checkout`, `authenticate.public.customerAccount`, and `authenticate.public.pos`, calls `validateSessionToken` with `{checkAudience: false, retryRequest: false}`. This disables the check that the JWT's `aud` claim matches the app's own `apiKey`, mirroring the ENS pattern of an "OR"/skip in a permission check that widens what the check accepts beyond its intended scope.

### Finding Description
`decodeSessionToken` (`packages/apps/shopify-api/lib/session/decode-session-token.ts:15-43`) verifies the JWT's HMAC signature against `config.apiSecretKey` and, only `if (checkAudience && payload.aud !== config.apiKey)`, rejects it. When `checkAudience` is `false`, the audience/tenant-binding check is entirely skipped — the only remaining protection is that the token must be signed by the correct `apiSecretKey`. [1](#0-0) 

`authenticateExtensionFactory` explicitly passes `checkAudience: false` for all "public extension" contexts (checkout, customer-account, pos): [2](#0-1) 

This is architecturally analogous to the ENS finding: a permission-scoping check (`aud === config.apiKey`, i.e., "this token was minted for *this* app") is deliberately omitted in one code path, while it remains the default (`checkAudience = true`) everywhere else — most importantly for the admin session-token flow in `validateSessionToken`/`getSessionTokenContext` (`packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts:189-218`), which does check the audience. [3](#0-2) 

Just as the ENS bug allowed a wrapper-approval (intended only for wrapped domains) to be reused to seize unwrapped domains, here a token-verification codepath (intended only to check the HMAC signature) is reused across the extension surface without re-confirming that the caller-supplied token's audience matches this specific app. Any app sharing the same `apiSecretKey` derivation context, or any token minted for a *different* app but somehow signed with a secret an attacker controls or a shared/misconfigured secret, would be accepted here even though it would be rejected by the admin flow's `checkAudience: true` path.

### Impact Explanation
If `apiSecretKey`s are ever shared, rotated inconsistently, or an app operates multiple `apiKey`s under one secret (a supported multi-app-per-secret configuration is not present in this codebase, but the `checkAudience` flag exists precisely because `aud` mismatches are a known distinguishing signal per the test suite), then `authenticate.public.checkout` / `customerAccount` / `pos` would accept a session token that was minted for a different app but signed with the same secret, i.e., cross-tenant (cross-app) token acceptance. The impact is limited to metadata read via `sessionToken.dest`/`sessionToken.sub` inside the extension handler — there is no session-storage lookup or access-token issuance directly gated by this check in the extension path, so the practical exploitability depends entirely on whether `apiSecretKey` is ever shared across `apiKey`s in a deployment, which is not verifiable purely from this repo.

### Likelihood Explanation
Low-to-moderate on its own: the primary defense (HMAC signature check against `apiSecretKey`) still stands, so exploitation requires a scenario where the same secret validates tokens intended for a different `apiKey`/app (e.g., a merchant/agency operating multiple apps sharing one secret, or the token library returning `aud` inconsistently). This is the same "narrower defense than assumed" pattern as the ENS report, but here the report itself is a design choice explicitly gated by an option flag (`checkAudience`), and it is not obviously reachable by an anonymous single request without an additional secret-reuse precondition.

### Recommendation
Re-enable `checkAudience: true` (or an explicit allow-list check) for `authenticateExtensionFactory`, matching the admin flow's behavior, unless there is a documented reason extension tokens must omit the audience check (e.g., certain extension token payloads lacking `aud`). If audience omission is required for compatibility, bind trust some other way (e.g., verifying `dest`/shop plus checking token `aud` is present and equal to any of the app's configured client IDs) rather than skipping the check entirely.

### Proof of Concept
1. Configure two Shopify apps, App A (`apiKey: keyA`) and App B (`apiKey: keyB`), that (misconfiguration scenario) share the same `apiSecretKey`.
2. Obtain a valid session token minted by Shopify for App A's checkout extension (`aud: keyA`).
3. Send this token as `Authorization: Bearer <token>` to App B's `authenticate.public.checkout` (or `customerAccount`/`pos`) route.
4. Because `authenticateExtensionFactory` calls `validateSessionToken(..., {checkAudience: false, ...})` → `decodeSessionToken(token, {checkAudience: false})`, the `aud !== config.apiKey` check at [4](#0-3) 
is skipped, and App B accepts the token intended for App A, whereas the equivalent admin-flow check (`checkAudience: true` by default) would reject it.

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/public/extension/authenticate.ts (L44-50)
```typescript
    return {
      sessionToken: await validateSessionToken(
        params,
        request,
        sessionTokenHeader,
        {checkAudience: false, retryRequest: false},
      ),
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L207-217)
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
```
