This is confirmed: `shopify-app-express`'s built-in `APP_UNINSTALLED` handler (`deleteAppInstallationHandler`) calls `appInstallations.delete(shop)` using the `shop` value extracted from the webhook's **`domain` header**, and that header is never covered by the webhook HMAC.

### Title
Cross-tenant DoS via unauthenticated `domain` header on webhook HMAC validation - (File: packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
Shopify webhook authenticity is verified with a single HMAC computed over `rawBody` only, using the app's shared `apiSecretKey`. The `domain`/shop-identifying header is read separately, after HMAC success, and is never included in the HMAC computation. Because the same `apiSecretKey` signs every shop's webhooks for a given app, any actor who can obtain one genuine `(rawBody, hmac)` pair (e.g. by installing the app on a store they control and capturing its `APP_UNINSTALLED` webhook) can replay that exact body+HMAC to the app's public webhook endpoint while substituting an arbitrary victim shop in the `domain` header. `validate()`/`process()` will report `valid: true` for that forged victim shop, and downstream handlers that trust `domain` (e.g. the built-in uninstall handler) execute against the victim's shop.

### Finding Description
`validateHmacFromRequestFactory` computes the HMAC exclusively over `rawBody`: [1](#0-0) 

`validateFactory` runs this HMAC check first, then separately extracts unauthenticated headers (including `domain`) via `checkWebhookHeaders`, with no cross-check that `domain` is bound to the body/HMAC: [2](#0-1) [3](#0-2) 

The `shop`/`domain` value returned from validation is then handed directly to consumers, e.g. `shopify.session.getOfflineId(domain)` in the docs, or into `ensureValidOfflineSession(params, check.domain)` in the remix/react-router adapters: [4](#0-3) 

And in `shopify-app-express`, the built-in `APP_UNINSTALLED` handler directly calls `appInstallations.delete(shop)` using this unauthenticated `shop` value: [5](#0-4) [6](#0-5) 

This mirrors the reported bug class: a validation step's success/failure is computed over data that does not commit to all the mutable fields that downstream logic treats as authenticated, allowing an attacker-controlled non-committed field (here, the `domain` header, analogous to Zcash's block header not covering the mutated `scriptSig`) to be substituted after the check passes and to poison a *different, legitimate* record (a victim shop's install/session state) sharing no cryptographic binding to the originally signed data.

### Impact Explanation
A single-merchant/anonymous attacker who has installed the app on any store (including a free dev store) can capture one genuine `APP_UNINSTALLED` webhook `(rawBody, X-Shopify-Hmac-Sha256)` pair, then POST it to the app's public webhook endpoint with the `X-Shopify-Shop-Domain` header set to a victim's shop domain. This passes `shopify.webhooks.validate()`/`process()` as valid, and the built-in uninstall handler deletes the victim's app-installation record via `appInstallations.delete(shop)`, and equivalent app code that keys off `check.domain`/the webhook `shop` parameter (e.g., calling `sessionStorage.deleteSession`/deleting offline sessions, revoking access, wiping app-specific data) will act on the victim's tenant. This is a cross-tenant Denial-of-Service: it can deauthorize/deregister an arbitrary, uninvolved merchant's app installation without their consent, forcing forced reinstall/re-auth and disrupting background jobs relying on the offline session.

### Likelihood Explanation
High. No secrets need to be leaked and no privileged position is required — the attacker only needs the ability to install the target app on any store they control (trivial for public/dev-store apps) to legitimately receive one valid webhook body+HMAC pair, then replay it with a forged `domain` header at the app's public, unauthenticated `/webhooks` endpoint from anywhere on the internet.

### Recommendation
Bind the shop/domain (and other webhook-identifying headers such as topic and webhook id) into the HMAC input, or otherwise cryptographically verify that the claimed `domain` corresponds to the app installation associated with the received body, before dispatching to handlers that treat `domain`/`shop` as trusted (in `validateHmacFromRequestFactory` / `checkWebhookHeaders` in `packages/apps/shopify-api/lib/webhooks/`). At minimum, downstream consumers (like `deleteAppInstallationHandler` in `shopify-app-express`) should not perform destructive/state-changing actions keyed solely on an unauthenticated header without corroborating it against stored session/installation state for that shop.

### Proof of Concept
1. Install the target app on attacker-controlled store `attacker.myshopify.com`; trigger app uninstall to receive a genuine `APP_UNINSTALLED` webhook POST with a valid `X-Shopify-Hmac-Sha256` header computed over the raw JSON body.
2. Replay that exact raw body and HMAC header to the app's public webhook endpoint (e.g. `/webhooks`), but set `X-Shopify-Shop-Domain: victim.myshopify.com` and `X-Shopify-Topic: app/uninstalled`.
3. `shopify.webhooks.validate()`/`process()` returns `valid: true` (HMAC over the unmodified body passes) with `domain: 'victim.myshopify.com'`.
4. In `shopify-app-express`, `deleteAppInstallationHandler` fires with `shop = 'victim.myshopify.com'` and calls `appInstallations.delete('victim.myshopify.com')`, deregistering the victim's installation despite the app still being installed on their store [5](#0-4) .

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L185-197)
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L35-52)
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

**File:** packages/apps/shopify-app-express/src/webhooks/index.ts (L37-53)
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
```
