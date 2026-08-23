### Title
Header spoofing after HMAC success allows cross-tenant webhook impersonation via shop/topic/webhookId header forgery - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
`validateFactory` verifies the HMAC only over `rawBody` (and the HMAC header itself) via `validateHmacFromRequestFactory`, but then calls `checkWebhookHeaders`, which trusts `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, and `X-Shopify-Webhook-Id` verbatim from the request without binding them to the signed payload. An attacker who possesses one previously-valid `(rawBody, hmac)` pair (e.g., a real webhook they legitimately received for their own shop) can resend it with arbitrary `domain`/`topic`/`webhookId` header values and `validateFactory` will still return `valid:true`, attributing the (unrelated) body content to a different shop/topic.

### Finding Description
In `packages/apps/shopify-api/lib/webhooks/validate.ts`, `validateFactory` (lines 46-75) first validates HMAC via `validateHmacFromRequestFactory` <cite repo="Oyahkilomeikhide/shopify-app-js--009" path="packages/apps/shopify-api/lib/webhooks/validate.ts" start="56="/> [1](#0-0) , then, if HMAC succeeds, calls `checkWebhookHeaders(request.headers, webhookType)` [2](#0-1) .

In `hmac-validator.ts`, `validateHmacFromRequestFactory` computes the HMAC purely over `rawBody` and compares it to the `X-Shopify-Hmac-Sha256`-equivalent header value via `validateHmacString(config, rawBody, hmac, HashFormat.Base64)` [3](#0-2) . None of `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, or `X-Shopify-Webhook-Id` are included in the HMAC input.

`checkWebhooksHeaders`/`checkEventsHeaders` then simply read those headers with `getRequiredHeader`/`getHeader` and place them directly into the returned `WebhookValidationValid` object as trusted fields (`domain`, `topic`, `webhookId`) [4](#0-3) .

Downstream, `process.ts`'s `callWebhookHandlers` passes `webhookCheck.topic` and `webhookCheck.domain` directly to the app's registered handler callback as authenticated, trusted values used to route/attribute the webhook (`handler.callback(webhookCheck.topic, webhookCheck.domain, rawBody, webhookId, ...)`) [5](#0-4) , and topic lookup for handler dispatch also relies on `webhookCheck.topic` (`webhookRegistry[webhookCheck.topic]`) [6](#0-5) .

An attacker who is a legitimate merchant/user of the app for shop A receives a genuine webhook (valid `rawBody` + valid `hmac`). Because the HMAC does not cover headers, the attacker can resend the exact same `rawBody`/`hmac` pair with `X-Shopify-Shop-Domain: victim-shop.myshopify.com` and/or a different `X-Shopify-Topic`/`X-Shopify-Webhook-Id`. `validateFactory` still returns `valid:true` with the attacker-chosen `domain`/`topic`/`webhookId`, and `process()` will invoke the app's handler believing the event legitimately originated from the victim shop and/or under a different topic — none of the existing checks (HMAC on body, required-header presence) catch this because they were never designed to bind header identity to body authenticity.

The existing test suite in `validate.test.ts` already demonstrates a related header-replay concept (replaying a signed cookie value as an HMAC with attacker-controlled `topic`/`domain` headers) [7](#0-6) , confirming the library's own test authors were aware that `domain`/`topic` headers are attacker-controllable independent of the HMAC-signed body.

### Impact Explanation
If a host app's webhook handler trusts `domain`/`topic` from `WebhookValidationValid` to select which shop's session/data to act on (a common and reasonable pattern, since this is exactly the API contract `process()` exposes to handler callbacks), an attacker with one valid signed webhook body can impersonate a different shop or forge a different topic for that body. This can lead to cross-tenant data confusion/corruption (e.g., an `orders/create` payload legitimately signed for shop A being attributed to shop B, causing the app to write/act on shop B's tenant data using shop A's payload), or bypassing topic-specific business logic (e.g., triggering `app/uninstalled` cleanup logic for a shop that didn't actually uninstall). This maps to Shopify's "cross-tenant data/state access" and "forged/accepted webhook request" impact classes.

### Likelihood Explanation
Exploitability requires the attacker to have obtained at least one legitimately-signed `(rawBody, hmac)` pair — trivially available to any merchant installing the app, since they will naturally receive real webhooks for their own shop. No secret key, no privileged role, and no non-default configuration are required; the attacker only needs the ability to send raw HTTP requests to the app's webhook endpoint with custom headers, which is always possible for a public-facing endpoint. This is highly repeatable — the same captured pair can be replayed indefinitely with different spoofed domain/topic values (bounded only by whatever de-duplication the app does on `webhookId`, which is also attacker-controlled).

### Recommendation
Do not treat `domain`, `topic`, or `webhookId` headers as authenticated merely because the body HMAC validated. Either (a) include these header values in the HMAC computation (not possible without changing Shopify's protocol, since Shopify itself only signs the body), or (b) require host apps/library callers to cross-check the `domain` header against a shop that is actually known/installed (e.g., has a stored session) before trusting the payload, and treat `webhookId` as a dedup key independent of trust. At minimum, the library should document explicitly in `WebhookValidationValid`/`checkWebhookHeaders` that `domain`/`topic`/`webhookId` are NOT cryptographically bound to the HMAC-verified body and must be independently validated by the consuming application against known installed shops before being used for authorization or tenant-scoping decisions.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/webhooks/__tests__/validate.test.ts (illustrative addition)
it('accepts spoofed domain/topic when replaying a valid (rawBody, hmac) pair', async () => {
  const shopify = shopifyApi(testConfig());
  const app = getTestApp(shopify);
  const rawBody = '{"foo": "bar"}'; // originally received & signed for "shop1.myshopify.io"

  const validHmac = hmac(shopify.config.apiSecretKey, rawBody);

  // Attacker replays the *same* rawBody+hmac but swaps shop/topic/webhookId headers
  const response = await request(app)
    .post('/webhooks')
    .set(
      headers({
        hmac: validHmac,
        domain: 'victim-shop.myshopify.com', // attacker-controlled, not covered by HMAC
        topic: 'app/uninstalled',            // attacker-controlled topic
        webhookId: 'attacker-chosen-id',
      }),
    )
    .send(rawBody)
    .expect(200);

  // HMAC over rawBody still validates -> valid:true with attacker-supplied domain/topic
  expect(response.body.data).toEqual(
    expect.objectContaining({
      valid: true,
      domain: 'victim-shop.myshopify.com',
      topic: 'APP_UNINSTALLED',
      hmac: validHmac,
    }),
  );
});
```
This demonstrates that `validateFactory` returns `valid: true` with an attacker-chosen `domain` and `topic` as long as the attacker supplies any previously-valid `(rawBody, hmac)` pair, confirming that shop/topic identity is not cryptographically bound to the HMAC-authenticated body.

### Citations

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L56-61)
```typescript
    const validHmacResult = await validateHmacFromRequestFactory(config)({
      type: HmacValidationType.Webhook,
      rawBody,
      webhookType,
      ...adapterArgs,
    });
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L73-73)
```typescript
    return checkWebhookHeaders(request.headers, webhookType);
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

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L140-156)
```typescript
    if (webhookCheck.webhookType !== WebhookType.Webhooks) {
      throw new ShopifyErrors.InvalidWebhookError({
        message: 'process() only supports traditional webhooks, not events',
        response,
      });
    }
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

**File:** packages/apps/shopify-api/lib/webhooks/__tests__/validate.test.ts (L97-119)
```typescript
  it('returns false when a cookie signature is replayed as the webhook HMAC', async () => {
    const shopify = shopifyApi(testConfig());
    const app = getTestApp(shopify);
    const cookieValue = 'oauth-state-nonce';
    const cookieResponse = {} as NormalizedResponse;
    const cookieJar = new Cookies(
      {headers: {}} as NormalizedRequest,
      cookieResponse,
      {keys: [shopify.config.apiSecretKey]},
    );
    await cookieJar.setAndSign('shopify_app_state', cookieValue);

    const response = await request(app)
      .post('/webhooks')
      .set(
        headers({
          hmac: cookieJar.outgoingCookieJar['shopify_app_state.sig'].value,
          topic: 'app/uninstalled',
          domain: 'victim-shop.myshopify.io',
        }),
      )
      .send(cookieValue)
      .expect(200);
```
