This confirms the vulnerability path. The webhook HMAC (`validateHmacFromRequestFactory` in [1](#0-0) ) signs only `rawBody` with the app's single shared `apiSecretKey` — it never binds the `X-Shopify-Shop-Domain` header into the signed data. `checkWebhookHeaders`/`checkWebhooksHeaders` in [2](#0-1)  then simply reads the `domain` header verbatim and returns it as `valid: true` with that trusted-looking `domain` field, without ever checking it is consistent with anything cryptographically verified. Downstream, `authenticateWebhookFactory` uses `check.domain` directly to call `ensureValidOfflineSession(params, check.domain)` ( [3](#0-2) ), which loads and hands back the **admin session/client for whatever shop string is in that header** — exactly the "stored key not checked against the passed identifier" bug class from the report (there it was oracle-account vs. `randomness.oracle`; here it's the `domain` header vs. the actually-signed data).

### Title
Webhook HMAC Does Not Bind Shop Domain, Enabling Cross-Tenant Session Access via Domain Header Substitution - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
`shopify.webhooks.validate` (and the `authenticate.webhook` wrappers in shopify-app-remix / shopify-app-react-router) authenticate a webhook request purely by HMAC-ing the raw body with the app's single, shop-independent `apiSecretKey`. The `X-Shopify-Shop-Domain` header (and other headers) are read out of the request and trusted as-is, with no check that they are cryptographically tied to the same signed payload. Anyone able to produce one validly-HMAC'd `(rawBody, hmac)` pair for their own shop (e.g. a merchant who installed the app and can trigger/observe one of their own webhook deliveries) can replay that exact body+HMAC to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header value with an arbitrary other shop that has the app installed.

### Finding Description
- HMAC validation (`validateHmacFromRequestFactory`) computes `HMAC-SHA256(apiSecretKey, rawBody)` and compares it to the `X-Shopify-Hmac-Sha256` header — it never includes `domain`, `topic`, or `webhook-id` in the signed material. [4](#0-3) 
- `validateFactory` calls this HMAC check, and on success calls `checkWebhookHeaders`, which simply extracts `domain` from headers and returns it verbatim as part of the "valid" result — no cross-check against the signed body or any other verified value. [5](#0-4) 
- `authenticateWebhookFactory` (shopify-app-remix and shopify-app-react-router) takes `check.domain` straight from that untrusted-but-"valid" result and passes it to `ensureValidOfflineSession(params, check.domain)` to fetch the shop's stored offline session and construct an authenticated `admin` GraphQL client for the webhook context. [6](#0-5) 
- Since the `apiSecretKey` is shared across all shops that install the app (it's the app's client secret, not per-shop), a valid `(rawBody, hmac)` pair generated for shop A's webhook remains valid for any `domain` header value B; the API/library never verifies that the domain identifying which tenant's session to load is the domain that was actually included in the signed request from Shopify.

### Impact Explanation
An attacker who can obtain (or generate, by taking an installable/free-trial copy of the app on their own store) one legitimately-signed webhook body+HMAC pair can forge requests to the app's public webhook endpoint that impersonate the delivery for any other shop known to be using the app (shop domains are guessable/enumerable via app listings, custom-domain naming, or leaked from other channels). This yields:
- Cross-tenant session access: the webhook handler resolves and hands application code the victim shop's offline `Session`/`admin` client, letting handler logic act (read/write via GraphQL) in the context of a shop the attacker does not own.
- Data confusion/poisoning: any app logic keyed off `webhookContext.shop`/`session.shop` (e.g., updating shop-specific records, syncing data, billing state) can be triggered for a shop that never actually sent that webhook.

### Likelihood Explanation
Reachable by a single external actor (an app-installing merchant) without needing any secret beyond what they can legitimately obtain from their own installation, and without any privileged position (no MITM required) — they only need to replay their own valid webhook body with a substituted header, which any standard HTTP client can do against the app's public webhook route. The main precondition is that the attacker can capture one valid `(rawBody, hmac)` pair from their own shop's webhook traffic, which is plausible for a merchant/developer who logs or proxies incoming webhooks to their own server.

### Recommendation
Bind the shop domain (and ideally topic/webhook-id) into the value that is cryptographically verified before it's trusted, rather than trusting the raw header independently of the HMAC check. Options:
- Have `checkWebhookHeaders`/`validateFactory` fail closed unless the caller can additionally prove the `domain` corresponds to a shop that has an installed/active session with a scope matching this app (defense in depth), and/or
- Document/enforce that consuming code must never use the webhook `domain` value to authorize cross-tenant actions without an independent verification step, and add such a check in `authenticateWebhookFactory` before calling `ensureValidOfflineSession`.

### Proof of Concept
1. Attacker installs the target app on shop `attacker-shop.myshopify.com`, and via any means (their own server logs) captures a legitimate webhook delivery: `rawBody = B`, header `X-Shopify-Hmac-Sha256 = H` (valid because `H = HMAC-SHA256(apiSecretKey, B)`).
2. Attacker sends `POST /webhooks` to the app with the same `rawBody = B` and `X-Shopify-Hmac-Sha256: H`, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (a shop known to have the app installed) instead of their own domain.
3. `validateHmacFromRequestFactory` recomputes `HMAC-SHA256(apiSecretKey, B)` and it matches `H` — validation succeeds regardless of the domain header. [7](#0-6) 
4. `checkWebhookHeaders` returns `{valid: true, domain: 'victim-shop.myshopify.com', ...}`. [8](#0-7) 
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')`, loading the victim's offline session and constructing an `admin` client for it, which is then handed to the app's webhook handler logic as if the request genuinely originated from the victim shop. [9](#0-8)

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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-134)
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

function getRequiredHeader(
  headers: Headers,
  headerName: string,
  missingHeaders: string[],
): string | undefined {
  const value = getHeader(headers, headerName);
  if (!value) {
    missingHeaders.push(headerName);
  }
  return value;
}

function checkWebhookHeaders(
  headers: Headers,
  webhookType: WebhookTypeValue,
): WebhookValidationMissingHeaders | WebhookValidationValid {
  if (webhookType === WebhookType.Webhooks) {
    return checkWebhooksHeaders(headers);
  }
  return checkEventsHeaders(headers);
}

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L33-96)
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
```
