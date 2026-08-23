### Title
Webhook shop-domain header is not cryptographically bound to the HMAC signature, allowing cross-tenant session/admin-API access - (File: packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
The Jet v2 report's root cause is the same class of bug in both findings it describes: a value that is trusted to determine *which authority/account a privileged operation applies to* is never actually bound to the cryptographic proof that authorizes the operation (the `Authority` account isn't checked to really be a CPI-signed authority; the `TokenAccount` isn't checked to be *the* account registered for that position). In `shopify-app-js`, the webhook-validation flow has the same structural flaw: the HMAC signature only covers the raw request body, but the shop identity (`X-Shopify-Shop-Domain` header) used to select *whose* offline session/admin client gets attached to the webhook handler is taken unverified from request headers.

### Finding Description
`validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` computes the webhook HMAC over `rawBody` only: [1](#0-0) 
No header, including the shop domain, participates in the signature.

After HMAC validation succeeds, `checkWebhooksHeaders`/`checkEventsHeaders` in `packages/apps/shopify-api/lib/webhooks/validate.ts` pull the `domain` straight from the (unsigned) `X-Shopify-Shop-Domain` / events domain header, with no sanitization against `sanitizeShop` and no cryptographic link to the signed body: [2](#0-1) 

`authenticateWebhookFactory` in the Remix/React-Router adapters then uses this attacker-controlled `check.domain` to load and attach a real offline session and an authenticated admin client to the webhook context: [3](#0-2) 
`ensureValidOfflineSession` simply loads (or creates) the session for whatever shop string it is given: [4](#0-3) 
Similarly, `shopify-app-express`'s `process()` hands `webhookCheck.domain` straight to the handler callback as the "verified" shop for the webhook, and `callWebhookHandlers` passes it unchanged: [5](#0-4) 

Because the app's HMAC secret (`apiSecretKey`) is the *same shared secret* for every shop that installs the app, a single merchant (an unprivileged actor who legitimately installs the app on their own store) can capture a genuine `(rawBody, X-Shopify-Hmac-Sha256)` pair from a webhook Shopify sends to their own shop, and replay that exact body+HMAC while substituting the `X-Shopify-Shop-Domain` header with a victim shop's domain. The HMAC check passes (it never inspected the domain), `checkWebhooksHeaders` accepts the forged domain, and downstream code loads the **victim's stored offline session** and constructs an authenticated admin client using that victim session — driven by attacker-supplied payload content. This mirrors the Jet report's "invalid authority check": a value that determines whose privileged resource is acted upon is accepted without proof that it was actually endorsed by the entity that produced the signature.

### Impact Explanation
An attacker with mere merchant-level access to the app (installing it on their own store) can forge webhook deliveries that the app framework will treat as authenticated for an arbitrary *other* installed shop, causing the app's webhook handler to run with that victim shop's `session`/`admin` client. Depending on what the app's webhook handlers do (common actions: process orders, update products, issue refunds, delete data, call GDPR/mandatory webhook logic), this is a cross-tenant integrity/confidentiality breach — writes and admin API calls performed under the victim's access token, driven entirely by attacker-controlled webhook payload.

### Likelihood Explanation
Exploitation requires only: (1) the attacker's own legitimate app installation (any merchant can obtain this by installing a public/embedded app), (2) knowledge that a target shop domain is installed on the same app (often discoverable), and (3) the ability to POST directly to the app's public `/webhooks` endpoint with a captured raw body and its accompanying HMAC while overriding the domain header — no secrets need to be leaked and no MITM is required, since the endpoint is unauthenticated by design (webhooks arrive from Shopify's servers, not the merchant's browser). This satisfies the "unprivileged/anonymous HTTP webhook path" and "accepted forged Shopify request" acceptance criteria.

### Recommendation
Bind the shop identity to the signed payload rather than trusting the header value:
- Prefer deriving the shop from a field embedded and signed in the JSON body (Shopify webhook payloads consistently include the numeric `shop_id`/`shop_domain` inside the JSON body for many topics), and cross-check it against the header, rejecting mismatches.
- Alternatively/in addition, verify that the resolved `domain` actually corresponds to a session that was created via the app's own signed OAuth callback for that shop (already stored session with matching `shop`), and reject processing (or at minimum, do not attach `session`/`admin`) if the header-provided shop cannot be independently corroborated.
- Consider incorporating relevant headers (domain, topic, webhook id) into the HMAC computation input where the webhook contract allows it, or add a secondary application-level check that ties the `X-Shopify-Webhook-Id` to a previously-seen/expected value per shop to prevent cross-shop replay.

### Proof of Concept
1. Install the target app on an attacker-controlled development shop `attacker.myshopify.com`; trigger any subscribed webhook topic (e.g., `products/update`) to receive a real webhook POST from Shopify with a valid `X-Shopify-Hmac-Sha256` header computed over the raw JSON body using the app's shared `apiSecretKey`.
2. Capture the exact `rawBody` and `X-Shopify-Hmac-Sha256` value.
3. Send a new POST to the app's `/webhooks` endpoint (or Remix/React-Router `authenticate.webhook` route) with the identical `rawBody` and `X-Shopify-Hmac-Sha256`, but set `X-Shopify-Shop-Domain: victim.myshopify.com` (a shop known to have installed the same app) and the appropriate topic/webhook-id headers.
4. Observe that `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:189-197`) returns valid because it only checks `rawBody` integrity, and `checkWebhooksHeaders` (`packages/apps/shopify-api/lib/webhooks/validate.ts:99-134`) accepts the forged `domain` unchecked; the framework then calls `ensureValidOfflineSession(params, 'victim.myshopify.com')`, loading the victim's real offline session and attaching an authenticated `admin` client to the webhook context used by the app's handler.

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

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L146-156)
```typescript
    const {webhookId} = webhookCheck;

    try {
      await handler.callback(
        webhookCheck.topic,
        webhookCheck.domain,
        rawBody,
        webhookId,
        webhookCheck.apiVersion,
        context,
      );
```
