### Title
Webhook shop identity (`X-Shopify-Shop-Domain`) is not covered by the HMAC signature, allowing cross-tenant session/admin-API access via header spoofing - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
The Shopify webhook HMAC verification only authenticates the raw request body, never the identifying headers (shop domain, topic, webhook id, api version). The library then trusts the unauthenticated `X-Shopify-Shop-Domain` header as the "sender" identity used to load that shop's offline session and construct an authenticated Admin API client for processing the webhook payload. This mirrors the ZetaChain finding: the cctx's `Sender` field was populated from an unverified, attacker-influenceable value (`tx.origin`/`emittingContract`) rather than from the value that was actually cryptographically authenticated, and that unverified value was later used to grant/target a privileged, sensitive operation (the refund recipient). Here, the header-derived `domain` plays the same role as the mis-attributed `Sender`: it is not bound to the HMAC that only covers `rawBody`, yet it is used to select which shop's session and Admin API credentials the webhook handler operates with.

### Finding Description
`validateFactory` in [1](#0-0)  performs HMAC validation strictly over `rawBody`: [2](#0-1) 

Only after the body-only HMAC check passes does the code extract identity/routing fields — critically the `domain` (shop) — directly from request headers with no cryptographic tie to the HMAC: [3](#0-2) 

That unauthenticated `domain` value is then passed straight through as `check.domain` to `ensureValidOfflineSession(params, check.domain)` in the webhook authentication flow, which loads the offline session/access token for that shop and builds an Admin API client used to fulfill the webhook handler's business logic: [4](#0-3) [5](#0-4) 

Because the app's `apiSecretKey` used to compute the webhook HMAC is shared across every shop that installs the app (it is not shop-specific), any merchant who has legitimately installed the app can obtain a validly-HMAC'd `(rawBody, hmac)` pair for their own store's events (e.g. by triggering `products/update` on their own shop and capturing the delivery, or via a proxy/logging layer they control in front of their own endpoint). Because the domain header is never bound to that signature, the same `(rawBody, hmac)` pair remains valid when replayed with the `X-Shopify-Shop-Domain` header rewritten to a victim shop. The library will report `valid: true` with `domain` equal to the attacker-chosen victim shop, load the victim's offline session, and execute the webhook business logic (and any resulting Admin API mutations) using the victim's credentials while still allowing the attacker to control the JSON contents of `rawBody` (subject only to it matching a schema they already had signed for themselves).

### Impact Explanation
This is a cross-tenant authentication/authorization confusion: a value that is not cryptographically bound to the HMAC (the shop domain header) is used to select which tenant's stored access token and Admin API session get used for processing a webhook payload whose content is effectively attacker-influenced. Depending on the app's webhook handlers (order/product/customer mutation, GDPR data requests, billing, fulfillment), this can result in unauthorized data access or mutation on a victim's store using the victim's own access token — directly matching the "cross-tenant access" acceptance criterion.

### Likelihood Explanation
Exploitation requires only that the attacker be a legitimate (even free/trial) app installer capable of triggering events on their own shop and capturing/replaying the resulting webhook delivery with a modified `X-Shopify-Shop-Domain` header pointed at any other shop id/domain the attacker wants to target — no leaked secrets, MITM of Shopify's own channel, or privileged access is required, since the shared `apiSecretKey` legitimately signs the body for any of the app's installs. The main precondition is the attacker's ability to intercept/replay their own outbound webhook request to the app's public endpoint, which is fully within their control as the receiving merchant of that webhook.

### Recommendation
Bind the shop identity used for session lookup to data that is actually covered by the HMAC, e.g. include the shop domain (and topic/webhook id) inside the signed payload/HMAC computation, or cross-verify the header-provided `domain` against a shop value embedded in and covered by the signed body before using it to load a session. At minimum, additionally verify that the resolved session's shop matches an independently-authenticated shop identifier (such as one obtained via the Admin GraphQL `shop` query using the loaded token) before trusting the webhook as belonging to that shop.

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.io`.
2. Attacker triggers an event (e.g., updates a product) causing Shopify to send a legitimately HMAC-signed webhook (`rawBody`, `X-Shopify-Hmac-Sha256`) to the app's webhook endpoint; attacker captures this request via a proxy they control in front of their own endpoint (or via any means allowing them to see their own inbound request).
3. Attacker resends the same `rawBody`/HMAC pair to the app's webhook endpoint, replacing `X-Shopify-Shop-Domain` with `victim.myshopify.io`.
4. `validateFactory` in `validate.ts` validates the HMAC (which only checks `rawBody`), succeeds, and returns `domain: 'victim.myshopify.io'` as extracted from the header — no HMAC coverage.
5. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim.myshopify.io')`, loading the victim's stored offline session/access token and handing it (plus an Admin client authenticated as the victim) to the app's webhook handler, which then executes using the attacker-supplied `rawBody` content.

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
