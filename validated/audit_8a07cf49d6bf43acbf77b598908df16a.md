## Analysis

The FraxLend bug is a class of vulnerability where a security-critical decision (the solvency check) is made using data that isn't authoritative at the moment of the check — the exchange rate is stale/not refreshed, so the check passes even though the true, current state would have failed it.

The closest unprivileged analog in `shopify-app-js` is in webhook request authentication: the HMAC signature that authenticates a webhook request only covers the **raw body**, but the **shop domain** used to select which tenant's session (and access token) to load for processing that webhook is taken from an **unauthenticated header** that is not bound to the signature at all.

### Title
Webhook `X-Shopify-Shop-Domain` header is not authenticated by the HMAC, allowing cross-tenant session selection - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
`shopify.webhooks.validate` (and the `authenticate.webhook` wrappers in `shopify-app-remix`/`shopify-app-react-router`/`shopify-app-express`) verifies the webhook's authenticity solely by comparing an HMAC computed over the **raw body** against the value in the `X-Shopify-Hmac-Sha256` header. The shop-identifying field (`domain`, from the `X-Shopify-Shop-Domain` header) that is subsequently used to select and load the tenant's session/access token is never covered by that signature, so any request whose body+HMAC pair is valid for the app's shared secret passes validation regardless of which domain header is attached.

### Finding Description
`validateFactory` in [1](#0-0)  validates a webhook purely via `validateHmacFromRequestFactory`, which computes and compares an HMAC over `rawBody` only [2](#0-1) . After the HMAC check succeeds, `checkWebhooksHeaders`/`checkEventsHeaders` simply read the `domain` value straight from the (unauthenticated) `X-Shopify-Shop-Domain` header and return it as trusted data [3](#0-2) .

That `domain` value is then used directly to look up/refresh the *tenant's* offline session and construct an authenticated Admin API client for the webhook handler: [4](#0-3) 

Because the app's `apiSecretKey` is shared across every shop that installs the app (it is not per-shop), a body+HMAC pair that is valid for one shop's webhook delivery is *also* a cryptographically valid pair for any other shop using the same app — the signature never binds the payload to a specific shop domain. This is exactly analogous to FraxLend's `isSolvent` check running against a value (`exchangeRateInfo.exchangeRate`) that was never refreshed/bound to the current true state before being trusted for an authorization decision: here, the "shop identity" used to pick whose credentials/session to use for the operation is never bound to (or verified by) the authenticity check that was actually performed.

### Impact Explanation
A merchant who legitimately installs the app on their own store (an unprivileged, single-merchant actor) can capture a real webhook body/HMAC pair that Shopify delivered to them, then replay that exact body to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header with a **different** shop that also has the app installed. The request passes HMAC validation (the body's HMAC is still valid — same shared secret), so the library loads and returns the victim shop's offline session and access token, and invokes the app's webhook handler with `shop = victim domain`, `session = victim session/admin client`, but `payload = attacker-controlled body`. Depending on the app's webhook handler logic (e.g. `orders/paid`, `app/uninstalled`, `products/update`), this enables cross-tenant state corruption, spoofed business events processed against another merchant's account, or forced use of the victim's Admin API credentials to execute app logic the attacker chose the payload for.

### Likelihood Explanation
Reachable from an anonymous HTTP endpoint (`shopify.authenticate.webhook` / the app's webhook route) requiring only that the attacker itself is a merchant who has installed the app (a single, unprivileged actor) — no leaked secrets or MITM are required, since the attacker legitimately receives a validly signed body for their own store from Shopify and merely reuses it with a forged header value.

### Recommendation
Bind the shop domain (and other identity-critical fields such as topic/webhook id) into the authenticated payload used for the signature check, or independently verify that the `domain` header actually corresponds to the shop that owns the delivered `webhookId`/event (e.g., by cross-checking against Shopify via API, or requiring HMAC verification to also cover the header value). At minimum, do not treat `X-Shopify-Shop-Domain` as authoritative for session/tenant selection without additional binding to the signed body.

### Proof of Concept
1. Attacker installs the target app on `attacker.myshopify.com`.
2. Attacker triggers a webhook event (e.g. updates a product) and captures the raw POST body plus the `X-Shopify-Hmac-Sha256` value Shopify sent to the app's webhook endpoint.
3. Attacker resends the identical body and HMAC header to the same webhook endpoint, but sets `X-Shopify-Shop-Domain: victim.myshopify.com` (a store that also has the app installed).
4. `validateHmacFromRequestFactory` succeeds (HMAC matches body+shared secret) [5](#0-4) ; `checkWebhooksHeaders` returns `domain: 'victim.myshopify.com'` as trusted [6](#0-5) .
5. `authenticateWebhookFactory` loads the victim's offline session via `ensureValidOfflineSession(params, check.domain)` and builds an Admin client from it, then invokes the app's handler with attacker-supplied `payload` [7](#0-6) .

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-102)
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

    return {
      ...webhookContext,
      session,
      admin,
    };
```
