### Title
Webhook `domain` header is not cryptographically bound to HMAC, enabling cross-tenant shop-domain spoofing via webhook replay - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
`checkWebhooksHeaders` (and `checkEventsHeaders`) extract the `X-Shopify-Shop-Domain` header via `getRequiredHeader` with zero cross-validation against the HMAC, the raw body, or any shop registration record. Since the webhook HMAC is computed only over `rawBody` with the app's shared `apiSecretKey` (`validateHmacString` in `hmac-validator.ts`), and that secret is identical for every shop that installs the app, any attacker who has captured one legitimate `(rawBody, hmac)` pair delivered to a shop they control can replay it with an arbitrary `domain` header value and have `validateFactory` return `valid: true, domain: <attacker-chosen>`.

### Finding Description
`validateFactory` (`packages/apps/shopify-api/lib/webhooks/validate.ts:46-75`) validates the request purely by calling `validateHmacFromRequestFactory`, which checks `rawBody` + the `X-Shopify-Hmac-SHA256` header against a locally-recomputed HMAC using `config.apiSecretKey` [1](#0-0) . This check never reads or incorporates the `domain` header. After the HMAC check succeeds, `checkWebhookHeaders` → `checkWebhooksHeaders` independently pulls `domain` straight from the headers with `getRequiredHeader`, with no comparison to anything cryptographic [2](#0-1) .

Because `apiSecretKey` is a single shared app secret (not a per-shop secret), any shop that installs the app can generate a validly-HMAC'd request body/signature pair for that app. An attacker who controls one such shop can capture a real webhook delivery (body + `X-Shopify-Hmac-SHA256`), then replay the identical body/HMAC to the app's webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` value. `validateFactory` will still report `valid: true` with `domain` equal to the attacker-supplied value, because the HMAC check and the header-extraction step are decoupled. There is also no timestamp/nonce/replay protection on webhook HMAC validation (unlike OAuth's `validateHmacTimestamp`), so the same captured pair can be replayed indefinitely.

This matters because downstream consumers trust `domain`/`shop` as the tenant identifier without further validation: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` and its `react-router` counterpart pass `check.domain` directly into `ensureValidOfflineSession(params, check.domain)` and expose it as `shop` in the webhook context, then build an authenticated `admin` API client bound to whatever session is found for that domain [3](#0-2) . `process.ts` similarly passes `webhookCheck.domain` straight into the app's registered callback handler as the `shop` argument [4](#0-3) .

### Impact Explanation
An attacker who legitimately installs the target app on their own store can capture one valid webhook delivery and replay it with a forged `X-Shopify-Shop-Domain` header naming a victim shop. Any app-specific or shop-specific webhook handler that keys logic (session lookup, admin API client construction, data writes) off the `domain`/`shop` value will operate against the wrong tenant's session/admin client while processing attacker-supplied event content — a cross-tenant state/data access primitive. This corresponds to Shopify's "cross-tenant data/state access" bounty impact class.

### Likelihood Explanation
Preconditions are low-privilege and realistic: the attacker only needs to install the target app on any shop they control (or otherwise obtain one real webhook delivery), which is normal, unprivileged usage of a public Shopify app. No secret, MITM, or host-app misconfiguration is required — the flaw is in the default `checkWebhooksHeaders`/`validateHmacFromRequestFactory` split, and the vulnerable consumption pattern (`check.domain` → session lookup) exists in the shipped `shopify-app-remix`/`shopify-app-react-router` packages. Replay is trivially repeatable since there is no timestamp or nonce check on webhook HMAC validation.

### Recommendation
Bind the `domain` (and ideally `topic`, `webhookId`) into the HMAC computation, or require validating `domain` against a known/registered shop record (e.g., an existing session in session storage) before trusting it, and add replay protection (timestamp/nonce, e.g. reusing `HMAC_TIMESTAMP_PERMITTED_CLOCK_TOLERANCE_SEC` logic already used for OAuth) to `validateHmacFromRequestFactory` for webhooks.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/webhooks/__tests__/validate-domain-spoof.test.ts
import {shopifyApi} from '../..';
import {testConfig} from '../../__tests__/test-config';
import {headers, hmac} from './utils';

test('domain header is not bound to HMAC and can be spoofed on replay', async () => {
  const shopify = shopifyApi(testConfig({apiSecretKey: 'shared-app-secret'}));
  const rawBody = '{"id": 123}'; // captured from a real webhook sent to attacker's own shop

  const validHmac = hmac(shopify.config.apiSecretKey, rawBody); // attacker captured this from their own shop's webhook

  const forgedRequest = {
    rawBody,
    rawRequest: {
      method: 'POST',
      headers: headers({
        hmac: validHmac,
        domain: 'victim-shop.myshopify.com', // attacker-chosen, does not match the shop that produced the HMAC
      }),
    },
  };

  const result = await shopify.webhooks.validate(forgedRequest as any);

  expect(result.valid).toBe(true);
  // Attacker-controlled domain accepted despite not matching the HMAC-signing shop
  expect((result as any).domain).toBe('victim-shop.myshopify.com');
});
```
Expected: the assertion passes, confirming `checkWebhooksHeaders` returns the attacker-chosen `domain` value alongside `valid: true` with no cross-validation against the HMAC-producing shop.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-102)
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
        triggeredAt: check.triggeredAt,
        eventId: check.eventId,
      };
    } else {
      webhookContext = {
        apiVersion: check.apiVersion,
        shop: check.domain,
        topic: check.topic as Topics,
        webhookId: check.webhookId,
        payload: JSON.parse(rawBody),
        session: undefined,
        admin: undefined,
        webhookType: check.webhookType,
        handle: check.handle,
        action: check.action,
        resourceId: check.resourceId,
        triggeredAt: check.triggeredAt,
        eventId: check.eventId,
      };
    }

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
