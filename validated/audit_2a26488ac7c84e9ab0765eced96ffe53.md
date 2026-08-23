This is a valid analog. The reported bug class — a permissionless function crediting/attributing an action to the wrong actor because the actual submitter's identity is never cryptographically verified — maps directly onto how Shopify webhook validation in this codebase separates HMAC verification (which only covers the raw body) from the shop/topic attribution (which is read from unsigned headers).

### Title
Webhook HMAC only signs the request body, allowing shop-domain/topic header forgery to misattribute webhooks to arbitrary tenants - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
`shopify.webhooks.validate()` verifies a webhook's authenticity solely by re-computing an HMAC over `rawBody` and comparing it against the `X-Shopify-Hmac-Sha256` (or events equivalent) header. All other identifying fields returned to the caller — `domain`, `topic`, `webhookId`, `apiVersion` — are pulled directly from unsigned HTTP headers and are never included in the HMAC computation, so nothing cryptographically binds a validated body to the shop or topic the app subsequently trusts.

### Finding Description
`validateHmacFromRequestFactory` in [1](#0-0)  computes `validateHmacString(config, rawBody, hmac, HashFormat.Base64)`, i.e. the signature only covers `rawBody`. Once this passes, `checkWebhooksHeaders`/`checkEventsHeaders` in [2](#0-1)  extract `domain`, `topic`, `webhookId`, and `apiVersion` straight from request headers with no further cryptographic check tying them to the signed body.

Because the app's `apiSecretKey` is shared across every shop that installs the app, any merchant who has installed the app can legitimately trigger a webhook whose body they fully or partially control (e.g. a `PRODUCTS_CREATE`/`ORDERS_CREATE` payload containing attacker-chosen text), obtaining a genuinely valid `(rawBody, hmac)` pair signed with the app's real secret. Since the app's webhook endpoint is a public HTTP endpoint, that attacker can then POST directly to it (bypassing Shopify's relay entirely) using the legitimately-signed body/HMAC pair, but with an arbitrary `X-Shopify-Shop-Domain` header pointing at a victim shop and/or an arbitrary `X-Shopify-Topic` header.

Downstream consumers trust these unsigned fields as if they were verified: `authenticateWebhookFactory` in both the Remix and React Router packages calls `ensureValidOfflineSession(params, check.domain)` — [3](#0-2)  — to load the offline session/access token for whatever shop is named in the header, then hands the merchant's attacker-controlled body to the handler along with an authenticated Admin client for the victim shop.

### Impact Explanation
An attacker (a single merchant who has installed the app on their own store — no elevated privilege required) can make the app believe a webhook came from a different tenant. This can result in:
- Cross-tenant confusion: victim's webhook handler runs with the victim's `admin` client/offline access token against attacker-chosen `payload`/`topic`, potentially triggering business logic (order processing, inventory updates, GDPR-style deletion flows, etc.) for the wrong shop.
- If handler logic uses `topic`/`payload` to make authorization or state decisions (e.g., `APP_UNINSTALLED` cleanup, billing state changes) tied only to `domain`, the attacker can spoof these against any shop domain string they choose, since `domain` is taken verbatim from `check.domain` with no ownership check against the caller.

### Likelihood Explanation
Medium/context-dependent. The attacker needs only to be an installed merchant of the app (unprivileged, self-serve) to mint a legitimately HMAC-signed body, and needs no compromise of the app secret. The main barrier is producing a webhook body that is both validly triggerable and useful for the target handler's logic; some topics have attacker-influenceable content (product/order fields), and even a generic/empty-content topic (like `APP_UNINSTALLED`) can be replayed with a forged domain to disrupt a victim shop's state if handlers act on `topic`+`domain` alone.

### Recommendation
Do not treat unsigned headers (`domain`, `topic`, `webhookId`, `apiVersion`) as trusted identifiers on their own. Where possible, cross-check that the shop the webhook claims to be from actually has an installed session/access-token record consistent with request provenance, rate-limit/dedupe by `webhookId` per shop, and document clearly that consumers must not use `domain`/`topic` values for authorization decisions without additional verification (e.g., confirming the request originated from Shopify's known infrastructure, not just relying on HMAC-over-body).

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com`.
2. Attacker triggers a real webhook (e.g., `PRODUCTS_CREATE`) with a body they control, capturing the resulting `rawBody` and its `X-Shopify-Hmac-Sha256` value (both signed with the app's real, shared `apiSecretKey`).
3. Attacker sends a POST directly to the app's public webhook endpoint (e.g., `/webhooks`) with:
   - Body: the captured `rawBody`
   - Header `X-Shopify-Hmac-Sha256`: the captured valid HMAC
   - Header `X-Shopify-Shop-Domain`: `victim-shop.myshopify.com` (forged)
   - Header `X-Shopify-Topic`: any topic name (forged)
4. `validateHmacFromRequestFactory` only checks HMAC(rawBody) — [4](#0-3)  — and succeeds because the body/HMAC pair is genuinely valid.
5. `checkWebhooksHeaders` returns `domain: 'victim-shop.myshopify.com'` from the forged header — [5](#0-4) .
6. `authenticateWebhookFactory` loads the victim's offline session via `ensureValidOfflineSession(params, check.domain)` and invokes the handler with the victim's `admin` client and the attacker's payload — [6](#0-5) .

### Citations

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
