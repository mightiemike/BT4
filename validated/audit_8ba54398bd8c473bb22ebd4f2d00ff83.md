### Title
Webhook HMAC only signs the raw body, allowing forged shop-domain/topic headers to be accepted as a valid Shopify webhook for a different shop or topic - (File: packages/apps/shopify-api/lib/webhooks/validate.ts, packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
This is the closest unprivileged analog to the LooksRareExchange finding: like a signature that fails to bind a critical piece of execution context (`chainId`) into the signed hash, the webhook-validation logic in `shopify-app-js` computes its HMAC over the raw body only, and treats the `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, and `X-Shopify-Webhook-Id` headers as trusted, unauthenticated metadata. Anyone who can obtain one genuine `(rawBody, hmac)` pair (e.g., a merchant who installs the app on their own store and receives a real webhook) can replay that exact body/HMAC pair to the app's public webhook endpoint while forging the domain/topic/webhookId headers, and the request will still pass `validateHmacFromRequestFactory`.

### Finding Description
`validateHmacFromRequestFactory` computes the expected HMAC using only `config.apiSecretKey` and `rawBody`: [1](#0-0) 

Critically, `config.apiSecretKey` is the app's client secret — the same value for **every shop that installs the app** — not a per-shop or per-tenant secret. The HMAC digest therefore authenticates only "this body was produced with knowledge of the app secret," and says nothing about which shop or which topic the payload is for.

`validateFactory` then reads `domain`, `topic`, and `webhookId` straight out of request headers, with no cryptographic binding to the HMAC that was just checked: [2](#0-1) 

The consuming frameworks (Remix/React Router) then use the unauthenticated `check.domain` value directly to look up/create an offline session and build the webhook execution context, and `check.topic` to dispatch topic-specific logic: [3](#0-2) 

`ensureValidOfflineSession` simply passes the attacker-controlled `shop` string through to session lookup/creation without validating it against the `sanitizeShop` allow-list used elsewhere in the library: [4](#0-3) 

This mirrors the report's root cause exactly: the signature only commits to a subset of the semantically important data (body of a hard-fork-agnostic message vs. body of an app-agnostic webhook), letting the un-signed context (chainId vs. shop-domain/topic) be swapped by an attacker while the signature still validates.

### Impact Explanation
If exploited, a party with legitimate access to trigger real webhooks for one installation of the app (e.g., a merchant who installs the same public app on their own development store) can:
- Capture a genuine `(rawBody, X-Shopify-Hmac-Sha256)` pair from their own store's webhook delivery.
- Replay it against the app's webhook endpoint while substituting `X-Shopify-Shop-Domain` with a victim shop's domain that also has the app installed, and/or substituting `X-Shopify-Topic`/`X-Shopify-Webhook-Id` with an arbitrary topic.
- Because the HMAC check only verifies the body against the shared app secret, the forged request is accepted as `valid: true`, and the webhook handler is invoked with `shop: <victim domain>` and `topic: <attacker-chosen topic>`, using the offline session (and thus access token) belonging to the victim shop.

This is a cross-tenant/forged-request condition: the offline session and access token of an arbitrary shop that shares the app's client secret can be operated on by triggering handlers (e.g., `app/uninstalled`, `customers/redact`, `shop/redact`, or any app-specific data-processing webhook) with attacker-chosen payload content, without any interaction from that victim shop.

### Likelihood Explanation
Medium. It requires the attacker to (a) be a merchant/user able to install the same app on a store they control in order to capture a valid `(rawBody, hmac)` pair, and (b) know or guess a target shop's `myshopify.com` domain that also has the app installed (shop domains are often discoverable/enumerable) and a webhook topic that yields useful impact for that app's handler logic. It does not require breaking cryptography, leaking the app secret, or any MITM — only observing normal webhook traffic to one's own store and replaying it with modified headers to the same public, unauthenticated webhook endpoint.

### Recommendation
- Bind the security-relevant headers (`X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-API-Version`) into the HMAC computation (e.g., HMAC over `domain|topic|webhookId|apiVersion|rawBody`) rather than the body alone, or additionally verify these headers against the corresponding fields inside the JSON payload / a nonce registry so that they cannot be swapped independently of the signed body.
- Validate `check.domain` against `sanitizeShop`'s allow-listed shop-domain regex before using it to load/create an offline session, consistent with how `sanitizeShop` is enforced elsewhere in the library (`packages/apps/shopify-api/lib/utils/shop-validator.ts`).
- Track/deduplicate `webhookId` values (idempotency store) to reject re-delivery/replays of a previously seen `(webhookId, hmac)` combination, limiting the blast radius of any single captured payload.

### Proof of Concept
1. Install the target app on an attacker-controlled development store; trigger any webhook subscription (e.g., `products/create`) to receive a real request with a valid `X-Shopify-Hmac-Sha256` header for some `rawBody`.
2. Resend that exact `rawBody` and `X-Shopify-Hmac-Sha256` value to the same app's webhook endpoint, but replace:
   - `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (a shop that also installed the app)
   - `X-Shopify-Topic: customers/redact` (or another topic the app handles)
   - `X-Shopify-Webhook-Id: <any-string>`
3. `validateHmacFromRequestFactory` recomputes HMAC over the same `rawBody` with the same shared `config.apiSecretKey` and it matches, so `valid: true` is returned along with the attacker-controlled `domain`/`topic` fields.
4. `authenticateWebhookFactory` proceeds to call `ensureValidOfflineSession(params, check.domain)` for the victim's shop and invokes application webhook handling logic (`webhookContext`) using the victim's session/access token and the attacker-chosen topic/body.

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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-146)
```typescript
export function validateFactory(config: ConfigInterface) {
  return async function validate({
    rawBody,
    ...adapterArgs
  }: WebhookValidateParams): Promise<WebhookValidation> {
    const request: NormalizedRequest =
      await abstractConvertRequest(adapterArgs);

    const webhookType = detectWebhookType(request.headers);

    const validHmacResult = await validateHmacFromRequestFactory(config)({
      type: HmacValidationType.Webhook,
      rawBody,
      webhookType,
      ...adapterArgs,
    });

    if (!validHmacResult.valid) {
      if (validHmacResult.reason === ValidationErrorReason.InvalidHmac) {
        const log = logger(config);
        await log.debug(
          "Webhook HMAC validation failed. Please note that events manually triggered from a store's Notifications settings will fail this validation. To test this, please use the CLI or trigger the actual event in a development store.",
        );
      }
      return validHmacResult;
    }

    return checkWebhookHeaders(request.headers, webhookType);
  };
}

function getRequiredHeader(
  headers: Headers,
  headerName: string,
  missingHeaders: string[],
): string | undefined {
  const value = getHeader(headers, headerName);
  if (!value) {
    missingHeaders.push(headerName);
  }
  return value;
}

function checkWebhookHeaders(
  headers: Headers,
  webhookType: WebhookTypeValue,
): WebhookValidationMissingHeaders | WebhookValidationValid {
  if (webhookType === WebhookType.Webhooks) {
    return checkWebhooksHeaders(headers);
  }
  return checkEventsHeaders(headers);
}

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

  const name = getHeader(headers, headerNames.name);
  if (name) fields.name = name;

  const triggeredAt = getHeader(headers, headerNames.triggeredAt);
  if (triggeredAt) fields.triggeredAt = triggeredAt;

  const eventId = getHeader(headers, headerNames.eventId);
  if (eventId) fields.eventId = eventId;

  return {valid: true, ...fields};
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-66)
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

    let webhookContext: WebhookContextWithoutSession<Topics>;

    if (check.webhookType === WebhookType.Webhooks) {
      webhookContext = {
        apiVersion: check.apiVersion,
        shop: check.domain,
        topic: check.topic as Topics,
        webhookId: check.webhookId,
        payload: JSON.parse(rawBody),
        session: undefined,
        admin: undefined,
        webhookType: check.webhookType,
        name: check.name,
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
