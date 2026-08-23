Confirmed: the `domain` (and `topic`/`webhookId`) header values are taken verbatim from HTTP headers via `getRequiredHeader` and returned in the `valid: true` result, and are entirely independent of the HMAC verification, which is computed only over `rawBody` and the shared `config.apiSecretKey`. [1](#0-0) [2](#0-1) 

### Title
Webhook HMAC does not bind shop domain/topic/webhook-id headers, allowing forged `X-Shopify-Shop-Domain` to trigger cross-tenant session deletion - (File: packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts)

### Summary
This is the closest reachable analog to the RocketStorage ACL bug: a single "authorization" check (the HMAC signature) is treated as authoritative for the whole request, but it does not actually cover the field (`shop`/domain namespace) that downstream code trusts to select which tenant's data to mutate. Just as RocketStorage's `onlyLatestRocketNetworkContract` modifier authorizes a contract to write *any* key regardless of namespace, the webhook validator authorizes a request to act as *any* shop, because the domain header isn't part of what's signed.

### Finding Description
`validateFactory`/`validateHmacFromRequestFactory` compute and check the webhook HMAC using only `rawBody` and the app's single `apiSecretKey` — the same secret is used for HMAC validation across *all* shops that install the app. [3](#0-2)  After the HMAC check passes, `checkWebhooksHeaders`/`checkEventsHeaders` simply read the `X-Shopify-Topic`, `X-Shopify-Shop-Domain`, `X-Shopify-Webhook-Id` headers directly and return them as trusted (`valid: true`) fields without any relationship to the signed body. [1](#0-0) 

Because the HMAC is a pure function of `(secret, body)`, any two webhook deliveries with the *same body content* (e.g. `APP_UNINSTALLED`, which is typically delivered with an empty JSON body `{}` for every shop) will have an *identical valid HMAC value*, regardless of which shop triggered it. A merchant who has legitimately installed the app on their own shop will receive a genuine, validly-signed `APP_UNINSTALLED` webhook (or can otherwise obtain a valid `(body, hmac)` pair for a predictable/empty-body topic). They can then replay that exact body+HMAC to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header with a victim shop's domain string, and the `X-Shopify-Topic`/`X-Shopify-Webhook-Id` headers as desired.

The `process()` pipeline calls `validateFactory` then dispatches to the registered handler using the (attacker-controlled) `domain`/`shop` value. [4](#0-3)  The Express package's built-in `APP_UNINSTALLED` handler, `deleteAppInstallationHandler`, is wired via `mountWebhooks`/`processWebhooks` for every app using this package, [5](#0-4)  and unconditionally calls `appInstallations.delete(shop)` using that unauthenticated `shop` string. [6](#0-5)  `AppInstallations.delete` finds and deletes **all** sessions for that shop domain via `findSessionsByShop`/`deleteSessions`. [7](#0-6) 

This mirrors the RocketStorage flaw exactly: the "permission" check (HMAC) is coarse-grained and app-wide rather than scoped to the specific namespace (shop) being acted upon, so a party legitimately authorized for one namespace (their own shop) can act on any other namespace by forging the header that carries the namespace identity.

### Impact Explanation
A malicious merchant (an "unprivileged" actor from the app's perspective — they only ever had legitimate access to their own shop's install/uninstall events) can force any other shop using the app to have its offline/online sessions wiped from storage, without actually uninstalling the app or performing any privileged action against the victim. This forces the victim's app instance into a re-authentication state (a denial-of-service against the auth/session layer) and can be repeated at will. If the app maintainer relies on `AppInstallations.includes()` for licensing/billing gating logic, this can also be used to falsely mark a paying shop as uninstalled.

### Likelihood Explanation
Requires only: (1) the attacker be able to install the target app on any shop they control (freely available for public apps) to legitimately receive one valid `(body, hmac)` webhook pair with a predictable/empty body (e.g., `APP_UNINSTALLED`), and (2) send a raw HTTP POST to the target app's webhook endpoint with the captured body/HMAC and a forged `X-Shopify-Shop-Domain` header. No secret knowledge, session forgery, or MITM is needed — it is a direct unauthenticated HTTP replay against the app's own public webhook endpoint.

### Recommendation
Bind the trust-sensitive headers (`X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-Hmac-Sha256` computed context) into the authenticated data, or otherwise verify that the claimed `domain` is consistent with a known/expected value before trusting it (e.g., cross-check against the session/shop that is expected to be receiving this webhook route, reject topics whose payload should not be empty when body is empty, or require per-shop webhook secrets/verification of shop identity via a separate authenticated channel). At minimum, `deleteAppInstallationHandler` and any handler that mutates data keyed by the header-derived `shop` should not treat that value as fully trusted without additional corroboration.

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com` and lets the app auto-register `APP_UNINSTALLED` (default empty-body webhook).
2. Attacker uninstalls the app from `attacker-shop.myshopify.com`, capturing the real webhook HTTP request Shopify sends, including body `{}` and header `X-Shopify-Hmac-Sha256: <valid-hmac-for-empty-body>`.
3. Attacker replays this exact request to the same app's webhook endpoint, changing only `X-Shopify-Shop-Domain` to `victim-shop.myshopify.com` (and updating `X-Shopify-Webhook-Id` to avoid idempotent dedup, if any).
4. `validateFactory` computes `HMAC(secret, "{}")`, matches the captured value (since HMAC is app-wide, not shop-specific), and returns `valid: true` with `domain: "victim-shop.myshopify.com"`. [8](#0-7) 
5. `deleteAppInstallationHandler` is invoked with `shop = "victim-shop.myshopify.com"` and deletes all of the victim's stored sessions. [6](#0-5)

### Citations

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L56-73)
```typescript
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

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L153-200)
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

export function getCurrentTimeInSec() {
  return Math.trunc(Date.now() / 1000);
}

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

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L43-73)
```typescript
export function process(
  config: ConfigInterface,
  webhookRegistry: WebhookRegistry<HttpWebhookHandlerWithCallback>,
) {
  return async function process({
    context,
    rawBody,
    ...adapterArgs
  }: WebhookProcessParams): Promise<AdapterResponse> {
    const response: NormalizedResponse = {
      statusCode: StatusCode.Ok,
      statusText: STATUS_TEXT_LOOKUP[StatusCode.Ok],
      headers: {},
    };

    await logger(config).info('Receiving webhook request');

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
