### Title
Webhook `X-Shopify-Shop-Domain` header is not covered by HMAC signature, allowing shop-domain spoofing and cross-tenant session/admin-client binding - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
Shopify webhook authenticity is verified purely by computing an HMAC over the raw request body and comparing it to the value in the `X-Shopify-Hmac-Sha256` (or events) header, using the app's shared secret. The `X-Shopify-Shop-Domain` header, which the library later trusts as "the shop this webhook belongs to," is never included in that HMAC computation.

### Finding Description
`validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` computes the signature over `rawBody` only: [1](#0-0) 

`validateFactory` in `packages/apps/shopify-api/lib/webhooks/validate.ts` calls that check and, only *after* it succeeds, extracts the trusted `domain` field straight from the unauthenticated header via `checkWebhooksHeaders`/`checkEventsHeaders`: [2](#0-1) [3](#0-2) 

Downstream, `authenticateWebhookFactory` (shopify-app-remix / shopify-app-react-router) takes `check.domain` at face value and uses it to load the **offline session and construct an authenticated admin/storefront client** for that shop: [4](#0-3) 

Because the app's API secret key is shared across every shop that has installed the app (this is standard OAuth-app design, not shop-specific), any merchant who has installed the app can legitimately receive a real webhook delivery for their own shop with a valid `X-Shopify-Hmac-Sha256` value computed over that body. Since the HMAC only covers the body and not the `X-Shopify-Shop-Domain` header, that merchant can replay the same body/HMAC pair to the app's webhook endpoint while substituting a different shop's domain in the `X-Shopify-Shop-Domain` header. The signature check still passes (body unchanged), `checkWebhooksHeaders` accepts the attacker-supplied domain, and the webhook handler is invoked with `webhookContext.shop` set to the victim shop and an `admin`/`storefront` client authenticated against the victim's stored offline session/access token.

This mirrors the root cause of the referenced report: a value used to identify "who this operation is meant for" (`sender`/`lender` in the report, `shop`/`domain` here) is accepted without being cryptographically bound to the authenticated payload, letting one authorized-but-unprivileged party (a borrower / an installing merchant) substitute another party's identity into a privileged operation (paying on the lender's behalf / acting with another shop's admin credentials).

### Impact Explanation
An attacker who runs a legitimate but malicious app install (any merchant able to receive/replay a real webhook for their own shop) can forge the `shop` context of processed webhooks toward any other shop that has installed the same app, letting webhook handler business logic run with cross-tenant `session`/`admin` client access, and with attacker-controlled payload data, being attributed to a different, unrelated store. This is a cross-tenant confusion / spoofing vulnerability affecting all consumers of `shopify.webhooks.validate` / `shopify.authenticate.webhook` (shopify-api, shopify-app-remix, shopify-app-react-router).

### Likelihood Explanation
The attacker only needs to be able to install the target app on any shop they control (a single unprivileged merchant), capture one legitimate webhook delivery, and replay it with a modified `X-Shopify-Shop-Domain` header toward the app's public webhook endpoint — an anonymous HTTP POST from outside Shopify's IP range is not otherwise restricted by this code path. No secret leakage, MITM, or dependency issue is required.

### Recommendation
Bind the shop identity to the signed payload instead of trusting the `X-Shopify-Shop-Domain` header on its own:
- Verify that `check.domain` matches the shop actually associated with the loaded/looked-up session before constructing an `admin`/`storefront` client, or
- Additionally require that the offline session found for `check.domain` corresponds to a shop whose installation is independently verifiable (e.g., cross-check against the topic's expected resource, or require an app-level allowlist of currently-installed shop domains), or
- Where feasible, incorporate the domain header into the local HMAC comparison, consistent with how Shopify signs Events-API webhooks, so header tampering invalidates the signature.

### Proof of Concept
1. Attacker installs the target Shopify app on `attacker-shop.myshopify.com`, obtaining valid API-secret-signed webhook deliveries for that shop (e.g., `orders/create`).
2. Attacker captures one such delivery: raw body `B` and header `X-Shopify-Hmac-Sha256: H` where `H = HMAC_SHA256(apiSecretKey, B)`.
3. Attacker resends `POST /webhooks` with the same body `B` and header `H`, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
4. `validateHmacFromRequestFactory` verifies `H` against `B` successfully (headers are irrelevant to the HMAC) — see [1](#0-0) .
5. `checkWebhooksHeaders` extracts `domain = 'victim-shop.myshopify.com'` unchecked — see [5](#0-4) .
6. `authenticateWebhookFactory` loads the offline session for `victim-shop.myshopify.com` and builds an authenticated `admin` client for it, then invokes the webhook handler with attacker-controlled payload `B` and `shop: victim-shop.myshopify.com` — see [6](#0-5) .

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L40-102)
```typescript
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
