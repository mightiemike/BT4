### Title
Webhook HMAC validation does not bind the `X-Shopify-Shop-Domain` header, allowing cross-tenant webhook impersonation - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
`shopify.webhooks.validate()` / `process()` authenticate an incoming webhook solely by verifying an HMAC computed over the raw request body against the app's single, app-wide `apiSecretKey`. The shop identity used for all downstream authorization decisions (`X-Shopify-Shop-Domain`) is read from an HTTP header that is never included in the signed material, so the "authenticated" identifier (a valid body signature) and the "identity" used to act (the domain header) are not cryptographically bound together — the same root-cause pattern as the reported `V3Vault::transform` bug, where the parameter used for the authorization check (`tokenId`) was never checked against the identifier actually acted upon (the `tokenId` encoded in `data`).

### Finding Description
`validateHmac` computes the local HMAC purely from `rawBody` and the app's `apiSecretKey`, then does a `safeCompare` against the `X-Shopify-Hmac-Sha256` header: [1](#0-0) 

The shop identity, topic, and webhook id are parsed straight from unauthenticated headers in `checkWebhooksHeaders`/`checkEventsHeaders`, with no check that the value in `X-Shopify-Shop-Domain` corresponds to the shop the body was actually generated for: [2](#0-1) [3](#0-2) 

`validateFactory` returns `valid: true` together with the unauthenticated `domain` field as soon as `validHmacResult.valid` is true, and `process()` then hands that unauthenticated `domain` directly to the registered webhook callback, which is expected to use it to load the shop's session: [4](#0-3) [5](#0-4) 

The library's own documentation instructs developers to key session lookups directly off this `domain`/`shop` value returned from `validate`/`process`, with no guidance to cross-check it against anything cryptographically verified: [6](#0-5) 

Because `apiSecretKey` is one shared secret for the whole app (not per-shop), any merchant who installs the app on their own store can capture a legitimate, validly-signed webhook delivery for their own shop (e.g. `products/create`) and replay it to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header rewritten to point at a victim shop. The HMAC check still passes (it never covered that header), so `validate()`/`process()` report a "valid" webhook whose `domain`/`shop` is the attacker-chosen victim domain.

### Impact Explanation
Any handler that trusts the returned `domain`/`shop` value to look up or mutate per-tenant state is exposed to cross-tenant impersonation. The built-in `APP_UNINSTALLED` handler is a concrete, always-registered example: it calls `AppInstallations.delete(shopDomain)`, which loads and deletes every session for the given shop domain with no further verification: [7](#0-6) [8](#0-7) 

An attacker-controlled shop can therefore forge an `APP_UNINSTALLED` (or `CUSTOMERS_DATA_REQUEST`/`CUSTOMERS_REDACT`/`SHOP_REDACT`) event for any victim shop domain, causing the app to wipe the victim's stored offline/online access tokens (denial of service requiring the victim to re-install/re-auth) or run app-authored data-erasure/export logic against the wrong tenant. Custom `webhooks.addHandlers` callbacks in host apps that key business logic off `shop`/`domain` inherit the same exposure.

### Likelihood Explanation
Exploitation requires only that the attacker be a legitimate, unprivileged merchant who has installed the target app on their own store (a routine, self-service action) — no leaked secrets, MITM, or privileged access are needed. They receive a validly HMAC-signed webhook for their own shop from Shopify, then replay it to the app's public webhook endpoint with a modified `X-Shopify-Shop-Domain` header. This is a standard, low-effort HTTP replay attack against a publicly reachable endpoint.

### Recommendation
Do not use the unauthenticated `X-Shopify-Shop-Domain`/`shopify-shop-domain` header as the sole tenant identifier for security-relevant actions. At minimum:
- Verify that a session already exists for the claimed domain before performing destructive actions (defense in depth, not a full fix since attacker can target shops that do have sessions).
- Where possible, bind the shop identity into the signed payload validation (e.g., cross-check against Shopify Admin API using an access token scoped to that domain, or require app-specific webhook subscriptions delivered per-shop endpoint rather than a shared endpoint keyed by header).
- At minimum, document/require that consumers of `validate()`/`process()` treat `domain` as untrusted input and independently confirm shop ownership before mutating per-tenant data, and consider deprecating trust in the raw header value in favor of Shopify's newer per-topic signed delivery mechanisms if available.

### Proof of Concept
1. App installs handler for `APP_UNINSTALLED` via `shopify.processWebhooks` (default wiring, always present) — see `mountWebhooks` in `packages/apps/shopify-app-express/src/webhooks/index.ts`.
2. Attacker installs the app on `attacker-shop.myshopify.com`, then uninstalls it, causing Shopify to send a real `APP_UNINSTALLED` webhook to the app's public webhook URL with a valid `X-Shopify-Hmac-Sha256` for that body.
3. Attacker captures this request (own traffic) and resends it to the same endpoint, changing only the `X-Shopify-Shop-Domain` header to `victim-shop.myshopify.com` and leaving body/HMAC untouched.
4. `validateHmacFromRequestFactory` recomputes HMAC over the (unchanged) body and it matches, so `validateFactory` returns `valid: true, domain: 'victim-shop.myshopify.com', topic: 'APP_UNINSTALLED', ...` per `packages/apps/shopify-api/lib/webhooks/validate.ts`.
5. `callWebhookHandlers` invokes the registered `APP_UNINSTALLED` callback with `webhookCheck.domain` = victim domain (`packages/apps/shopify-api/lib/webhooks/process.ts:146-156`), which calls `AppInstallations.delete('victim-shop.myshopify.com')`, deleting the victim's stored sessions/access tokens without the victim having done anything.

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

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L60-73)
```typescript
    const webhookCheck = await validateFactory(config)({
      rawBody,
      ...adapterArgs,
    });

    let errorMessage = 'Unknown error while handling webhook';
    if (webhookCheck.valid) {
      const handlerResult = await callWebhookHandlers(
        config,
        webhookRegistry,
        webhookCheck,
        rawBody,
        context,
      );
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

**File:** packages/apps/shopify-api/docs/guides/webhooks.md (L63-98)
```markdown
// Handle webhooks
app.post('/webhooks', express.text({type: '*/*'}), async (req, res) => {
  const {valid, topic, domain} = await shopify.webhooks.validate({
    rawBody: req.body, // is a string
    rawRequest: req,
    rawResponse: res,
  });

  if (!valid) {
    console.error('Invalid webhook call, not handling it');
    res.send(400); // Bad Request
  }

  console.log(`Received webhook for ${topic} for shop ${domain}`);

  const sessionId = shopify.session.getOfflineId(domain);

  // Run your webhook-processing code here!
});
```

**OR**, you can pass in a `callback` in your handler configuration, and call `process`:

```ts
const handleWebhookRequest = async (
  topic: string,
  shop: string,
  webhookRequestBody: string,
  webhookId: string,
  apiVersion: string,
  context?: any,
) => {
  const sessionId = shopify.session.getOfflineId(shop);

  // Run your webhook-processing code here!
};
```

**File:** packages/apps/shopify-app-express/src/app-installations.ts (L33-41)
```typescript
  async delete(shopDomain: string): Promise<void> {
    const shopSessions =
      await this.sessionStorage.findSessionsByShop!(shopDomain);
    if (shopSessions.length > 0) {
      await this.sessionStorage.deleteSessions!(
        shopSessions.map((session: Session) => session.id),
      );
    }
  }
```

**File:** packages/apps/shopify-app-express/src/webhooks/index.ts (L37-54)
```typescript
function mountWebhooks(
  api: Shopify,
  config: AppConfigInterface,
  handlers: WebhookHandlersParam,
) {
  api.webhooks.addHandlers(handlers as AddHandlersParams);

  // Add our custom app uninstalled webhook
  const appInstallations = new AppInstallations(config);

  api.webhooks.addHandlers({
    APP_UNINSTALLED: {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: config.webhooks.path,
      callback: deleteAppInstallationHandler(appInstallations, config),
    },
  });
}
```
