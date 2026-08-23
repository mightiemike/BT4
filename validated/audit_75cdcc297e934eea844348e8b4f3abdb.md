This confirms the design: the webhook HMAC is computed only over `rawBody`, not the domain header. Let me verify the exact HMAC computation call and confirm domain is passed through unverified end-to-end.### Title
`validate()` trusts unauthenticated `X-Shopify-Shop-Domain`/topic headers instead of binding shop identity to the HMAC signature - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
`validateFactory`'s `validate()` function verifies only the HMAC over the raw body via `validateHmacFromRequestFactory`, then independently reads the shop domain and topic straight from request headers in `checkWebhooksHeaders`/`checkEventsHeaders` without any cryptographic binding to those values. Because the HMAC signature never covers the domain/topic headers, any request carrying a body+HMAC pair that was legitimately signed for one shop will also pass validation with an attacker-chosen `domain` header claiming a different shop of the same app.

### Finding Description
`validateHmacFromRequestFactory` computes `validHmac` using `validateHmacString(config, rawBody, hmac, HashFormat.Base64)`, i.e. HMAC-SHA256 over `rawBody` alone with the app's shared `apiSecretKey`: [1](#0-0) 

After this check passes, `validate()` calls `checkWebhookHeaders`, which pulls `domain`, `topic`, `apiVersion`, `webhookId`, etc. directly from headers via `getRequiredHeader`/`getHeader`, with no comparison against anything covered by the signature: [2](#0-1) [3](#0-2) 

The returned `domain` field is documented and expected to be trusted by host apps to select which shop/session to act on, e.g. `shopify.session.getOfflineId(domain)` in the official usage example, and `process.ts` passes `webhookCheck.domain` directly into the app's registered callback as the `shop` parameter: [4](#0-3) 

Because `apiSecretKey` is a single app-level secret shared across every shop that installs the app (not a per-shop secret), any merchant that has installed the app can capture one of their own genuine webhook deliveries (a valid `rawBody` + `X-Shopify-Hmac-Sha256` pair signed with the shared secret) and replay that exact body/HMAC to the app's webhook endpoint while substituting `X-Shopify-Shop-Domain` (and/or the topic header) for a different, victim shop's domain that also uses the same app. `validateHmacFromRequestFactory` will still return `valid: true` because the body and HMAC are unchanged and match each other; `checkWebhooksHeaders`/`checkEventsHeaders` will then return the attacker-supplied `domain` as if it were authenticated, since nothing ties the domain header to the signature.

### Impact Explanation
If a host application follows the library's documented pattern of trusting `validate()`'s `domain` output to look up the shop's offline session or otherwise gate tenant-scoped actions, this allows a merchant who already has one legitimately signed webhook from Shopify to forge a request that the app believes originated from a different shop. Depending on what the webhook handler does with the shop identity, this can cause cross-tenant state changes or trigger tenant-scoped side effects against a shop other than the one that actually sent the request — matching the "Cross-tenant webhook action" impact class described in the question.

### Likelihood Explanation
The attacker only needs to be an existing merchant/user of an app built on this library (an "unprivileged" party per the rules, no `apiSecretKey` or other secret needed) and to have received at least one real webhook delivery from Shopify for their own shop (trivial, since apps normally have `APP_UNINSTALLED` or other webhooks configured that fire automatically). Replaying that body+HMAC pair with a modified `Domain`/topic header is a simple HTTP request; the check in `validateHmacFromRequestFactory` and `checkWebhookHeaders` will not detect the mismatch because domain/topic are outside the signed data. This is fully reproducible and repeatable, limited by the practical need for the attacker to already have a genuine valid HMAC+body pair (via their own webhook traffic) rather than needing to fabricate one from scratch.

### Recommendation
Cryptographically bind the shop identity to the signature rather than trusting the header value alone. Options: (1) include the `X-Shopify-Shop-Domain` (and topic) in the data that is HMAC-verified, or (2) after HMAC validation, cross-check the returned `domain` against an app-maintained registry of shops that are expected to be sending this webhook (e.g., confirm an offline session exists for that shop and that the webhook's `webhookId`/topic was actually registered for that shop), rejecting the request if there is a mismatch. At minimum, update `docs/reference/webhooks/validate.md` and `docs/guides/webhooks.md` to explicitly warn that the `domain`/`topic` fields returned by `validate()`/`process()` are not covered by the HMAC and must be independently corroborated (e.g., against `shopify.session.getOfflineId(domain)` existing and matching session storage) before being used for tenant-scoped decisions.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/webhooks/__tests__/validate-shop-spoof.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';
import {hmac, headers} from './utils'; // hmac(secret, rawBody) computes HMAC over body only

describe('cross-tenant shop-domain spoof', () => {
  it('accepts a genuine HMAC+body pair with a forged shop domain', async () => {
    const shopify = shopifyApi(
      testConfig({apiSecretKey: 'shared-app-secret', isEmbeddedApp: true}),
    );

    const rawBody = '{"id": 1, "foo": "bar"}';
    // Attacker (merchant of shop-a.myshopify.com) captured this real webhook:
    const genuineHmac = hmac(shopify.config.apiSecretKey, rawBody);

    // Replay with victim shop's domain substituted in the header:
    const forgedHeaders = headers({
      hmac: genuineHmac,
      domain: 'victim-shop.myshopify.com', // attacker-chosen, not covered by HMAC
    });

    const result = await shopify.webhooks.validate({
      rawBody,
      rawRequest: {headers: forgedHeaders} as any,
    });

    // BUG: validation succeeds and trusts the spoofed domain
    expect(result.valid).toBe(true);
    expect((result as any).domain).toBe('victim-shop.myshopify.com');
  });
});
```
Expected (buggy) result: `valid === true` and `domain === 'victim-shop.myshopify.com'`, confirming that a valid HMAC computed for one shop's payload is accepted with an arbitrary, attacker-controlled shop-domain claim.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L185-197)
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
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-74)
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
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L99-146)
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

  const name = getHeader(headers, headerNames.name);
  if (name) fields.name = name;

  const triggeredAt = getHeader(headers, headerNames.triggeredAt);
  if (triggeredAt) fields.triggeredAt = triggeredAt;

  const eventId = getHeader(headers, headerNames.eventId);
  if (eventId) fields.eventId = eventId;

  return {valid: true, ...fields};
}
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
