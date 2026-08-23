[1](#0-0) 

### Title
Webhook HMAC does not cover the shop-domain header, allowing cross-tenant session hijack via forged `X-Shopify-Shop-Domain` - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
Shopify webhook authenticity is verified purely by computing an HMAC-SHA256 over the raw request body using the app's shared `apiSecretKey`, while the shop-identifying header (`X-Shopify-Shop-Domain`) is read separately and never included in the signed material. Because the secret is shared across every shop that has the app installed, an attacker who legitimately controls one shop on the app can obtain a genuinely-signed `(rawBody, hmac)` pair (e.g. by triggering a webhook on their own store) and then replay it to the app's webhook endpoint with the `domain` header rewritten to point at a victim shop. The signature still validates because it never bound the domain to the payload, so the app framework loads the victim shop's offline session/access token and hands it (plus the attacker-controlled body) to the merchant's webhook handler.

### Finding Description
`validateFactory` in [2](#0-1)  validates a webhook purely by calling `validateHmacFromRequestFactory`, which computes `createSHA256HMAC(config.apiSecretKey, rawBody, ...)` and compares it against the `X-Shopify-Hmac-Sha256` header, as seen in [3](#0-2) . Notice that only `rawBody` participates in the HMAC computation — no headers, including the shop/domain header, are included in the signed data.

After the HMAC check passes, `checkWebhooksHeaders`/`checkEventsHeaders` simply pull the `domain` value straight out of the (unauthenticated) HTTP headers and return it as trusted output: [4](#0-3) .

The webhook authenticate handler then uses this attacker-controllable `check.domain` value directly to look up and load the offline session (and therefore the shop's access token) for that domain: [5](#0-4) 

Because the secret key is per-app (shared by all installed shops) rather than per-shop, any merchant who has the app installed can generate a fully valid `(rawBody, hmac)` pair for their own shop (via a real webhook delivery or the Shopify CLI webhook trigger), then resend that exact body/HMAC to the app's public webhook endpoint while substituting the `X-Shopify-Shop-Domain` (and related domain) header for a different, victim shop that also has the app installed. The signature check only re-derives the HMAC from the body and secret — it has no way to detect that the domain header doesn't match the shop that actually produced the signature — so the request is accepted as "valid" for the victim shop.

This mirrors the Oracle bug's root cause: an unprivileged caller can supply data (here, the `domain` header) that the trusted code path uses to select which tenant's credentials/target to act upon, without that data being cryptographically bound to the portion of the request that was actually authenticated.

### Impact Explanation
A successful attack lets a single (potentially free-trial) merchant who has installed the target Shopify app cause the app's webhook handler to execute merchant-supplied webhook payload data in the authenticated context of a different shop's offline session — i.e., using another tenant's access token via `adminClientFactory` built from the mis-attributed session ( [6](#0-5) ). Depending on what the app's webhook handlers do with the payload (e.g. update orders, fulfill, write metafields, issue refunds), this is a cross-tenant confidentiality/integrity break and can lead to unauthorized actions being performed against a victim merchant's store using their own access token.

### Likelihood Explanation
Exploitability requires only: (1) attacker owns/controls a shop that has the app installed (or can trigger a webhook for any shop, e.g. via CLI webhook trigger which uses the same app secret), and (2) knowledge of the victim's shop domain, which is not secret. No leaked credentials, no MITM, and no privileged access to Shopify's infrastructure are needed — this is reachable purely by crafting an anonymous HTTP POST to the app's public webhook endpoint with a replayed body/HMAC and a forged domain header.

### Recommendation
Bind the shop domain (and other security-relevant identifiers) into the signed payload verification, or otherwise cryptographically tie the `hmac` to the `domain` header — for example by verifying that the session/access token retrieved for `check.domain` was itself established through a prior OAuth/token-exchange flow tied to a request whose HMAC covered that domain, or by additionally validating the domain against Shopify's webhook topic subscription records rather than trusting the header value outright. At minimum, apps should not implicitly trust the header-derived `domain` for session lookup without an out-of-band binding to the signed body (e.g., including shop in the webhook payload itself and cross-checking it against the header).

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com`.
2. Attacker triggers (or waits for) any real webhook delivery to their app endpoint; captures the raw POST body and its `X-Shopify-Hmac-Sha256` header value — both are valid because they were signed with the app's real secret.
3. Attacker resends this exact `(rawBody, hmac)` pair to the app's public webhook route, but sets `X-Shopify-Shop-Domain` (and any other domain-carrying header the app expects) to `victim-shop.myshopify.com`.
4. `validateHmacFromRequestFactory` recomputes the HMAC solely from `rawBody` and the shared secret — validation succeeds ( [7](#0-6) ).
5. `checkWebhooksHeaders` returns `domain: 'victim-shop.myshopify.com'` as trusted ( [4](#0-3) ).
6. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, check.domain)` and builds an `admin` client from the victim's session ( [8](#0-7) ), causing the merchant's webhook handler to run attacker-controlled payload data with victim-shop credentials.

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
