Confirmed: the webhook HMAC signature only ever covers the raw request body, and the shop-identifying header (`X-Shopify-Shop-Domain`) is never included in the signed payload or cross-checked against any other authenticated value.

### Title
Webhook shop-domain header is not bound to the HMAC signature, enabling cross-tenant webhook impersonation - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
This mirrors the bug class in the external report: a piece of data used to determine "whose state gets updated" (there, the trie hash "voter"; here, the shop domain that a webhook is attributed to) is never cryptographically bound to the authenticated proof (there, the missing per-voter uniqueness check on the hash vote; here, the missing binding of `X-Shopify-Shop-Domain` to the HMAC). This lets an actor who holds one validly-signed message reuse it to make the system attribute the action to a different tenant.

### Finding Description
`validateHmacFromRequestFactory` computes the webhook HMAC exclusively over `rawBody`: [1](#0-0) . None of the webhook headers (`X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-Api-Version`) participate in the signature computation.

`validateFactory` in `lib/webhooks/validate.ts` calls this HMAC check and, if it passes, separately extracts `domain`, `topic`, etc. straight from request headers via `checkWebhookHeaders`/`checkWebhooksHeaders`, with no cross-check that the domain is consistent with anything cryptographically verified: [2](#0-1)  and [3](#0-2) .

Downstream, `authenticateWebhookFactory` (both in `shopify-app-remix` and `shopify-app-react-router`) takes `check.domain` from that header, unconditionally, and uses it to look up/create an **offline session and admin API client** for that shop: [4](#0-3) . The session used to build the `admin` client passed to the app's webhook handler is `ensureValidOfflineSession(params, check.domain)` — driven entirely by the unauthenticated header value.

Because a Shopify app's client secret (`apiSecretKey`) is shared across all shops that install the app, an attacker who installs the app on their own shop receives real, validly-HMAC-signed webhook deliveries from Shopify for their own store. Since the signature depends only on `rawBody` and the shared secret — not on the domain — the attacker can replay that exact `(rawBody, hmac)` pair directly to the app's webhook endpoint while substituting `X-Shopify-Shop-Domain` with a victim shop's domain. `validateHmacString`'s `safeCompare` will still succeed because the body and secret are unchanged: [5](#0-4) .

### Impact Explanation
The app will process the webhook as if it originated from the victim shop: it resolves/loads the victim's offline session and admin client (`ensureValidOfflineSession`, `createOrLoadOfflineSession`) and invokes the app's webhook handler with attacker-controlled `payload`/`topic` but `shop = victim-shop`. Depending on the app's webhook handlers, this can lead to cross-tenant data corruption (e.g., an `app/uninstalled` or `shop/redact` handler being triggered against the victim's session, or business-logic handlers writing attacker-supplied data keyed to the victim's shop). This is a direct cross-tenant integrity/impact vector, analogous to the original report's "single node forces the network to accept and act on data attributed to someone else."

### Likelihood Explanation
Any merchant who can install the app on their own store (a single unprivileged actor, no leaked secrets or MITM required) can obtain a validly-signed webhook body/HMAC pair for their own shop and mount this attack purely by editing headers on a direct HTTP POST to the app's public webhook endpoint. No server compromise or knowledge of `apiSecretKey` is required. This is a low-effort, single-actor attack fitting the "single merchant/customer" unprivileged threat model.

### Recommendation
Do not trust `X-Shopify-Shop-Domain` (or other webhook headers) as an implicit identity binding. At minimum:
- Cross-validate the header-derived `domain` against the shop recorded in the offline session that is ultimately resolved, and reject/short-circuit if there is any prior binding mismatch (e.g., maintain a per-body/webhook-id replay cache).
- Consider including a canonicalized subset of critical headers (`domain`, `topic`, `webhook_id`) in the value being HMAC-verified, or enforce webhook-id uniqueness/dedup scoped per shop to prevent cross-shop replay.
- Document/require that consuming apps validate `webhookId` idempotency in a way that is also shop-scoped, since currently nothing prevents replay across shops.

### Proof of Concept
1. Install the target app on an attacker-owned dev/test shop `attacker.myshopify.com`.
2. Trigger any webhook topic the app subscribes to (e.g., `products/update`) so Shopify delivers a POST with a real body `B` and header `X-Shopify-Hmac-Sha256: H` computed as `HMAC-SHA256(apiSecretKey, B)`.
3. Capture `B` and `H` (e.g. via a local proxy/logging in the attacker's own installed app instance).
4. Re-POST directly to the app's public webhook URL with the same body `B` and header `X-Shopify-Hmac-Sha256: H`, but set `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (and any desired `X-Shopify-Topic`/`X-Shopify-Webhook-Id`).
5. `validateHmacFromRequestFactory` accepts the request (HMAC only checks `B` and `H`), and `authenticateWebhookFactory` resolves/loads the offline session for `victim-shop.myshopify.com`, invoking the app's webhook handler with the attacker's payload attributed to the victim shop.

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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-197)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-96)
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
```
