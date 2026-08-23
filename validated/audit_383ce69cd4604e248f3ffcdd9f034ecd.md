### Title
Webhook `domain`/`topic` header values are not covered by HMAC verification, allowing cross-tenant webhook replay - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`, `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
`process()` in `packages/apps/shopify-api/lib/webhooks/process.ts` dispatches webhook handlers using `webhookCheck.domain` and `webhookCheck.topic`, but these values are read directly from the `X-Shopify-Shop-Domain` / `X-Shopify-Topic` HTTP headers and are never included in the HMAC computation. Because the HMAC only signs the raw body, any request carrying a previously valid `(rawBody, hmac)` pair passes validation regardless of what shop domain or topic is claimed in the headers.

### Finding Description
`validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` computes the local HMAC using only `rawBody` and `config.apiSecretKey`: [1](#0-0) 
It never incorporates the `X-Shopify-Shop-Domain` or `X-Shopify-Topic` headers into the signed material.

Separately, `checkWebhooksHeaders` in `packages/apps/shopify-api/lib/webhooks/validate.ts` extracts `topic` and `domain` straight from unsigned headers and returns them as part of the "valid" result object: [2](#0-1) 

`validateFactory` first checks the HMAC (`validHmacResult`), and only if that succeeds does it call `checkWebhookHeaders` to pull the domain/topic — the two are never cross-checked against each other or against the signature: [3](#0-2) 

Finally, `callWebhookHandlers` in `process.ts` passes `webhookCheck.topic` and `webhookCheck.domain` (both attacker-controllable headers) directly into the app's registered handler callback: [4](#0-3) 

**Exploit flow:** In a multi-tenant app, all shops share the same `apiSecretKey` (the app's client secret) for webhook HMAC signing — Shopify computes `HMAC-SHA256(rawBody, apiSecretKey)` identically for every shop that installs the app. An attacker who controls one legitimately-installed shop (Shop A, "unprivileged" relative to other tenants) can trigger a real webhook delivery to their own app endpoint (e.g., by creating an order), capturing a genuinely valid `(rawBody, hmac)` pair. They can then replay that exact body+HMAC to the app's webhook endpoint while substituting `X-Shopify-Shop-Domain: shop-b.myshopify.com` and/or a different `X-Shopify-Topic`. Because the domain/topic headers are not part of the HMAC-signed data, `validateHmacFromRequestFactory` still returns `valid: true`, and `callWebhookHandlers` invokes the app's handler with `domain = "shop-b.myshopify.com"` — a shop the attacker does not own — breaking the invariant that the shop identity delivered to the handler is bound to the verified signature.

### Impact Explanation
This enables a cross-tenant forged webhook action: any app that uses `webhookCheck.domain`/`topic` (as delivered by `process()`) to key session/database lookups, trigger shop-scoped side effects (e.g., data sync, uninstall cleanup, order processing) can be made to perform those actions attributing them to a shop the attacker does not control, using a body the attacker fully manufactured on their own shop. This matches the "cross-tenant webhook action" impact class — a forged authenticated request causing state change/data access on behalf of another tenant.

### Likelihood Explanation
Requires the attacker to have at least one legitimate shop installation of the target app (readily achievable, since app installs are generally open/self-serve on the Shopify App Store or dev stores) — no admin/developer/Shopify-employee privilege and no leaked secret are needed. The attacker only needs to observe one real webhook delivery to their own shop (trivial — they can trigger it themselves) and replay it with modified headers to the same public endpoint. This is fully repeatable for any topic the attacker's own shop can generate.

### Recommendation
Bind the shop domain/topic to the cryptographic proof instead of trusting bare headers: either (a) include `X-Shopify-Shop-Domain` and `X-Shopify-Topic` in the HMAC-signed material (not possible without changing Shopify's wire format), or (b) after HMAC validation, cross-validate the claimed `domain` against session/shop storage state expected for that specific webhook subscription/registration (e.g., verify the webhook was actually registered for that shop, or verify shop record exists and is active), and reject if it doesn't match a shop known to have that webhook topic registered. At minimum, document that `domain`/`topic` are unauthenticated and host apps must not use them as sole trust anchor for shop-scoped operations without additional verification (e.g., confirm the shop exists in the app's session storage before acting).

### Proof of Concept
```javascript
// Jest-style PoC demonstrating that HMAC validity is independent of the
// X-Shopify-Shop-Domain header.

import {validateHmacFromRequestFactory} from '../../lib/utils/hmac-validator';
import {HmacValidationType} from '../../lib/utils/types';
import {WebhookType} from '../../lib/webhooks/types';
import {createSHA256HMAC} from '../../runtime/crypto';
import {HashFormat} from '../../runtime/crypto/types';

test('valid HMAC accepted regardless of shop domain header', async () => {
  const rawBody = '{"id":123,"order_number":1}';
  const hmac = await createSHA256HMAC(config.apiSecretKey, rawBody, HashFormat.Base64);

  // Legit request originally delivered for shop-a.myshopify.com
  const forgedRequest = buildMockRequest({
    headers: {
      'X-Shopify-Hmac-Sha256': hmac,
      'X-Shopify-Shop-Domain': 'shop-b.myshopify.com', // attacker swaps domain
      'X-Shopify-Topic': 'orders/create',
      'X-Shopify-Webhook-Id': 'attacker-chosen-id',
      'X-Shopify-API-Version': '2024-01',
    },
  });

  const result = await validateHmacFromRequestFactory(config)({
    type: HmacValidationType.Webhook,
    rawBody,
    webhookType: WebhookType.Webhooks,
    ...forgedRequest,
  });

  // Expected (vulnerable) behavior: HMAC check passes even though
  // domain was never part of the signed payload.
  expect(result.valid).toBe(true);
});
```
Combined with `process()`, this forged request causes `callWebhookHandlers` to invoke the registered handler with `domain: 'shop-b.myshopify.com'`, even though the HMAC was only ever computed (by Shopify) for the attacker's own shop A delivery. [4](#0-3)

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L189-197)
```typescript
    const validHmac = await validateHmacString(
      config,
      rawBody,
      hmac,
      HashFormat.Base64,
    );
    if (!validHmac) {
      return fail(ValidationErrorReason.InvalidHmac, type, config);
    }
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L56-73)
```typescript
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

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L146-156)
```typescript
    const {webhookId} = webhookCheck;

    try {
      await handler.callback(
        webhookCheck.topic,
        webhookCheck.domain,
        rawBody,
        webhookId,
        webhookCheck.apiVersion,
        context,
      );
```
