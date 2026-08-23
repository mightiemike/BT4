This confirms the vulnerability pattern: webhook HMAC validation in `validateHmacFromRequestFactory` at [1](#0-0)  computes the HMAC only over `rawBody` and the app's shared `apiSecretKey` — it never binds the signature to the `X-Shopify-Shop-Domain` header. The domain/shop value used downstream is extracted separately from headers in `checkWebhooksHeaders`/`checkEventsHeaders` at [2](#0-1)  and is never cross-checked against anything cryptographically bound to that specific domain.

### Title
Webhook HMAC does not bind to shop domain, allowing forged `X-Shopify-Shop-Domain` header to spoof cross-tenant webhooks - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The reported bug class is: an unprivileged caller supplies two independent pieces of data to a sensitive operation (asset + pool), the two are never cross-validated, and the signature/authorization check does not actually cover the field that determines which entity is being acted upon. Applied here: Shopify webhook HMACs are computed over `rawBody` and the single app-level `apiSecretKey` [3](#0-2) , but the `shop`/`domain` that identifies which tenant's session/data the webhook applies to comes from the `X-Shopify-Shop-Domain` (or `shopify-shop-domain`) header, which is completely outside the HMAC's scope [2](#0-1) .

### Finding Description
`shopify.webhooks.validate`/`process` and the framework-level `authenticate.webhook` helpers validate a webhook by checking the `X-Shopify-Hmac-Sha256` header against `HMAC(apiSecretKey, rawBody)` [1](#0-0) . Because the same `apiSecretKey` is shared by the app across all of its installed shops, and the HMAC input is only the body, **any store that has the app installed can generate a body whose HMAC is valid for that same body from any other store**, then send a forged HTTP POST to the app's webhook endpoint with an arbitrary `X-Shopify-Shop-Domain` header and a body that happens to be identical byte-for-byte, or the attacker can capture a legitimate webhook delivered to their own store (with a valid HMAC) and simply relay it while swapping the `X-Shopify-Shop-Domain` header to a victim shop's domain.

Downstream, `authenticateWebhookFactory` trusts `check.domain` (derived purely from the header) to look up the offline session and build an authenticated admin client: `const session = await ensureValidOfflineSession(params, check.domain);` [4](#0-3) . This mirrors the audited bug precisely: the field used to select *which* asset/tenant is acted upon (`domain`) is never validated against the field that was actually cryptographically signed (`rawBody`/`apiSecretKey`), so an attacker who is a legitimate (if malicious) merchant of the app can trick the app into processing webhook data — and obtaining the resulting admin client / session — under a different shop's identity.

### Impact Explanation
If successful, this lets one merchant impersonate another merchant's webhook delivery, causing the app to run webhook handler logic (and load the victim's offline `Session`/admin API client) as if the event came from the victim shop. Depending on what the app's webhook handlers do with `shop`/`session` (e.g. update per-shop billing state, write to per-shop data, or issue Admin API calls), this can lead to cross-tenant data corruption, unauthorized actions taken against another merchant's store, or state confusion that an attacker weaponizes for further abuse — a direct analog to the stolen-fee scenario in the original report where an unchecked secondary parameter (`domain`/`pool`) is combined with a validated payload from an unrelated context.

### Likelihood Explanation
Reachable by any unprivileged actor who can install the app on their own store (a normal merchant) and knows/controls the webhook endpoint URL, which is always the app's own public route — no special access to Shopify's signing key or MITM capability is required, only crafting/relaying an HTTP POST with a mismatched header. The main constraint is that the attacker needs a `rawBody` whose HMAC they can produce validly (trivial: use their own store's genuine webhook delivery, or any body value, since the HMAC only covers the body and the shared secret, not the domain), making this a realistically reachable, not merely theoretical, path.

### Recommendation
Do not treat `X-Shopify-Shop-Domain` as fully trusted metadata derived only from the un-signed header set. Where possible, cross-validate the domain against data that is cryptographically bound to the specific shop (e.g., verify the domain matches a shop for which the app has an active offline session created via legitimate OAuth/token exchange with that same shop, and/or require Shopify's newer per-topic HMAC schemes that include shop-scoping) before using it to select session storage or take shop-scoped actions. At minimum, document/require that `WebhookContext.shop` returned from `authenticate.webhook` must not be used for authorization decisions unless the app confirms it independently owns/possesses a session tied to that domain that was itself established through OAuth, and add explicit guidance that the `X-Shopify-Shop-Domain` header is unauthenticated relative to the HMAC computation.

### Proof of Concept
Not independently executed against a live Shopify webhook endpoint (no test/browser/terminal access in this mode). Conceptually:
1. Attacker merchant installs the app on `attacker.myshopify.com`, triggers a webhook (e.g., `PRODUCTS_CREATE`), and captures the raw POST body plus the resulting `X-Shopify-Hmac-Sha256` value — this HMAC is valid because it only depends on `rawBody` and the app's shared secret [3](#0-2) .
2. Attacker replays the exact same body/HMAC to the app's webhook endpoint but sets `X-Shopify-Shop-Domain: victim.myshopify.com`.
3. `validateFactory`/`checkWebhooksHeaders` accepts the request as valid (HMAC check passes, headers all present) and returns `domain: 'victim.myshopify.com'` [5](#0-4) .
4. `authenticateWebhookFactory` loads the victim's offline session and admin client using this forged `domain` [4](#0-3) , and the app's webhook handler executes as though the event genuinely originated from the victim shop.

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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L52-65)
```typescript
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
