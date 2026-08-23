### Title
Webhook shop-domain header is not cryptographically bound to the HMAC-signed body, allowing cross-tenant session/action confusion - (File: packages/apps/shopify-api/lib/utils/hmac-validator.ts, packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
This is analogous to the `strategyId` bug: a value used later to select which tenant's resource/authority to act on (`strategyId` in the audit, `X-Shopify-Shop-Domain` here) is never validated against the value that was actually authenticated (the collateral's real strategy vs. the HMAC-signed payload). In `shopify-app-js`, `validateHmacFromRequest` only signs/verifies `rawBody` against the `X-Shopify-Hmac-Sha256` header, while `domain` (the shop identifier used to load the offline session and dispatch the webhook to the correct tenant) is read from a separate, unsigned header and trusted as-is.

### Finding Description
`validateHmacFromRequestFactory` computes the local HMAC solely from `rawBody`: [1](#0-0) 

The `domain` (shop) field is extracted independently from the `X-Shopify-Shop-Domain` header, with no cryptographic linkage to the HMAC: [2](#0-1) 

`validateFactory` only checks `rawBody`'s HMAC and then separately trusts the headers (including `domain`) via `checkWebhookHeaders`: [3](#0-2) 

This `domain` value is then used, unchecked against the signed content, to select which tenant's offline session/access token to load and to identify the shop passed to the webhook callback in both the low-level `process()` path and the higher-level Remix/React-Router `authenticateWebhookFactory`: [4](#0-3) [5](#0-4) 

Because the HMAC is computed only over `rawBody` and the shared `apiSecretKey` — not over the shop domain — any request with a *body* that produces a matching HMAC (e.g., a genuine webhook body an attacker legitimately received from Shopify for their own shop, since `rawBody` content is often generic/templated per-topic) can be replayed with an attacker-forged `X-Shopify-Shop-Domain` header pointing at a different (victim) shop that has the same app installed. The validation logic has no mechanism analogous to the recommended `strategies[strategyId].vault == unwrappedCollToken` check — i.e., no verification that the claimed `domain` corresponds to the shop that actually produced/owns the signed payload.

### Impact Explanation
If exploited, this allows a merchant/attacker who has legitimate access to their own shop's outgoing webhook payload/HMAC to redirect webhook processing to a victim shop: `ensureValidOfflineSession(params, check.domain)` / callback handlers will operate using the **victim's real offline session and access token** while processing **attacker-controlled body content** (since only the body's HMAC — not its origin shop — is verified). This is a cross-tenant confusion primitive: application webhook handlers built on top of `authenticateWebhookFactory`/`process()` that trust `shop`/`domain` as authoritative for tenant selection can be tricked into performing actions or writes against the wrong tenant using attacker-supplied payload data, since the shop domain is not part of the authenticated data.

### Likelihood Explanation
Exploitation requires the attacker to already have received (or otherwise possess) a validly-HMAC'd webhook body+signature pair, which is available to any merchant who has the app installed on their own shop (webhooks are delivered to app-controlled endpoints, and a malicious merchant can capture the raw body and HMAC signature from their own webhook deliveries). They then only need to resend the request with a modified `X-Shopify-Shop-Domain`/`X-Shopify-Api-Version`/`X-Shopify-Webhook-Id` header (none of which are covered by the HMAC) targeting the shared app endpoint. This requires the target app to have another shop's offline session already stored, which is a reasonable precondition for any multi-tenant app.

### Recommendation
Bind the shop domain (and other identifying headers such as topic and API version) into the HMAC-verified payload, or otherwise cryptographically tie the claimed `domain` to the specific webhook delivery — e.g., verify that the HMAC signature is unique per (shop, body) pair rather than per body alone, or cross-check `check.domain` against an independent trust anchor before loading the corresponding offline session, mirroring the audit's recommendation to validate `strategies[strategyId].vault == unwrappedCollToken` (i.e., validate that the domain used to select the session matches data that was actually authenticated).

### Proof of Concept
1. Attacker installs the target app on their own shop `attacker.myshopify.com` and triggers a webhook event (e.g., `PRODUCTS_CREATE`) whose payload is attacker-controlled or predictable, capturing the raw body `B` and the resulting `X-Shopify-Hmac-Sha256` header value `H` (computed by Shopify from `B` and the shared `apiSecretKey`, per `validateHmacString`: [6](#0-5) ).
2. Attacker sends a POST to the app's webhook endpoint with body `B`, header `X-Shopify-Hmac-Sha256: H` (valid, since it's a function of `B` and the secret only), but with `X-Shopify-Shop-Domain: victim.myshopify.com` (a shop that already has an offline session stored by the app).
3. `validateFactory` verifies `H` against `B` successfully (HMAC check passes since domain isn't part of the signed data) and returns `{valid: true, domain: 'victim.myshopify.com', ...}` per `checkWebhooksHeaders`.
4. `process()`/`authenticateWebhookFactory` then loads/uses the victim's stored offline session (`ensureValidOfflineSession(params, check.domain)`) and dispatches the attacker's body `B` to the app's webhook business logic as if it originated from `victim.myshopify.com`, with the app's real access token for that victim shop available to the handler.

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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L185-198)
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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-75)
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L35-59)
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
```
