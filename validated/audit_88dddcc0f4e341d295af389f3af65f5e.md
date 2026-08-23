This confirms the finding: the webhook HMAC (`X-Shopify-Hmac-Sha256`) is computed only over the raw body using a single app-wide shared secret, and the domain header (`X-Shopify-Shop-Domain`) is never part of the signed payload, so it is trusted independently. This is validated by the code in `validateHmacFromRequestFactory` and `checkWebhooksHeaders`/`checkEventsHeaders`.

### Title
Webhook signature does not bind to shop domain, allowing cross-tenant webhook replay - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The webhook HMAC verification in `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts`) only signs and verifies the raw request body against a single, app-wide `apiSecretKey`. The shop identity is taken from the separate `X-Shopify-Shop-Domain` header (`ShopifyHeader.Domain` / `shopify-shop-domain`), which is **not included in the HMAC input**. As a result, a valid `(rawBody, hmac)` pair obtained from a legitimate webhook delivery for one shop can be replayed against the app's webhook endpoint with a forged domain header naming a different shop, and the signature check will still pass.

### Finding Description
`validateHmacFromRequestFactory` computes the local HMAC purely from `rawBody`: [1](#0-0) 
No shop domain, topic, or webhook id is folded into the signed material. The domain, topic, and webhook id are extracted afterward, purely from headers, in `checkWebhooksHeaders`/`checkEventsHeaders`: [2](#0-1) 

Downstream, `authenticateWebhookFactory` (identical in `shopify-app-remix` and `shopify-app-react-router`) trusts `check.domain` from these headers to look up and attach a live, authenticated offline session for that shop, including a ready-to-use Admin API client: [3](#0-2) 

`ensureValidOfflineSession` loads (or refreshes) the offline session purely from the shop string, with no cross-check against the signed body content: [4](#0-3) 

This is directly analogous to the reported bug class: the "signature" (HMAC) does not bind to the specific "chain"/"contract" — here, the specific shop domain and webhook topic/id — so a signature that is valid for one context (one shop's webhook delivery) can be replayed in a different context (a different shop's domain header) with the same validation code accepting it.

### Impact Explanation
Because `apiSecretKey` is shared across all shops that install a given public app, any merchant who installs the app on their own store legitimately receives real webhook deliveries with valid `(rawBody, hmac)` pairs. That merchant/attacker can capture such a pair and POST it to the app's `/webhooks` endpoint again, but with the `X-Shopify-Shop-Domain` (or `shopify-shop-domain`) header rewritten to name a victim shop that also has the app installed. `shopify.webhooks.validate`/`process` will report `valid: true` because only the body/hmac match is checked, and `check.domain` will be the attacker-chosen victim shop. The webhook-handling framework code then loads the victim's real offline session/access token and hands the app's webhook handler an authenticated `admin` API client for the victim shop, together with attacker-controlled `payload` data (originally the attacker's own webhook body). Any app business logic that acts on the webhook payload using the provided `admin` client (e.g., syncing orders/products, issuing refunds, creating discounts, updating metafields) can be tricked into performing those actions against the victim's store, or the app's own data store can be corrupted with attacker data mislabeled as belonging to the victim shop — a cross-tenant confusion/compromise reachable by any single merchant with app access and no special privileges.

### Likelihood Explanation
Any merchant that can install the target public app (a very low bar — self-service Shopify App Store installs, or a hostile "custom app" collaborator) can trivially harvest one or more legitimate `(rawBody, hmac)` webhook pairs simply by receiving normal webhook traffic for their own store, then replay them against the shared webhook endpoint with a forged `X-Shopify-Shop-Domain` header. No secrets need to be leaked and no cryptographic break is required — the HMAC's domain of validity is broader than intended because the shop identity was never part of the signed data. The main constraint is that the victim shop must also have an active offline session/installation of the same app, which is common for any multi-tenant public app.

### Recommendation
Bind the webhook signature verification to the specific tenant/context, analogous to including `chainId`/`address(this)` in the reported smart-contract bug: include the `X-Shopify-Shop-Domain` (and ideally topic/webhook id) as part of the data that is authenticated, or independently verify that the domain header is consistent with a value tied to the request (e.g., re-derive/authenticate shop identity via a shop-specific secret, or bind the HMAC check to a canonical string of `domain + topic + webhookId + body` rather than `body` alone). Shorter-term, downstream consumers (`authenticateWebhookFactory`) should not implicitly trust the header-derived `domain` for loading and exposing a live Admin API session without an additional binding check that ties the verified signature to that specific shop.

### Proof of Concept
1. Install the target public app on an attacker-controlled shop `attacker.myshopify.com`; the app registers a webhook subscription (e.g. `PRODUCTS_CREATE`).
2. Trigger the event on the attacker's shop (e.g., create a product) so Shopify sends a legitimate webhook POST to the app's `/webhooks` endpoint with headers `X-Shopify-Hmac-Sha256: <hmac>` and `X-Shopify-Shop-Domain: attacker.myshopify.com`, and body `rawBody`.
3. Capture `rawBody` and the corresponding `hmac`.
4. Replay a new POST to the same `/webhooks` endpoint with the identical `rawBody` and `hmac`, but set `X-Shopify-Shop-Domain: victim.myshopify.com` (a shop where the app is also installed).
5. `shopify.webhooks.validate`/`process` returns `valid: true, domain: 'victim.myshopify.com'` because HMAC verification in `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts` lines 189-197) never inspected the domain header.
6. `authenticateWebhookFactory` loads the victim's real offline session via `ensureValidOfflineSession(params, check.domain)` and returns an authenticated `admin` client plus the attacker's payload data as if it came from the victim shop, which the app's webhook handler will process under the victim's identity.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L52-96)
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

**File:** packages/apps/shopify-app-remix/src/server/helpers/ensure-valid-offline-session.ts (L1-15)
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
}
```
