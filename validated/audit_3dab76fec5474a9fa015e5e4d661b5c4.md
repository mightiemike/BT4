Confirmed: `validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` computes the HMAC over `rawBody` only, using the app's global `apiSecretKey`, and `checkWebhookHeaders` in `packages/apps/shopify-api/lib/webhooks/validate.ts` only verifies that identity headers (`X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`) are *present*, never that they are cryptographically bound to the signed body. The `domain` value is then trusted directly to load/act on a session in `ensureValidOfflineSession` and to invoke the `APP_UNINSTALLED` handler that deletes app installation/session state (`packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts` `deleteAppInstallationHandler`). This is the closest unprivileged analog to the RaptorCast bug class: a signed message's identity field is never cross-checked against what was actually signed, enabling identity spoofing.

<br>

### Title
Webhook identity headers (shop domain, topic, webhook ID) are not covered by HMAC signature, enabling cross-tenant webhook forgery/DoS - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
The webhook signature validation in `shopify-api` verifies that `X-Shopify-Hmac-Sha256` matches an HMAC computed solely over the raw request body using the app's global `apiSecretKey`. The identity-bearing headers — `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-API-Version` — are never included in the signed data and are never validated to correspond to the shop/topic that actually produced the signed body. Any party who can obtain one valid `(rawBody, hmac)` pair for the app (e.g., by installing the app on their own development store and triggering a webhook) can replay that exact body+HMAC to the app's webhook endpoint while freely substituting the `X-Shopify-Shop-Domain` header to a victim shop and the `X-Shopify-Topic` header to any registered topic, because neither is part of the signed payload.

### Finding Description
`validateHmacFromRequestFactory` in [1](#0-0)  reads only `rawBody` and the `hmac` header, computes `createSHA256HMAC(config.apiSecretKey, rawBody, ...)`, and compares it via `safeCompare`. It never reads or binds the `domain`, `topic`, or `webhookId` headers into the HMAC input.

`checkWebhooksHeaders`/`checkEventsHeaders` in [2](#0-1)  only check that these headers are *present*, using `getRequiredHeader`, with no cryptographic tie to the verified HMAC.

The resulting `domain` value is then trusted downstream: `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, check.domain)` in [3](#0-2)  to look up and attach a shop's session/admin client to the webhook context. In `shopify-app-express`, the built-in `APP_UNINSTALLED` handler `deleteAppInstallationHandler` in [4](#0-3)  deletes the app installation/session state keyed purely by the unauthenticated `shop` argument sourced from `check.domain`.

Because `apiSecretKey` is the app's single client secret (shared across every shop/store installing the app, not per-tenant), an attacker who controls any shop with the app installed can capture a genuine `(rawBody, hmac)` pair for a webhook delivered to their own store, then POST that identical body+HMAC directly to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header rewritten to a victim shop's domain and/or the `X-Shopify-Topic` header rewritten to `APP_UNINSTALLED` or another registered topic. The HMAC check still passes because it never covered those headers.

### Impact Explanation
This is a root-cause identity-field vs. signature mismatch identical in class to the RaptorCast finding: cryptographic verification passes, but the claimed identity in the message is never checked against what was signed. Concretely this enables:
- Forged, attacker-chosen topic dispatch against a victim shop's webhook handlers using only a body the attacker legitimately produced for their own store.
- Triggering the default `APP_UNINSTALLED` cleanup logic against an arbitrary victim shop, deleting that shop's app-installation/session bookkeeping — a Denial of Service against a legitimate merchant's app session/auth state, forcing re-auth or breaking automated webhook-driven flows for that shop.
- Cross-tenant confusion: a webhook handler that trusts `check.domain` may perform tenant-scoped actions (e.g., data writes, GraphQL calls via `admin`) against the wrong shop's session if that shop happens to have a valid stored session.

### Likelihood Explanation
High reachability, no privileged access required: any actor able to install the target app on any Shopify development/partner store (a free, self-service action) can legitimately trigger a webhook delivery to observe a valid `(rawBody, hmac)` pair, then replay it directly to the app's public webhook endpoint with modified `shop`/`topic` headers. No secret leakage or MITM is required — this exploits the fact that the signed payload never included the identity headers in the first place.

### Recommendation
Bind the identity headers into what is being trusted before acting on them:
- At minimum, enforce webhook idempotency/replay protection using `webhookId` (many apps already need this per the docs), and reject a `(rawBody, hmac)` pair once already consumed for a different `domain`/`topic` combination.
- Consider explicitly documenting/enforcing that `domain` must match a shop with an active, expected session before invoking session-mutating default handlers like `deleteAppInstallationHandler`, and/or require the caller to additionally confirm shop identity via a second channel (e.g., only process `APP_UNINSTALLED` if a subsequent OAuth/API check confirms the app is truly uninstalled for that shop) rather than trusting the header unconditionally.
- Longer term, this mirrors Shopify's actual webhook design (secret is app-wide, not per-shop) — the library-level mitigation available to `shopify-app-js` is to add defensive checks/logging when the same `(rawBody, hmac)` is seen with differing `domain` header values, and to make this trust boundary explicit in `validate.ts`/`authenticate.ts` documentation and types.

### Proof of Concept
1. Attacker installs the target Shopify app on their own dev store `attacker-shop.myshopify.com`.
2. Attacker triggers any webhook (e.g., updates a product) and captures the raw POST body and the `X-Shopify-Hmac-Sha256` header value delivered by Shopify to the app's webhook endpoint.
3. Attacker crafts a new HTTP POST to the same webhook endpoint URL, reusing the exact captured `rawBody` and `X-Shopify-Hmac-Sha256`, but sets:
   - `X-Shopify-Shop-Domain: victim-shop.myshopify.com`
   - `X-Shopify-Topic: APP_UNINSTALLED`
   - `X-Shopify-Webhook-Id`, `X-Shopify-API-Version` set to any plausible value.
4. `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts`) validates successfully because it only checks `rawBody` against the app-wide secret.
5. `checkWebhooksHeaders` (`packages/apps/shopify-api/lib/webhooks/validate.ts`) only checks header presence and passes the forged `domain`/`topic` through as `valid: true`.
6. The app's `APP_UNINSTALLED` handler (e.g., `deleteAppInstallationHandler` in `shopify-app-express`) runs against `victim-shop.myshopify.com`, deleting that shop's stored session/installation record even though the app was never actually uninstalled there — a DoS against the victim shop's app functionality.

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

**File:** packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts (L94-108)
```typescript
export function deleteAppInstallationHandler(
  appInstallations: AppInstallations,
  config: AppConfigInterface,
) {
  return async function (
    _topic: string,
    shop: string,
    _body: any,
    _webhookId: string,
  ) {
    config.logger.debug('Deleting shop sessions', {shop});

    await appInstallations.delete(shop);
  };
}
```
