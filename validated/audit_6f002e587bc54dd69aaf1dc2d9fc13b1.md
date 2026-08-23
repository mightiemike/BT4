Based on my investigation, I traced the closest structural analog to the `hexToAddress` bug (truncation/parsing without validating that the discarded/remaining data is well-formed) into the shop/host derivation logic used to build OAuth redirect URIs.

### Title
Unsanitized `shop` derived from JWT `dest` via naive string replace used to build OAuth redirect/exit-iframe URI - (File: `packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts`)

### Summary
`setShopFromSessionOrToken` extracts the `shop` value from a session token's `dest` claim using `payload.dest.replace('https://', '')` instead of parsing it as a URL and validating it against the app's `sanitizeShop` allow‑list, the same pattern used by every other shop-resolution path in the codebase.

### Finding Description
`decodeSessionToken` only validates the JWT signature, audience, `exp`/`nbf` — it never validates the format/content of `dest` itself. [1](#0-0) 

In `validate-authenticated-session.ts`, when no query-string `shop` or stored session shop is available, the fallback path decodes the bearer token and derives `shop` with a raw string replace rather than `new URL(payload.dest).hostname` (used elsewhere) or `sanitizeShop`: [2](#0-1) 

That `shop` is then embedded directly into a redirect URI and handed to `redirectOutOfApp`, which — depending on the request shape — either performs a server-side `res.redirect(redirectUri)`, an exit-iframe redirect (`res.redirect(`${config.exitIframePath}?${queryParams}`)` with `shop` in the query string), or echoes it back in response headers: [3](#0-2) [4](#0-3) 

This is structurally analogous to `hexToAddress`: instead of parsing the full value and confirming the "discarded"/prefix portion is exactly what's expected (a full `https://` origin resolving to a valid `*.myshopify.com`/allow-listed domain), the code performs a naive substring removal. If `dest` does not start with `"https://"` (or contains an unexpected embedded value), `.replace('https://', '')` silently returns something other than a clean hostname (e.g. a full URL with an unexpected scheme, or the value unmodified), and that raw string is trusted as the `shop` value — exactly like the ENS bug trusted a truncated `bytes32` as a valid `address` without checking the discarded high bits were zero.

By contrast, every other place in this codebase that derives `shop` from `payload.dest` does it safely:
- `getEmbeddedAppUrl`/`buildEmbeddedAppUrl` calls `sanitizeHost(...)` before use. [5](#0-4) 
- `performTokenExchange` uses `new URL(payload.dest).hostname` (proper URL parsing). [6](#0-5) 
- `getSessionTokenContext` (remix) also uses `new URL(payload.dest)`. [7](#0-6) 
- `getCurrentSessionId` derives shop the same naive way, but immediately funnels it through `getJwtSessionId`/`getOfflineId`, which internally call `sanitizeShop(config)(shop, true)` and throw on invalid input. [8](#0-7) [9](#0-8) 

`setShopFromSessionOrToken` is the outlier: its result is used to build the redirect target but is **never** passed through `sanitizeShop`/`sanitizeHost` before use in `redirectOutOfApp`.

### Impact Explanation
If `dest` is anything other than a clean `https://{shop}` string, the unsanitized `shop` value flows into a `res.redirect()` (server-side redirect) or is embedded in the exit-iframe query string, without going through the app's shop allow-list validation that exists specifically to prevent this (`sanitizeShop`/`InvalidShopError`). This is a validation-bypass defect in a code path whose entire purpose is to safely resolve the tenant shop for an authentication redirect — the same class of "accepted-but-invalid identity used for a privileged operation" as the original finding, though the concrete blast radius here (open-redirect-style leakage of the derived value vs. ENS's node bricking) is comparatively limited and requires the session token's `dest` claim itself to be non-conforming.

### Likelihood Explanation
The `dest` claim itself is set server-side by Shopify (or `tokenExchange` responses), so under normal operation `dest` is always a well-formed `https://{shop}` string. However, the code contains no defensive validation, unlike every sibling code path, so if `dest` is ever malformed (a compromised custom-token-exchange integration, a proxy/relay set up incorrectly, or a future Shopify format change) this path silently trusts the derived string with no `InvalidShopError` guard. This matches the report's own framing: the analog is a "minor at best" defensive-coding gap, valid primarily as a hardening/consistency issue rather than a directly exploitable vulnerability under current conditions.

### Recommendation
Route the value derived in `setShopFromSessionOrToken` through `api.utils.sanitizeShop(shop, true)` (or reuse `new URL(payload.dest).hostname` followed by `sanitizeShop`) before it is used to build `redirectUri` or handed to `redirectOutOfApp`, consistent with `redirectToAuth` and `getEmbeddedAppUrl`.

### Proof of Concept
1. Obtain (or synthesize in a test harness) a valid-signature session token whose `dest` claim is not of the form `https://{shop}`, e.g. `dest: "httpshttps://attacker.example/https://shop.myshopify.com"` or `dest: "not-a-url"`.
2. Send a request with `Authorization: Bearer <token>`, no `shop` query param, and no matching stored session, to a route protected by `validateAuthenticatedSession()` with `useTokenExchange` disabled.
3. Observe that `setShopFromSessionOrToken` returns `payload.dest.replace('https://', '')` unchecked, which is then placed into `redirectUri`/`exitIframeRedirect` query params without ever hitting `sanitizeShop`, unlike the `redirectToAuth` path a few lines away. [10](#0-9)

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

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L165-182)
```typescript
  const bearerPresent = req.headers.authorization?.match(/Bearer (.*)/);
  if (bearerPresent) {
    if (!shop) {
      shop = await setShopFromSessionOrToken(api, session, bearerPresent[1]);
    }
  }

  const redirectUri = `${config.auth.path}?shop=${shop}`;
  config.logger.info(`Session was not valid. Redirecting to ${redirectUri}`, {
    shop,
  });

  return redirectOutOfApp({api, config})({
    req,
    res,
    redirectUri,
    shop: shop!,
  });
```

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L198-212)
```typescript
async function setShopFromSessionOrToken(
  api: Shopify,
  session: Session | undefined,
  token: string,
): Promise<string | undefined> {
  let shop: string | undefined;

  if (session) {
    shop = session.shop;
  } else if (api.config.isEmbeddedApp) {
    const payload = await api.session.decodeSessionToken(token);
    shop = payload.dest.replace('https://', '');
  }
  return shop;
}
```

**File:** packages/apps/shopify-app-express/src/redirect-out-of-app.ts (L43-76)
```typescript
function exitIframeRedirect(
  config: AppConfigInterface,
  req: Request,
  res: Response,
  redirectUri: string,
  shop: string,
): void {
  config.logger.debug(
    `Redirecting: request is embedded, using exitiframe path to ${redirectUri}`,
    {shop},
  );

  const queryParams = new URLSearchParams({
    ...req.query,
    shop,
    redirectUri,
  }).toString();

  res.redirect(`${config.exitIframePath}?${queryParams}`);
}

function serverSideRedirect(
  config: AppConfigInterface,
  res: Response,
  redirectUri: string,
  shop: string,
): void {
  config.logger.debug(
    `Redirecting: request is at top level, going to ${redirectUri} `,
    {shop},
  );

  res.redirect(redirectUri);
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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L65-68)
```typescript
  try {
    const payload = await api.session.decodeSessionToken(sessionToken);
    const shop = new URL(payload.dest).hostname;
    const sub = payload.sub;
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L207-211)
```typescript
  if (config.isEmbeddedApp) {
    const payload = await validateSessionToken(params, request, sessionToken);
    const dest = new URL(payload.dest);
    const shop = dest.hostname;

```

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L16-26)
```typescript
export function getJwtSessionId(config: ConfigInterface) {
  return (shop: string, userId: string): string => {
    return `${sanitizeShop(config)(shop, true)}_${userId}`;
  };
}

export function getOfflineId(config: ConfigInterface) {
  return (shop: string): string => {
    return `offline_${sanitizeShop(config)(shop, true)}`;
  };
}
```

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L54-63)
```typescript

        const jwtPayload = await decodeSessionToken(config)(matches[1]);
        const shop = jwtPayload.dest.replace(/^https:\/\//, '');

        log.debug('Found valid JWT payload', {shop, isOnline});

        if (isOnline) {
          return getJwtSessionId(config)(shop, jwtPayload.sub);
        } else {
          return getOfflineId(config)(shop);
```

**File:** packages/apps/shopify-app-express/src/redirect-to-auth.ts (L8-21)
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
```
