### Title
Webhook `admin` client context can be bound to an attacker-chosen shop via the unauthenticated `X-Shopify-Shop-Domain` header - ([File: packages/apps/shopify-app-remix/src/server/helpers/create-or-load-offline-session.ts])

### Summary
For merchant-custom (`AppDistribution.ShopifyAdmin`) apps, the webhook authentication pipeline derives the `shop` used to build the `admin` API client from the `X-Shopify-Shop-Domain` request header (`check.domain`), which is **not** covered by Shopify's webhook HMAC signature. This mirrors the Llama root cause: a value that should only ever be set at a trusted point (during verified authentication) is instead taken from unauthenticated/unprivileged input and used later to make a security-relevant decision (which shop the returned `admin` client is scoped to).

### Finding Description
Shopify webhook validation only verifies the HMAC over the **raw request body** — the `shop`/topic/webhook-id headers are never included in the signed content: [1](#0-0) 

`checkWebhooksHeaders` simply extracts `domain` from the `X-Shopify-Shop-Domain` header with no cryptographic binding to the body or HMAC: [2](#0-1) 

The webhook authenticate handler then passes this unauthenticated `check.domain` straight into `ensureValidOfflineSession`: [3](#0-2) 

For a merchant-custom/single-tenant app (`AppDistribution.ShopifyAdmin`), `createOrLoadOfflineSession` does not look up a stored, previously-authenticated session tied to that shop — it fabricates a brand-new `Session` object directly from the caller-supplied `shop` string via `api.session.customAppSession(shop)`, with no validation that this shop matches the app's actual installed/configured shop: [4](#0-3) [5](#0-4) 

Because only the *body* is HMAC-signed, an attacker who is able to obtain any single valid `(rawBody, hmac)` pair signed by Shopify (e.g. from a webhook Shopify actually sent to the app for some shop, or one triggered on a store the attacker controls) can replay that exact body+HMAC to the app's webhook endpoint while freely substituting the `X-Shopify-Shop-Domain` header. `validateFactory`/`checkWebhooksHeaders` will accept it as valid since the HMAC check never inspects the domain header, and the resulting `webhookContext.session`/`admin` client will be built and scoped using the attacker-chosen domain string instead of the shop the request actually came from.

### Impact Explanation
For merchant-custom apps, this lets an unprivileged actor who possesses one legitimate (body, HMAC) pair cause the webhook handler to build and hand application code an `admin` API client/session object whose `shop` field is attacker-controlled, rather than the value tied to the actually-authenticated request. Depending on how the app's webhook handler uses `session.shop` / the `admin` client (e.g., for authorization checks, tenant-scoping decisions, logging, or constructing further outbound API calls), this can lead to incorrect tenant/shop context being trusted — a cross-tenant/session-confusion condition analogous to the Llama bug where an unprivileged caller mutated a value relied upon for a downstream security decision.

### Likelihood Explanation
Moderate/low: it is scoped specifically to apps using `AppDistribution.ShopifyAdmin` (merchant-custom apps) and requires the attacker to already possess one valid HMAC-signed webhook body (which is feasible if the attacker controls a store where the app is installed, or otherwise obtains one such payload). Standard multi-tenant/App-Store distributed apps are less exposed because `createOrLoadOfflineSession` for other distributions loads a stored session by ID rather than fabricating one from the header value directly — though the underlying header-vs-HMAC-body mismatch pattern is present for every distribution.

### Recommendation
- Do not derive shop/tenant context for admin API client construction solely from unauthenticated request headers (`X-Shopify-Shop-Domain`); cross-check it against a securely stored, previously verified record (e.g., an existing offline session, an allow-list of installed shops, or a shop value embedded in and covered by an authenticated channel).
- For `AppDistribution.ShopifyAdmin`, validate that the incoming `domain` header matches the single configured/expected shop for the app before calling `customAppSession`, rather than trusting the header unconditionally.
- Consider including the shop domain in the HMAC-covered content (or otherwise cryptographically binding header claims to the signed body) to prevent header substitution replay of legitimately-signed payloads.

### Proof of Concept
1. Attacker installs the app (or otherwise triggers) on Shop A (or any store) and captures one legitimate webhook delivery: `rawBody` + valid `X-Shopify-Hmac-Sha256` header signed by Shopify with the app's shared secret.
2. Attacker replays this exact `rawBody` and `hmac` to the app's webhook endpoint, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (any value they like) and adjusts other non-signed headers (`topic`, `webhook-id`) as needed for the target handler.
3. `validateFactory` → `validateHmacFromRequestFactory` validates the HMAC solely against `rawBody`, which still matches, so `checkWebhooksHeaders` returns `valid: true` with `domain: 'victim-shop.myshopify.com'` taken from the attacker-supplied header [2](#0-1) .
4. `authenticateWebhookFactory` calls `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')` [6](#0-5) , which — for `AppDistribution.ShopifyAdmin` — returns `api.session.customAppSession('victim-shop.myshopify.com')` without verifying this shop is the one actually configured/installed [7](#0-6) .
5. The webhook handler receives `session`/`admin` scoped to the attacker-chosen shop string instead of the true request origin.

**Note:** I could not fully trace how the fixed custom-app access token from `config.adminApiAccessToken`/`ConfigInterface` is threaded into the GraphQL/REST client for a `customAppSession` at runtime (i.e., whether the attacker-controlled `shop` value ends up as the actual HTTP request hostname for authenticated Admin API calls, which would elevate this into an SSRF/credential-misuse issue). This would need to be verified in `packages/apps/shopify-app-remix/src/server/clients/admin/factory.ts` and `packages/apps/shopify-api/lib/clients/admin/*` before treating the impact as more severe than shop/tenant-context confusion in the webhook handler itself.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-96)
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
```

**File:** packages/apps/shopify-app-remix/src/server/helpers/create-or-load-offline-session.ts (L1-19)
```typescript
import {AppDistribution, BasicParams} from '../types';

export async function createOrLoadOfflineSession(
  {api, config, logger}: BasicParams,
  shop: string,
) {
  if (config.distribution === AppDistribution.ShopifyAdmin) {
    logger.debug('Creating custom app session from configured access token', {
      shop,
    });
    return api.session.customAppSession(shop);
  } else {
    logger.debug('Loading offline session from session storage', {shop});
    const offlineSessionId = api.session.getOfflineId(shop);
    const session = await config.sessionStorage!.loadSession(offlineSessionId);

    return session;
  }
}
```

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L86-94)
```typescript
export function customAppSession(config: ConfigInterface) {
  return (shop: string): Session => {
    return new Session({
      id: '',
      shop: `${sanitizeShop(config)(shop, true)}`,
      state: '',
      isOnline: false,
    });
  };
```
