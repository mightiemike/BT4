### Title
Webhook `X-Shopify-Shop-Domain` header is not cryptographically bound to the HMAC, allowing cross-tenant webhook domain spoofing - ([File: packages/apps/shopify-api/lib/webhooks/validate.ts])

### Summary
Shopify webhook validation in this library authenticates only the raw request body against `apiSecretKey` via HMAC; the `domain` (shop) header returned to callers as the authoritative shop identifier is read directly from an unauthenticated header and is never included in the HMAC computation or cross-checked against the signed payload.

### Finding Description
`validateFactory` computes HMAC validity purely from `rawBody` and the app's single, app-wide `apiSecretKey`: [1](#0-0) 

The shop domain is then pulled from the `X-Shopify-Shop-Domain` header with no relationship to the HMAC-verified body: [2](#0-1) 

Because `apiSecretKey` is a single, app-wide secret shared across every shop that has the app installed (not per-tenant), any `(rawBody, hmac)` pair that is valid for one shop's webhook delivery is *also* a valid HMAC pair irrespective of which shop domain header accompanies it — the domain header is not part of the signed data. This is directly analogous to the reported bug class: a critical trust decision (which "pool"/tenant to act on behalf of) is made using an unverified input (the pool address / the domain header) that is never authenticated against the source of truth.

The unauthenticated `check.domain` value is then used directly to look up/mint an offline session and construct an authenticated admin client for the webhook handler: [3](#0-2) [4](#0-3) 

The identical pattern exists in the React Router adapter: [5](#0-4) 

### Impact Explanation
An attacker who legitimately installs the app on their own shop (an unprivileged, single-merchant actor) can capture a genuine `(rawBody, X-Shopify-Hmac-Sha256)` pair delivered by Shopify for one of their own webhook events (e.g., `app/uninstalled`, or any topic whose payload doesn't need to vary), then replay that exact body+HMAC to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header with a victim shop's domain. Since HMAC verification only checks `rawBody` against the shared secret, the forged request passes validation and the webhook handler executes with `shop = victim-shop.myshopify.com`, loading/minting the victim's offline session and invoking business logic (including `afterAuth`/session storage side effects) attributed to the victim tenant. Depending on the topic and the app's webhook handlers, this enables denial-of-service or state-corruption against a shop the attacker never installed on (e.g., forcing uninstall cleanup logic, revoking data, or triggering session invalidation for another tenant) — a cross-tenant impact without needing the app secret or MITM access.

### Likelihood Explanation
Exploitation requires only: (1) installing the target app on an attacker-controlled shop (standard, unprivileged action), (2) capturing one legitimate webhook delivery from Shopify to observe a valid `(body, hmac)` pair, and (3) replaying the POST to the app's public webhook endpoint with a swapped domain header. No secret leakage, MITM, or privileged access is required, making this practically reachable from an anonymous/unprivileged actor's perspective relative to the victim tenant.

### Recommendation
1. Do not treat the `X-Shopify-Shop-Domain` (or event `domain`) header as trusted solely because the body HMAC validates. Either bind the domain to the HMAC input (e.g., include it in the signed payload/verification context), or cross-verify the domain against Shopify by an independent authenticated call before acting on it for session lookup/creation.
2. When looking up or creating sessions in `ensureValidOfflineSession`/`createOrLoadOfflineSession`, avoid blindly trusting the webhook `domain` field for mutating operations without an additional integrity check (e.g., verifying the webhook against Shopify's webhook signing keys per topic/shop if available, or rate-limiting/monitoring for repeated domain/body mismatches).
3. Consider deduplicating/replay-protecting webhooks using `X-Shopify-Webhook-Id` plus a freshness/timestamp check so a captured `(body, hmac)` pair cannot be reused indefinitely or with a different domain header.

### Proof of Concept
1. Install the target app on attacker-owned shop `attacker.myshopify.com`.
2. Trigger a webhook (e.g., `app/uninstalled`) and capture the raw POST body and its `X-Shopify-Hmac-Sha256` header from Shopify's real delivery to the app's endpoint.
3. Replay this exact `(body, hmac)` pair to the same endpoint, replacing `X-Shopify-Shop-Domain` with `victim-shop.myshopify.com`.
4. `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:189-197`) validates successfully because it only checks `rawBody` against `apiSecretKey`.
5. `checkWebhookHeaders` returns `domain: 'victim-shop.myshopify.com'` unchecked (`packages/apps/shopify-api/lib/webhooks/validate.ts:99-134`), and the webhook authenticate handler loads/creates an offline session and executes handler logic for `victim-shop.myshopify.com` (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:52`), even though the victim never sent this webhook.

Note: I could not fully trace whether every downstream `afterAuth`/webhook business-logic handler in this repo performs destructive/state-changing actions purely from the `shop` value without additional verification — that depends on app-specific handler code outside this library's scope, so the concrete blast radius (beyond session creation/lookup) is bounded by how consuming apps implement their webhook callbacks.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L52-65)
```typescript
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

**File:** packages/apps/shopify-app-remix/src/server/helpers/ensure-valid-offline-session.ts (L1-14)
```typescript
import {BasicParams} from '../types';

import {createOrLoadOfflineSession} from './create-or-load-offline-session';
import {ensureOfflineTokenIsNotExpired} from './ensure-offline-token-is-not-expired';

export async function ensureValidOfflineSession(
  params: BasicParams,
  shop: string,
) {
  const session = await createOrLoadOfflineSession(params, shop);

  if (!session) return undefined;

  return ensureOfflineTokenIsNotExpired(session, params, shop);
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L35-65)
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
```
