This confirms the root-cause pattern: `validateHmacFromRequestFactory` computes the HMAC exclusively over `rawBody` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts` lines 168-201, specifically `validateHmacString(config, rawBody, hmac, HashFormat.Base64)`), while `checkWebhookHeaders`/`checkWebhooksHeaders` in `packages/apps/shopify-api/lib/webhooks/validate.ts` (lines 89-146) pulls `domain`, `topic`, `apiVersion`, and `webhookId` straight from unauthenticated HTTP headers and stamps `valid: true` on them without ever comparing them against anything the HMAC actually covers. That `domain` value then flows unchecked into `ensureValidOfflineSession(params, check.domain)` in both `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts` and the react-router equivalent (lines 14-105 in each), which loads a session/admin client for whatever shop the header claims to be — this is exactly the "leak of value" pattern from the report: a value (`sellBasePreview`'s `returned`) is treated as validated input to a security-relevant operation while nothing actually constrains it, making the check a no-op for the value that matters. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Webhook HMAC only signs the raw body, letting the `X-Shopify-Shop-Domain` header select an arbitrary tenant session - (File: packages/apps/shopify-api/lib/webhooks/validate.ts)

### Summary
The webhook signature check in `shopify.webhooks.validate` verifies the HMAC solely against `rawBody`. The `domain`, `topic`, `apiVersion`, and `webhookId` fields returned as "valid" come from HTTP headers that are never included in the HMAC computation, so their authenticity is never actually verified. The `domain` value is nonetheless trusted downstream to select which merchant's offline session/admin client the webhook handler operates on.

### Finding Description
`validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-201`) computes and checks the HMAC using only `rawBody`:
```ts
const validHmac = await validateHmacString(config, rawBody, hmac, HashFormat.Base64);
```
Once that passes, `validateFactory` calls `checkWebhookHeaders` (`packages/apps/shopify-api/lib/webhooks/validate.ts:73,89-146`), which reads `domain`, `topic`, `apiVersion`, `webhookId` etc. directly from headers via `getRequiredHeader`, and returns them tagged `valid: true` — there is no cryptographic binding between these header values and the HMAC-signed body. Because the app's `apiSecretKey` used for HMAC is a single shared secret across all shops that install the app (not shop-specific), any actor who legitimately receives one valid webhook for their own shop (with a known `rawBody`/HMAC pair, since they can trigger events on their own store) can replay that exact body+HMAC while swapping the `X-Shopify-Shop-Domain` (or events equivalent) header to any other shop domain. The HMAC check still succeeds (it only checks the body), and `checkWebhookHeaders` happily returns the attacker-chosen `domain`.

That forged `domain` then flows straight into `ensureValidOfflineSession(params, check.domain)` in `authenticateWebhookFactory` (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:52` and the identical `shopify-app-react-router` copy), which loads the *victim* shop's stored offline session and constructs an authenticated `admin` client bound to that session (`adminClientFactory({params, session, ...})`, lines 92-102). Any custom webhook handler logic keyed off `shop`/`session`/`admin` in that context is executed as if it were a legitimate webhook from the victim shop.

### Impact Explanation
This allows a single malicious merchant (who has installed the app, or can otherwise obtain one signed webhook body/HMAC pair, e.g. via a low-value/predictable webhook payload) to make forged webhook requests attributed to a different, victim tenant. The forged request obtains a `WebhookContext` containing the victim's `session` and an `admin` GraphQL/REST client bound to the victim's access token, enabling cross-tenant data access/actions through any webhook handler that trusts `shop`/`session`/`admin` from the context. This is a direct cross-tenant / forged-request vulnerability against a multi-tenant app.

### Likelihood Explanation
Exploitability requires only a single unprivileged actor (any merchant who installs the app) capable of triggering at least one real webhook to capture a valid `rawBody` + HMAC pair (many webhook bodies are static/predictable, e.g. `app/uninstalled` payloads or minimal JSON bodies), then replaying it over HTTP to the app's public webhook endpoint with a different `X-Shopify-Shop-Domain` header. No secrets need to be leaked and no MITM is required — it is a pure unprivileged forgery against the authentication handler itself.

### Recommendation
Bind the header-derived identity fields (`domain`, `topic`, `apiVersion`, `webhookId`) to the HMAC verification, e.g., include them in the signed payload comparison, or independently verify `domain` against Shopify (or a previously stored, trusted mapping) before using it to select a session. At minimum, cross-check that the `domain` header matches a shop that is expected/allow-listed for the specific `hmac` presented, and treat any mismatch as invalid rather than trusting header values wholesale once the body-only HMAC succeeds.

### Proof of Concept
1. Malicious actor installs the app on `attacker-shop.myshopify.com` and triggers/receives one webhook, capturing `rawBody` (e.g., `{"id":123}`) and its valid `X-Shopify-Hmac-Sha256` header.
2. Actor sends `POST /webhooks` to the app with the same `rawBody` and `X-Shopify-Hmac-Sha256`, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`, `X-Shopify-Topic`, `X-Shopify-Webhook-Id`, and `X-Shopify-API-Version` to values matching a topic the app handles.
3. `shopify.webhooks.validate`/`authenticateWebhookFactory` accepts the HMAC (body-only check passes) and returns `check.domain === 'victim-shop.myshopify.com'`.
4. `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')` loads the victim's stored session; the webhook handler executes with `session`/`admin` scoped to the victim shop.

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-52)
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
