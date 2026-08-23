### Title
Webhook HMAC only signs the request body, not the shop-domain/topic headers, enabling cross-tenant webhook forgery - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
`shopify.webhooks.validate()` cryptographically verifies only the raw request body against `X-Shopify-Hmac-Sha256`, then trusts unsigned headers (`X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-Api-Version`) to determine which shop's session to load and which topic handler to invoke. Since a Shopify app's `apiSecretKey` is shared across all shops that install the app (not per-tenant), a merchant who owns any shop where the app is installed can capture a body+HMAC pair that is valid for their own store, then resend it directly to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header swapped to a different, victim shop that also uses the same app.

### Finding Description
`validateHmacFromRequestFactory` computes the HMAC over `rawBody` only: [1](#0-0) 

`validateFactory` then extracts `topic`, `domain`, `webhookId`, `apiVersion` from HTTP headers that are never included in the signed payload, and once the body HMAC checks out, treats those header values as trustworthy: [2](#0-1) [3](#0-2) 

The framework-level webhook handler (identical in `shopify-app-remix` and `shopify-app-react-router`) then uses the *unsigned* `check.domain` value directly to look up and load the offline session, and uses `check.topic` to route business logic and construct an authenticated Admin API client: [4](#0-3) 

Because Shopify apps use a single `apiSecretKey` shared by every installed shop (this is not a per-tenant secret), a valid HMAC for shop A's body is also a valid HMAC signature under the same key that the app uses to validate webhooks from shop B. An attacker who controls (or merely has an app installed on) any single shop can:
1. Trigger a webhook delivery to their own store (e.g. `app/uninstalled`, or any topic subscribed by the app) and capture the resulting `X-Shopify-Hmac-Sha256` + raw body pair, both of which are fully visible to them since it is delivered to a server they control or can proxy/log.
2. Replay that exact HTTP POST directly against the app's public webhook endpoint, but swap `X-Shopify-Shop-Domain` to a victim shop domain that also uses the same app (and, for events-format headers, swap `X-Shopify-Topic` as well).
3. The body's HMAC still validates (it's a legitimate signature under the app's shared secret), and the app then treats the request as an authentic webhook `for the victim shop`, loading the victim's offline session and invoking the corresponding topic handler with the attacker's chosen payload content.

This is the same root-cause pattern as the referenced report: the signature covers only a subset of the parameters that are subsequently trusted and acted upon (here, `domain`/`topic` headers), letting an attacker who has access to one valid signed message pair the signature with malicious/mismatched "parameters" that were never covered by it.

### Impact Explanation
This allows a merchant/attacker with access to their own shop's app installation to forge webhook deliveries "on behalf of" any other shop sharing the same app, without any secret leakage or network MITM. Depending on which topics the app subscribes to and how handlers use the payload/session, this can enable:
- Cross-tenant session/access abuse: the victim's offline session and Admin API client get instantiated and handed to attacker-triggered business logic with attacker-controlled body content.
- Denial of service against a specific victim shop, e.g. spoofing `app/uninstalled` to make the app wipe/deactivate the victim's stored data/session.
- Data corruption/incorrect processing if handlers trust `payload` fields (e.g., order/customer IDs) that don't correspond to the (falsely attributed) victim shop, causing state to be written against the wrong tenant.

### Likelihood Explanation
Reachable by any external, unprivileged actor who can install the app on a single shop (a standard, low-privilege action for any Shopify merchant/developer) and who knows/discovers the app's public webhook URL (typically fixed and often documented or easily inferred, e.g. `/webhooks`, `/api/webhooks`). No secret material needs to be leaked and no network position is required — the attacker directly crafts and sends the HTTP request themselves.

### Recommendation
Bind the shop identity/topic to the signature rather than trusting unsigned headers for anything with security consequences:
- Ideally, verify the `X-Shopify-Shop-Domain` (and topic) against an active, previously-registered webhook subscription for that shop/topic pair before acting, or
- Cross-check the payload body's own shop identifier (most Shopify webhook payloads include `shop_id`/similar fields, or GDPR webhooks include `shop_domain`) against the `X-Shopify-Shop-Domain` header, rejecting mismatches, or
- At minimum, document this as an explicit trust boundary and require app developers to independently verify shop ownership/topic before using `check.domain` to authorize any privileged action, since the current API surface (`shopify.webhooks.validate`) implies these header-derived fields are trustworthy once `valid: true` is returned.

### Proof of Concept
1. Install the target app (App) on attacker-owned shop `attacker.myshopify.com`.
2. Configure/observe the app's webhook subscription (e.g., `app/uninstalled`) delivered to `https://app.example.com/webhooks`, and capture the full raw request: body `B` and header `X-Shopify-Hmac-Sha256: H` (valid because `H = HMAC-SHA256(apiSecretKey, B)`, and `apiSecretKey` is the same for every shop using this app).
3. Send a new POST to `https://app.example.com/webhooks` with the exact same body `B` and header `X-Shopify-Hmac-Sha256: H`, but set `X-Shopify-Shop-Domain: victim.myshopify.com` (and matching `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-Api-Version` as needed to pass `checkWebhooksHeaders`).
4. `shopify.webhooks.validate()` returns `valid: true` (body HMAC matches) with `domain: 'victim.myshopify.com'`.
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim.myshopify.com')`, loading the victim's real offline session/Admin API client, and invokes the app's webhook business logic as if the (attacker-crafted) body legitimately originated from the victim shop.

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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-73)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L33-65)
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
        topic: check.topic as Topics,
        webhookId: check.webhookId,
        payload: JSON.parse(rawBody),
        session: undefined,
        admin: undefined,
        webhookType: check.webhookType,
```
