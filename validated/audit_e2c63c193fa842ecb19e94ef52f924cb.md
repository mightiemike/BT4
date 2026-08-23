### Title
Webhook HMAC validation does not bind `X-Shopify-Shop-Domain` header, enabling cross-tenant webhook domain spoofing - ([File: packages/apps/shopify-api/lib/utils/hmac-validator.ts])

### Summary
`validateHmacFromRequestFactory` (used by `lib/webhooks/validate.ts`) authenticates a webhook request solely by comparing an HMAC computed over `rawBody` against the `X-Shopify-Hmac-Sha256` header, using the app's single shared `apiSecretKey`. The `X-Shopify-Shop-Domain` header, which downstream code (`webhookCheck.domain` / `check.domain`) trusts as the identity of the originating shop, is never included in that HMAC computation, so it can be freely rewritten without invalidating the signature.

### Finding Description
`validateHmacFromRequestFactory` in [1](#0-0)  reads only the `hmac` header and `rawBody`, then calls `validateHmacString(config, rawBody, hmac, HashFormat.Base64)` at [2](#0-1) , which computes `createSHA256HMAC(config.apiSecretKey, data, format)` over the body alone and does a `safeCompare`. No other header, including the shop domain, is folded into the signed data.

`lib/webhooks/validate.ts`'s `validateFactory` calls this HMAC check first and, only if it succeeds, extracts the `domain` field straight from the `X-Shopify-Shop-Domain` header via `getRequiredHeader` and attaches it unchecked to the returned `WebhookValidationValid` object [3](#0-2) .

`process.ts`'s `callWebhookHandlers` then passes `webhookCheck.domain` directly into the registered handler callback as the identifying shop [4](#0-3) . In the Remix/React Router adapters, `authenticateWebhookFactory` uses this same untrusted `check.domain` to look up the victim's session via `ensureValidOfflineSession(params, check.domain)` and exposes it (with the attacker's `rawBody` payload) to the app's webhook handler [5](#0-4) .

Since the shared secret used for HMAC is per-app (not per-shop), an unprivileged attacker who installs the target app on their own store can register a shop-specific webhook subscription pointing at a server they control, capture the genuine `rawBody` + valid `X-Shopify-Hmac-Sha256` pair Shopify sends them, and replay that exact pair to the app's real webhook endpoint with `X-Shopify-Shop-Domain` rewritten to a victim shop. The HMAC check passes because domain is not covered by the signature, and the app resolves the victim's offline session/business logic using attacker-controlled body content.

### Impact Explanation
This breaks tenant isolation: an attacker can get their own genuinely-signed webhook payload processed as if it originated from a victim shop, causing the app to load the victim's offline access token/session and run business logic (e.g. order/product handlers) against attacker-chosen `rawBody` content attributed to the victim. Depending on the handler, this can corrupt per-shop state, trigger unwanted API calls on the victim's behalf, or leak information tied to the victim's session context. This maps to Shopify's "cross-tenant data/state access" bounty impact class.

### Likelihood Explanation
Requires the attacker to install the app on their own store (a normal unprivileged action) and to be able to register/redirect a shop-specific webhook subscription to an endpoint they control in order to capture a valid `(rawBody, hmac)` pair, then replay it against the app's public webhook endpoint with a modified domain header. This is feasible for any developer/merchant who has installed the target app, and is repeatable for any topic/body combination they can trigger for their own shop.

### Recommendation
Bind the shop domain (and ideally topic/webhook id) into the value that is authenticated, e.g. by deriving/confirming the shop from a value that is cryptographically tied to the request (such as verifying `X-Shopify-Shop-Domain` against the shop associated with the stored session/access token used to originally register that specific webhook, or including the domain in the HMAC-covered payload if Shopify's delivery format supports it). At minimum, apps relying on `check.domain`/`webhookCheck.domain` for session resolution should be able to cross-check it against the set of shops that have legitimately installed the app before trusting it for session lookups.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/utils/__tests__/hmac-domain-spoof.test.ts
import {createSHA256HMAC} from '../../../runtime/crypto';
import {HashFormat} from '../../../runtime/crypto/types';
import {validateHmacFromRequestFactory} from '../hmac-validator';
import {HmacValidationType} from '../types';
import {testConfig} from '../../__tests__/test-config'; // shared test config w/ apiSecretKey

test('hmac validation ignores X-Shopify-Shop-Domain, allowing spoofing', async () => {
  const config = testConfig();
  const rawBody = JSON.stringify({id: 1, title: 'attacker-controlled'});
  const hmac = await createSHA256HMAC(config.apiSecretKey, rawBody, HashFormat.Base64);

  const validate = validateHmacFromRequestFactory(config);

  // Attacker's own shop originally received this valid (rawBody, hmac) pair.
  const resultAttackerDomain = await validate({
    type: HmacValidationType.Webhook,
    rawBody,
    rawRequest: {
      headers: {
        'X-Shopify-Hmac-Sha256': hmac,
        'X-Shopify-Shop-Domain': 'attacker-shop.myshopify.com',
        'X-Shopify-Topic': 'products/create',
        'X-Shopify-Webhook-Id': '1',
        'X-Shopify-Api-Version': '2024-01',
      },
    } as any,
  });
  expect(resultAttackerDomain.valid).toBe(true);

  // Replay same rawBody+hmac but with victim's domain header instead.
  const resultVictimDomain = await validate({
    type: HmacValidationType.Webhook,
    rawBody,
    rawRequest: {
      headers: {
        'X-Shopify-Hmac-Sha256': hmac, // unchanged, still valid
        'X-Shopify-Shop-Domain': 'victim-shop.myshopify.com', // spoofed
        'X-Shopify-Topic': 'products/create',
        'X-Shopify-Webhook-Id': '1',
        'X-Shopify-Api-Version': '2024-01',
      },
    } as any,
  });

  // Vulnerability: validation still succeeds despite the spoofed domain.
  expect(resultVictimDomain.valid).toBe(true);
});
```
Expected (current, vulnerable) behavior: both assertions pass, proving `check.domain`/`webhookCheck.domain` can be set to any shop independent of who actually signed the request, since domain is not part of the HMAC-signed data in [1](#0-0)  and [3](#0-2) .

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L33-59)
```typescript
    const rawBody = await request.text();

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
```
