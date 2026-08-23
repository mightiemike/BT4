Confirmed: the HMAC in `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-201`) is computed solely over `rawBody` using the header named by `hmacHeaderName` — no other header (topic, domain, webhookId, api-version) is included in the signed data. `validateFactory` in `packages/apps/shopify-api/lib/webhooks/validate.ts:46-75` calls this HMAC check first, and only *after* HMAC succeeds does it call `checkWebhookHeaders` (`checkWebhooksHeaders`/`checkEventsHeaders`, lines 89-204) which extracts `domain`, `topic`, `webhookId`, etc. straight from the **unauthenticated** request headers. This `domain` value is then handed to `ensureValidOfflineSession(params, check.domain)` in both `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:52` and `packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts:52`, which loads that shop's stored offline session/access token and attaches an authenticated `admin` client to the handler context. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Title
Webhook shop-domain header is unauthenticated by HMAC, enabling replay against arbitrary shops - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
The webhook HMAC verification in this library binds the signature only to the raw request body, never to the `X-Shopify-Shop-Domain` (or `X-Shopify-Hmac-Sha256`-adjacent) headers. Because `domain`/`topic`/`webhookId` are read from headers only *after* HMAC validation passes, and are trusted as-is to select which shop's session/access token is loaded, this is structurally the same class of bug as the front-run `RFPSimpleStrategy` report: a value (`proposalBid` there, `domain` header here) that is *not* covered by the authorization check is used later to determine payout/authorization, letting an attacker substitute their own value between "verify" and "use."

### Finding Description
`validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-201`) computes the local HMAC exclusively from `rawBody` and compares it to the incoming HMAC header value. None of the other Shopify webhook headers (`X-Shopify-Shop-Domain`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, `X-Shopify-API-Version`) are part of the signed material.

`validateFactory` (`packages/apps/shopify-api/lib/webhooks/validate.ts:46-75`) runs the HMAC check first, and only on success proceeds to `checkWebhookHeaders`, which pulls `domain`, `topic`, and `webhookId` directly out of the (unauthenticated) headers and returns them as trusted fields (`checkWebhooksHeaders`, lines 99-146; `checkEventsHeaders`, lines 148-204).

Downstream, `authenticateWebhookFactory` in both the Remix and React Router adapters takes `check.domain` from this untrusted header and passes it straight into `ensureValidOfflineSession(params, check.domain)` to fetch that shop's offline session/access token, then builds an authenticated `admin` API client bound to it for the webhook handler.

Because a merchant/attacker who has legitimately installed the app on their own shop can capture one authentic, correctly-HMAC-signed webhook delivery from Shopify (body + valid HMAC header), the signature remains valid no matter what they change the `X-Shopify-Shop-Domain` header to when replaying that exact request to the app's webhook endpoint. The HMAC check only re-validates the untouched body against the untouched HMAC header — it says nothing about which shop the request should be attributed to.

### Impact Explanation
By replaying a self-obtained, validly-signed webhook body while swapping the shop-domain header to a victim's `myshopify.com` domain, an attacker can cause the app to load the victim shop's offline `Session` (containing its access token) and construct an authenticated `admin` client scoped to the victim, inside the developer's own webhook handler code. Any app that uses `check.domain`/`session.shop` inside its webhook callback to read/write shop-specific data (which is the standard intended usage pattern shown in the library's own docs) is exposed to cross-tenant data access or mutation using another merchant's access token — a direct violation of tenant isolation.

### Likelihood Explanation
Exploitation requires only: (1) the attacker's own store to have the app installed (trivial — any developer/malicious merchant can install a public app), (2) capturing one legitimate webhook delivery (easy — webhooks fire constantly on ordinary store activity, or the attacker can trigger one), and (3) sending an HTTP POST with a modified header to the app's public webhook URL, which needs no additional secret. No mempool timing or race condition is required (unlike the original front-running report) — this is a straightforward unprivileged, deterministic replay, making it at least as easy to exploit as the original H-5 finding.

### Recommendation
Do not trust header-derived `domain`/`topic`/`webhookId` values for authorization decisions unless they are cryptographically bound to the signed payload. Either (a) include the shop domain (and other identifying headers) in the HMAC computation so any tampering invalidates the signature, or (b) cross-check the header-derived `domain` against a shop identifier embedded in the verified JSON body (e.g. compare against the payload's own shop-scoped fields) before using it to select which stored session/access token to load, rejecting the request if they disagree.

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.com` and captures one legitimate webhook POST (e.g. `orders/create`), including its exact raw body and `X-Shopify-Hmac-Sha256` header value — both valid per `packages/apps/shopify-api/lib/utils/hmac-validator.ts`.
2. Attacker resends the identical body and identical HMAC header to the app's webhook endpoint, but sets `X-Shopify-Shop-Domain: victim.myshopify.com` (and, if desired, a different `X-Shopify-Webhook-Id`).
3. `validateHmacFromRequestFactory` recomputes the HMAC over the unchanged body and it matches, so `validHmacResult.valid === true` (`packages/apps/shopify-api/lib/webhooks/validate.ts:56-71`).
4. `checkWebhooksHeaders` returns `domain: 'victim.myshopify.com'` straight from the attacker-controlled header (`packages/apps/shopify-api/lib/webhooks/validate.ts:99-134`).
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim.myshopify.com')`, loading the victim's stored offline access token and handing the app's webhook handler an authenticated `admin` client for the victim shop (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:33-65`).

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-197)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L33-65)
```typescript
    const rawBody = await request.text();

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
```
