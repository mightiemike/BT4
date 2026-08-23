### Title
Webhook shop attribution (`X-Shopify-Shop-Domain`) is not covered by the HMAC signature, allowing cross-tenant spoofing/replay of webhook events - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The bug pattern in the reference report is a "confused deputy": the value used to compute an authorization parameter (multiplier) comes from one identity (sender), while the value that actually receives the effect belongs to a different identity (receiver), and the two are never cryptographically bound together. The same class of decoupling exists in the webhook-authentication path of `shopify-app-js`: the cryptographic proof (`HMAC`) covers only the raw request body, while the tenant identity used everywhere downstream (`shop`/`domain`) is taken from an unsigned header that is never included in the HMAC input.

### Finding Description
`validateHmacFromRequestFactory` computes the webhook's validity purely from `rawBody` and the `hmac` header: [1](#0-0) 

The shop identity (`domain`) is read from the `X-Shopify-Shop-Domain` header completely independently, and is never mixed into the HMAC computation: [2](#0-1) 

`checkWebhookHeaders`/`checkWebhooksHeaders` simply reads `domain` from headers, and only requires it to be *present*, not that it match anything proven by the HMAC: [3](#0-2) 

That unauthenticated `check.domain` value is then used everywhere downstream to select the tenant: to load the offline session (`ensureValidOfflineSession(params, check.domain)`), to populate `webhookContext.shop`, and to invoke app-registered webhook callbacks with `webhookCheck.domain` as the shop argument: [4](#0-3) [5](#0-4) 

Additionally, unlike the OAuth/App-Proxy HMAC path (`validateHmac`), which calls `validateHmacTimestamp` to enforce a freshness window, the webhook HMAC path (`validateHmacFromRequestFactory`) performs no timestamp/replay check at all: [6](#0-5) [7](#0-6) 

Because (a) the HMAC secret is the app's single shared `apiSecretKey` (identical for every installed shop) and (b) the header that tells the app *which* shop the payload belongs to is not part of the signed content and has no replay/freshness protection, a merchant who has legitimately installed the app on their own shop can capture a genuine `(rawBody, hmac)` pair that Shopify sent to their own webhook endpoint and re-POST it to the same endpoint with an arbitrary `X-Shopify-Shop-Domain` value. The HMAC still validates (it only checks `rawBody` against the shared secret), so the request is accepted as "valid" and processed as if it originated from a different, forged tenant.

### Impact Explanation
This is a concrete forged-Shopify-request / cross-tenant vulnerability, matching the "increaseLock" class where the value driving business logic (here, tenant attribution) is disjoint from the value that is cryptographically verified (the body). Depending on how the consuming app implements webhook handlers, this can be used to:
- Inject attacker-controlled webhook payloads (e.g., `orders/create`, `app/uninstalled`, `customers/data_request`, GDPR topics) attributed to a shop the attacker does not own, corrupting that shop's synced data or triggering shop-scoped side effects (emails, billing actions, data deletion flows) using the offline session (`access token`) of the *victim* shop, which is loaded via `ensureValidOfflineSession(params, check.domain)`.
- Because there is no timestamp check on this code path, previously captured webhooks can be replayed indefinitely, amplifying the attack window.

The severity depends on the specific webhook handler's use of `shop`/`domain`, but structurally the library provides an unauthenticated trust boundary (tenant identity) layered underneath an authenticated one (payload authenticity), which is exactly the anti-pattern flagged in the reference report.

### Likelihood Explanation
Any single merchant who installs the app on one shop can obtain genuine `(body, hmac)` pairs for their own shop by simply observing their own webhook endpoint (no MITM, no secret leak — they are the legitimate recipient of their own webhooks). Re-sending that pair with a modified `X-Shopify-Shop-Domain` header to the app's public webhook endpoint requires no privileged access and no interaction with other tenants, satisfying the "single merchant" unprivileged-actor bar. No secret material needs to be exfiltrated.

### Recommendation
- Bind the shop/domain (and ideally `api-version`, `topic`, `webhook-id`) into the HMAC input, or otherwise cryptographically tie the claimed tenant to the signed payload before trusting `check.domain` for session lookup and business logic dispatch.
- Add a timestamp/freshness check (mirroring `validateHmacTimestamp` used for OAuth/App-Proxy) and/or webhook-id based replay protection to the webhook validation path in `hmac-validator.ts` / `validate.ts`.

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com` (a normal, unprivileged merchant action) and receives a legitimate webhook, e.g. `orders/create`, at the app's public webhook endpoint. They capture the exact raw request: body `B` and header `X-Shopify-Hmac-Sha256: H` (valid because `H = HMAC(apiSecretKey, B)` and `apiSecretKey` is shared across all shops using the app).
2. Attacker re-sends an HTTP POST to the same webhook endpoint with:
   - Body: the exact same `B`
   - Header `X-Shopify-Hmac-Sha256: H` (unchanged, still valid since it only signs `B`)
   - Header `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (attacker-chosen, not signed)
   - Header `X-Shopify-Topic`, `X-Shopify-API-Version`, `X-Shopify-Webhook-Id` set to plausible/duplicated values.
3. `validateFactory` → `validateHmacFromRequestFactory` validates `H` against `B` successfully (per `packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-197`), and `checkWebhookHeaders` accepts the forged `domain` header as-is (per `packages/apps/shopify-api/lib/webhooks/validate.ts:99-146`).
4. `authenticateWebhookFactory` loads `victim-shop.myshopify.com`'s offline session via `ensureValidOfflineSession(params, check.domain)` and hands the forged payload to the app's registered handler with `shop: 'victim-shop.myshopify.com'` (per `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:52-73`), causing the app to process attacker-supplied data under the victim's tenant context.

Note: I could not find any additional binding between the header-derived `domain` and the HMAC input anywhere else in the codebase (checked `hmac-validator.ts`, `validate.ts`, `process.ts`, and both `shopify-app-remix`/`shopify-app-react-router` webhook authenticate handlers), so this appears to be the actual, intended design of the library rather than an omission in a single call site.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L84-116)
```typescript
export function validateHmac(config: ConfigInterface) {
  return async (
    query: HmacQuery,
    {signator}: {signator: HMACSignator} = {signator: 'admin'},
  ): Promise<boolean> => {
    const normalizedQuery = normalizeQuery(query, signator);

    if (signator === 'admin' && !normalizedQuery.hmac) {
      throw new ShopifyErrors.InvalidHmacError(
        'Query does not contain an HMAC value.',
      );
    }

    if (signator === 'appProxy' && !normalizedQuery.signature) {
      throw new ShopifyErrors.InvalidHmacError(
        'Query does not contain a signature value.',
      );
    }

    validateHmacTimestamp(normalizedQuery);

    const hmac =
      signator === 'appProxy'
        ? normalizedQuery.signature
        : normalizedQuery.hmac;
    const localHmac = await generateLocalHmac(config)(
      normalizedQuery,
      signator,
    );

    return safeCompare(hmac as string, localHmac);
  };
}
```

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-201)
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
}
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L52-73)
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
        name: check.name,
        triggeredAt: check.triggeredAt,
        eventId: check.eventId,
      };
    } else {
      webhookContext = {
        apiVersion: check.apiVersion,
        shop: check.domain,
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
