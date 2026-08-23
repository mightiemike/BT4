This confirms the finding. `authenticateWebhookFactory` at `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:52` calls `ensureValidOfflineSession(params, check.domain)` using `check.domain` — which is taken directly from the unauthenticated `X-Shopify-Shop-Domain` header via `checkWebhooksHeaders` in `validate.ts:107` — to fetch that shop's offline session/access token, and hands that session/admin client back to the app together with the attacker-controlled `rawBody` payload.

### Title
Webhook `domain`/`topic` headers are not covered by HMAC, allowing cross-tenant handler/session invocation via header spoofing - (File: packages/apps/shopify-api/lib/webhooks/process.ts)

### Summary
`validateHmacFromRequestFactory` only signs `rawBody` with the app's single shared `apiSecretKey`; it never incorporates `X-Shopify-Shop-Domain` or `X-Shopify-Topic`. Since these headers are read straight into `webhookCheck.domain`/`webhookCheck.topic` and passed unauthenticated into `handler.callback(...)` in `callWebhookHandlers`, and downstream consumers like `authenticateWebhookFactory` use `check.domain` to look up another shop's offline session, an attacker who possesses any one valid `(rawBody, hmac)` pair (e.g., from a webhook legitimately delivered to their own store) can replay it with a forged `domain`/`topic` header to make handlers act as if the event belongs to a different, victim shop.

### Finding Description
- `validateFactory` (`packages/apps/shopify-api/lib/webhooks/validate.ts:46-75`) calls `validateHmacFromRequestFactory` which only computes `validateHmacString(config, rawBody, hmac, ...)` [1](#0-0)  — no header value is folded into the HMAC input.
- After HMAC success, `checkWebhooksHeaders` extracts `domain` and `topic` directly from request headers with no cross-check against the signed body [2](#0-1) .
- `callWebhookHandlers` passes `webhookCheck.topic` and `webhookCheck.domain` straight to `handler.callback(...)` [3](#0-2) .
- In `shopify-app-remix`, `authenticateWebhookFactory` uses `check.domain` to fetch/create the victim's offline session via `ensureValidOfflineSession(params, check.domain)` and constructs an authenticated `admin` client bound to that session, then exposes `payload: JSON.parse(rawBody)` (attacker-controlled) to app webhook handlers under the victim's identity [4](#0-3) .
- The app secret is shared across all shops that install the app (it is per-app, not per-shop), so a merchant with a legitimately installed app on their own store can obtain a real `(rawBody, hmac)` pair from Shopify's own webhook delivery, then POST it directly to the app's public webhook endpoint with the `domain`/`topic` headers changed, since nothing re-derives or checks those values against the signature.

### Impact Explanation
This is a cross-tenant confusion/authorization bypass: the handler callback (and, in the remix/react-router integration, the offline session lookup) trusts an unauthenticated header for tenant identity. Concrete consequences depend on host-app logic that trusts `domain`/`shop` as an authenticated tenant identifier (e.g., `app/uninstalled`, GDPR, or billing webhook handlers keyed by `domain`), which is exactly the pattern the shipped `shopify-app-remix`/`shopify-app-react-router` integrations implement. This maps to Shopify's cross-tenant data/state access impact class.

### Likelihood Explanation
Requires only an unprivileged attacker who already has (or can obtain, e.g., by installing the app on their own dev store) one legitimately signed webhook body/HMAC pair — no secret theft, no privileged role. Forging headers on a direct HTTP POST to the public webhook endpoint is trivial and fully repeatable for any topic/domain combination once one valid pair is captured.

### Recommendation
Bind `domain` and `topic` into the HMAC-verified material (or independently authenticate them), e.g., by validating that the `domain` header corresponds to a shop that actually has this webhook subscription/session record before trusting it, and/or by having `callWebhookHandlers`/`authenticateWebhookFactory` reject requests where the header-derived domain cannot be corroborated against known shop state, not just the body signature.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/webhooks/__tests__/process.test.ts
it('trusts domain header without binding it to the HMAC', async () => {
  const shopify = shopifyApi(testConfig({apiSecretKey: 'kitties are cute', isEmbeddedApp: true}));
  const app = getTestApp(shopify);
  let receivedDomain: string | undefined;
  shopify.webhooks.addHandlers({
    PRODUCTS_CREATE: {
      ...HTTP_HANDLER,
      callback: async (_topic, shopDomain) => { receivedDomain = shopDomain; },
    },
  });

  const rawBody = '{"foo": "bar"}';
  await request(app)
    .post('/webhooks')
    .set(headers({
      hmac: hmac(shopify.config.apiSecretKey, rawBody), // valid HMAC over body only
      domain: 'victim.myshopify.com', // forged, not part of signed data
    }))
    .send(rawBody)
    .expect(StatusCode.Ok);

  expect(receivedDomain).toBe('victim.myshopify.com'); // proves domain is unauthenticated
});
``` [5](#0-4)

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L189-197)
```typescript
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

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L149-156)
```typescript
      await handler.callback(
        webhookCheck.topic,
        webhookCheck.domain,
        rawBody,
        webhookId,
        webhookCheck.apiVersion,
        context,
      );
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L52-69)
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
```

**File:** packages/apps/shopify-api/lib/webhooks/__tests__/process.test.ts (L43-60)
```typescript
  it('handles the request when topic is already registered', async () => {
    const shopify = shopifyApi(
      testConfig({apiSecretKey: 'kitties are cute', isEmbeddedApp: true}),
    );
    const app = getTestApp(shopify);

    const handler = {...HTTP_HANDLER, callback: blockingWebhookHandler};
    shopify.webhooks.addHandlers({PRODUCTS_CREATE: handler});

    const response = await request(app)
      .post('/webhooks')
      .set(headers({hmac: hmac(shopify.config.apiSecretKey, rawBody)}))
      .send(rawBody)
      .expect(StatusCode.Ok);

    expect(response.body.data.errorThrown).toBeFalsy();
    expect(blockingWebhookHandlerCalled).toBeTruthy();
  });
```
