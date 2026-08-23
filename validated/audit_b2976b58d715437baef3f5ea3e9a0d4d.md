## Title
Webhook shop domain is not bound to the HMAC-covered request body, enabling cross-tenant webhook replay/spoofing - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`, `packages/apps/shopify-api/lib/utils/hmac-validator.ts`, `packages/apps/shopify-api/lib/webhooks/process.ts`)

## Summary
`validateHmacFromRequestFactory` only verifies `HMAC(apiSecretKey, rawBody) == hmac header`, and `checkWebhooksHeaders` independently reads the shop domain straight from the unauthenticated `X-Shopify-Shop-Domain` header without any cryptographic linkage to the HMAC or body. `process()` then calls the registered handler with that header-derived `domain` alongside the HMAC-verified `rawBody`, so a replayed genuine `(rawBody, hmac)` pair with a forged domain header is treated as valid and dispatched under the attacker-chosen shop identity.

## Finding Description
The webhook pipeline is:
1. `validateFactory` in `validate.ts` calls `validateHmacFromRequestFactory(config)()`, passing `rawBody` and the adapter args (which include headers). [1](#0-0) 
2. `validateHmacFromRequest` in `hmac-validator.ts` reads only the HMAC header and calls `validateHmacString(config, rawBody, hmac, HashFormat.Base64)`, which recomputes `HMAC(apiSecretKey, rawBody)` and compares with `safeCompare`. No other header (including shop domain) is included in the signed material. [2](#0-1) [3](#0-2) 
3. On success, `validateFactory` calls `checkWebhookHeaders`, which for standard webhooks (`checkWebhooksHeaders`) pulls `domain` directly from the `X-Shopify-Shop-Domain`-equivalent header value with no cross-check against the body or HMAC. [4](#0-3) 
4. `process()` in `process.ts` then invokes the registered handler's `callback` with `webhookCheck.domain` (the header-derived value) together with the HMAC-verified `rawBody`. [5](#0-4) 

Because the app's `apiSecretKey` is shared across every shop that installs the app (it is not per-shop), any genuinely-signed webhook body/HMAC pair the attacker can observe (e.g., from a debug/error page, logging endpoint, or a webhook fired by the attacker's own installed shop) remains a valid `(rawBody, hmac)` pair for HMAC purposes regardless of which shop it is replayed against. Since the shop domain is never part of the signed content, an attacker who can POST directly to the app's public webhook endpoint (bypassing Shopify's delivery infrastructure, which this library does not restrict via IP allow-listing or any other binding) can resend that exact captured body/HMAC pair while substituting `X-Shopify-Shop-Domain` (and other unauthenticated headers like topic/webhook-id) to target a different shop. `validateHmacFromRequest` will still report `valid: true`, and `process()` will invoke the app's business-logic handler with the victim shop's domain but the attacker/leaked payload content.

## Impact Explanation
This is a cross-tenant webhook forgery: the handler is invoked believing it is processing a legitimate event for shop B ("domain") while the actual event content is one that belonged to shop A (or was otherwise not truly generated for shop B). Depending on what the host app's registered handler does (e.g., updating local DB state, syncing inventory, revoking access, marking orders fulfilled) keyed off `domain`, this can lead to state corruption or data leakage across tenant boundaries — matching Shopify's "cross-tenant data/state access" impact class.

## Likelihood Explanation
The attacker needs: (1) a previously observed valid `(rawBody, hmac)` pair — trivially obtainable from their own shop's real webhook traffic, since HMAC uses the shared `apiSecretKey`, not a per-shop secret; and (2) the ability to POST directly to the app's public webhook endpoint with custom headers, which is standard unauthenticated HTTP access with no additional precondition. No secrets, no privileged role, and no non-default configuration are required — the library provides no replay/timestamp protection for webhooks (unlike the OAuth HMAC path's `validateHmacTimestamp`) and no binding of `domain` to the signed body.

## Recommendation
Bind tenant identity to the HMAC-covered content, or otherwise validate it independently before dispatch: e.g., require that `domain` corresponds to a shop with an existing valid session/installation record before invoking handlers, add replay protection using `webhookId`/`triggeredAt` header deduplication, and/or document/enforce that host apps must re-derive the shop context from their own session store rather than trusting the raw header value returned by `WebhookValidationValid.domain`.

## Proof of Concept
```ts
// packages/apps/shopify-api/lib/webhooks/__tests__/cross-tenant-replay.test.ts
import {createSHA256HMAC} from '../../../runtime/crypto';
import {HashFormat} from '../../../runtime/crypto/types';
import {validateHmacFromRequestFactory} from '../../utils/hmac-validator';
import {HmacValidationType} from '../../utils/types';
import {WebhookType} from '../types';

test('HMAC valid body replayed with forged shop domain still passes HMAC check', async () => {
  const config = { apiSecretKey: 'shared-app-secret' } as any;
  const rawBody = JSON.stringify({order_id: 123, shop: 'shop-a.myshopify.com'});
  const hmac = await createSHA256HMAC(config.apiSecretKey, rawBody, HashFormat.Base64);

  // Attacker replays the same rawBody/hmac but targets shop B via header
  const result = await validateHmacFromRequestFactory(config)({
    type: HmacValidationType.Webhook,
    rawBody,
    webhookType: WebhookType.Webhooks,
    rawRequest: {
      headers: {
        'X-Shopify-Hmac-Sha256': hmac,
        'X-Shopify-Shop-Domain': 'shop-b.myshopify.com', // forged, victim shop
        'X-Shopify-Topic': 'orders/updated',
        'X-Shopify-Webhook-Id': 'replayed-id',
        'X-Shopify-API-Version': '2024-01',
      },
    },
  } as any);

  // Expected (vulnerable) behavior: HMAC check still passes even though
  // domain wasn't part of the signed content.
  expect(result.valid).toBe(true);
});
```
This demonstrates that `validateHmacFromRequest`/`validateHmacString` accepts the request purely based on body+HMAC, and `checkWebhooksHeaders`/`process()` will propagate the forged `domain` header to the handler untouched, confirming the tenant-isolation gap described.

### Citations

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L56-61)
```typescript
    const validHmacResult = await validateHmacFromRequestFactory(config)({
      type: HmacValidationType.Webhook,
      rawBody,
      webhookType,
      ...adapterArgs,
    });
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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L153-162)
```typescript
export async function validateHmacString(
  config: ConfigInterface,
  data: string,
  hmac: string,
  format: HashFormat,
) {
  const localHmac = await createSHA256HMAC(config.apiSecretKey, data, format);

  return safeCompare(hmac, localHmac);
}
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L185-199)
```typescript
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
```
