Confirmed: the domain (shop) is read from the `X-Shopify-Shop-Domain` header via `WEBHOOK_HEADER_NAMES[...].domain` while the HMAC in `validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` is computed **only over `rawBody`**, not over any headers.This confirms `check.domain` (from the unsigned `X-Shopify-Shop-Domain` / `Shopify-Shop-Domain` header) is passed directly into `ensureValidOfflineSession(params, check.domain)`, which loads whatever offline session exists for that shop string and attaches an authenticated `admin` client for it — all while the HMAC verified only the raw body, not this header.### Title
Webhook shop-domain header is not covered by HMAC, allowing cross-tenant replay in `authenticateWebhookFactory` - (File: packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts)

### Summary
`authenticateWebhookFactory` trusts the `X-Shopify-Shop-Domain` header (`check.domain`) to select which shop's offline session/access token to attach to the webhook context, but the underlying HMAC verification in `validateHmacFromRequestFactory` only signs the raw body, never the shop-domain header. An attacker who obtains one legitimately-signed `(rawBody, hmac)` pair (e.g. by installing the target app on their own store and receiving a real webhook) can replay that exact pair against the same endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` value, and the library will still treat the request as valid and bind it to that other shop's offline session.

### Finding Description
`authenticateWebhookFactory` (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:35-52`) calls `api.webhooks.validate({rawBody, rawRequest: request})`, then unconditionally uses `check.domain` to load a session: [1](#0-0) 

`validate` (`packages/apps/shopify-api/lib/webhooks/validate.ts`) calls `validateHmacFromRequestFactory`, which computes the HMAC over `rawBody` alone: [2](#0-1) 

After HMAC success, `checkWebhooksHeaders` simply reads `domain` (mapped to `ShopifyHeader.Domain`, i.e. `X-Shopify-Shop-Domain`) straight from the unauthenticated request headers with no cryptographic binding to the HMAC: [3](#0-2) 

That `domain` value flows unchecked into `ensureValidOfflineSession(params, check.domain)`, which loads the offline session for whatever shop string is supplied: [4](#0-3) [5](#0-4) 

Because the HMAC never covers the domain (or topic/webhookId) header, any request carrying a **previously-valid** `(rawBody, hmac)` pair will pass validation regardless of which `X-Shopify-Shop-Domain` is sent. An attacker who is an ordinary merchant with the target app installed on their own store legitimately receives real webhook deliveries (valid body + HMAC for their own shop). They can capture that request (their own server logs, a debugging proxy on infrastructure they control, etc. — no MITM of Shopify's infrastructure required) and replay the identical body/HMAC to the app's webhook endpoint while swapping the `X-Shopify-Shop-Domain` header to a different shop that also has the app installed. `authenticateWebhookFactory` will accept it as valid, load that other shop's offline session, and hand back an authenticated `admin` client plus `webhookContext.shop` set to the spoofed domain — so the host app's webhook handler processes attacker-supplied payload content under another merchant's credentials/session.

### Impact Explanation
This breaks per-shop authentication boundaries in the webhook pipeline: a single unprivileged merchant can cause the library to authenticate a forged request as coming from a different shop and hand the app code an `admin` client backed by that shop's real offline access token. Depending on the host app's webhook handler, this enables cross-tenant state mutation/data access using attacker-chosen (replayed) payload content under another tenant's access token — matching "forged authenticated request causing state change/data access."

### Likelihood Explanation
Requires only: (1) the attacker holds a valid app installation on any shop (any merchant, unprivileged) so they receive a genuine `(rawBody, hmac)` pair; (2) knowledge of the target shop domain that also has the app installed (often discoverable/guessable, e.g. `*.myshopify.com`); (3) the ability to send a raw HTTP POST to the app's public webhook endpoint with a modified header — no secret key, no MITM, no session/JWT forgery needed. This is fully repeatable since the same `(rawBody, hmac)` pair remains valid indefinitely (there is no timestamp/nonce binding domain to hmac and no delivery-id replay protection).

### Recommendation
Bind the shop domain (and ideally topic/webhookId) into the HMAC verification, or otherwise cryptographically tie `X-Shopify-Shop-Domain` to the signed payload (e.g., include headers in the HMAC input, or verify the domain against the shop associated with the webhook subscription server-side via Shopify's API rather than trusting the header). At minimum, track `webhookId`/`eventId` per shop to detect and reject cross-shop reuse of a previously-seen HMAC/body pair.

### Proof of Concept
```ts
// Pseudocode illustrating the replay
// 1. Attacker owns shop-a.myshopify.com with the app installed, and captures
//    a genuine webhook delivery to their own endpoint:
const rawBody = '{"id": 123, "note": "attacker-controlled fields from their own order"}';
const genuineHmac = capturedHeaders['X-Shopify-Hmac-Sha256']; // valid, signed by Shopify with the real apiSecretKey

// 2. Attacker replays the identical body+hmac, but swaps the domain header
//    to a different shop (shop-b.myshopify.com) that also has the app installed:
await fetch('https://target-app.example.com/webhooks', {
  method: 'POST',
  headers: {
    'X-Shopify-Topic': 'orders/create',
    'X-Shopify-Hmac-Sha256': genuineHmac,       // unchanged, still valid for rawBody
    'X-Shopify-Shop-Domain': 'shop-b.myshopify.com', // spoofed
    'X-Shopify-API-Version': '2024-01',
    'X-Shopify-Webhook-Id': 'replayed-id',
  },
  body: rawBody,
});
// Expected (buggy) result: authenticateWebhookFactory returns valid=true,
// webhookContext.shop === 'shop-b.myshopify.com', and `admin` is bound to
// shop-b's real offline session/access token — despite the payload
// originating from shop-a's genuine webhook.
```
A Jest test can assert this by calling `authenticateWebhookFactory` with a request whose HMAC is computed over a fixed `rawBody` and asserting that varying only the `X-Shopify-Shop-Domain` header (while keeping the same HMAC) still returns `valid: true` with `session`/`admin` bound to the spoofed shop, provided that shop has a stored offline session.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-52)
```typescript
    const check = await api.webhooks.validate({
      rawBody,
      rawRequest: request,
    });

    if (!check.valid) {
      if (check.reason === WebhookValidationErrorReason.InvalidHmac) {
        logger.debug('Webhook HMAC validation failed', check);
        throw new Response(undefined, {
          status: 401,
          statusText: 'Unauthorized',
        });
      } else {
        logger.debug('Webhook validation failed', check);
        throw new Response(undefined, {status: 400, statusText: 'Bad Request'});
      }
    }
    const session = await ensureValidOfflineSession(params, check.domain);
```

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

**File:** packages/apps/shopify-app-remix/src/server/helpers/ensure-valid-offline-session.ts (L1-15)
```typescript
import {BasicParams} from '../types';

import {createOrLoadOfflineSession} from './create-or-load-offline-session';
import {ensureOfflineTokenIsNotExpired} from './ensure-offline-token-is-not-expired';

export async function ensureValidOfflineSession(
  params: BasicParams,
  shop: string,
) {
  const session = await createOrLoadOfflineSession(params, shop);

  if (!session) return undefined;

  return ensureOfflineTokenIsNotExpired(session, params, shop);
}
```

**File:** packages/apps/shopify-app-remix/src/server/helpers/create-or-load-offline-session.ts (L1-19)
```typescript
import {AppDistribution, BasicParams} from '../types';

export async function createOrLoadOfflineSession(
  {api, config, logger}: BasicParams,
  shop: string,
) {
  if (config.distribution === AppDistribution.ShopifyAdmin) {
    logger.debug('Creating custom app session from configured access token', {
      shop,
    });
    return api.session.customAppSession(shop);
  } else {
    logger.debug('Loading offline session from session storage', {shop});
    const offlineSessionId = api.session.getOfflineId(shop);
    const session = await config.sessionStorage!.loadSession(offlineSessionId);

    return session;
  }
}
```
