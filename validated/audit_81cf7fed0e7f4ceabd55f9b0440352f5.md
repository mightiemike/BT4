### Title
Webhook HMAC does not bind the `X-Shopify-Shop-Domain` (or other identity) headers, allowing cross-tenant webhook forgery - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The webhook signature check in `validateHmacFromRequestFactory` verifies the HMAC only over the raw request body, using the app's single shared `apiSecretKey`. It never authenticates the `X-Shopify-Shop-Domain` header (or any other identity header) that downstream code (`authenticateWebhookFactory` in `shopify-app-remix`/`shopify-app-react-router`) trusts to select which shop's offline session/access token to load. This mirrors the `CompoundToNotionalV2.notionalCallback` bug class: an identity value (`sender`/`account` there, `domain` here) that the authorization/session-loading logic relies on is attacker-controllable and is not actually covered by the cryptographic check that is supposed to authenticate it.

### Finding Description
`validateHmacFromRequestFactory` computes and checks the HMAC exclusively over `rawBody`: [1](#0-0) 

The webhook header values, including the shop domain, are only checked for *presence*, never for authenticity, in `checkWebhooksHeaders`/`checkEventsHeaders`: [2](#0-1) 

Because the `apiSecretKey` is the same shared app secret across every shop that installs the app, an attacker who legitimately installs the target app on their own (attacker-controlled) shop will receive genuine webhooks with valid `(rawBody, X-Shopify-Hmac-Sha256)` pairs signed with that shared secret. No timestamp or nonce binds the body to a particular delivery, and the HMAC never covers `X-Shopify-Shop-Domain`, `X-Shopify-Topic`, or `X-Shopify-Webhook-Id`. The attacker can therefore replay that valid `(rawBody, hmac)` pair while swapping the `X-Shopify-Shop-Domain` header to any victim shop domain that also has the app installed, and the signature will still validate.

Downstream, `authenticateWebhookFactory` trusts `check.domain` (taken straight from the unauthenticated header) to look up and attach the victim's offline session/admin client to the webhook context: [3](#0-2) [4](#0-3) 

This is the same root cause as the H-04 analog: the value used for the authorization decision (`sender`/`account` in the Solidity bug, `domain` here) is attacker-supplied and not actually validated by the mechanism (`require(sender == address(this))` there, HMAC signature here) that is assumed to bind it.

### Impact Explanation
An attacker who installs the target app on their own shop can forge webhook deliveries "from" any other shop that has the same app installed, and the app's webhook handler will treat it as authentic and attach that victim shop's admin session/access token. Concretely:
- Replaying a genuine `app/uninstalled` webhook (which the attacker can trigger on their own shop) with the `X-Shopify-Shop-Domain` header set to a victim shop causes typical `afterAuth`/uninstall handlers to delete the victim's stored session/access token — a cross-tenant denial of service that forces the legitimate merchant to reinstall/re-authenticate.
- More generally, any webhook topic body the attacker can generate on their own shop can be replayed against any victim shop domain, running the app's business logic (data writes, admin API calls) using the victim's authenticated `admin` client, and exposing per-shop webhook context (`shop`, `session`, `admin`) to an unauthenticated/cross-tenant forged request.
- Since the HMAC check has no timestamp/nonce binding, this can be repeated indefinitely once one valid `(rawBody, hmac)` pair is obtained.

### Likelihood Explanation
Any single actor who can install the app on a shop they control (a normal, low-privilege action available to any merchant/developer) can obtain a genuinely signed webhook body+HMAC and then trivially recompute an HTTP request with an altered `X-Shopify-Shop-Domain` header. No secret needs to be leaked or guessed; the shared app secret is used as designed, just not bound to the field that authorization logic relies on.

### Recommendation
Do not trust the shop identity carried in webhook headers as authenticated by the body HMAC. Either:
- Include the shop domain (and topic/webhook-id) in the HMAC-signed material, or
- Cross-check the domain against Shopify's known/expected registered shop for that specific webhook subscription (e.g., match against the shop that was known to register this specific `webhookId`/topic combination) rather than trusting the raw header value directly, and
- Add replay protection (timestamp/nonce, and cache/dedupe by `webhookId`) to webhook processing so that a previously delivered `(rawBody, hmac)` pair cannot be reused with altered headers or at an arbitrary later time.

### Proof of Concept
1. Attacker installs the target Shopify app on their own store `attacker-shop.myshopify.com`.
2. Attacker triggers (or waits for) a real webhook delivery from Shopify for their own shop, e.g. uninstalls the app to receive a genuine `app/uninstalled` webhook with a valid `X-Shopify-Hmac-Sha256` header computed over the raw body using the shared `apiSecretKey`.
3. Attacker replays the exact same `rawBody` and `X-Shopify-Hmac-Sha256` value in a new POST request to the app's webhook endpoint, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (a shop known to have the app installed).
4. `validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-201`) validates because it only checks `rawBody` against the HMAC, not the domain header.
5. `checkWebhooksHeaders` (`packages/apps/shopify-api/lib/webhooks/validate.ts:99-134`) accepts because all required headers are present, with `domain: 'victim-shop.myshopify.com'`.
6. `authenticateWebhookFactory` (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:35-102`) calls `ensureValidOfflineSession(params, 'victim-shop.myshopify.com')`, loads the victim's offline session, and the app's `app/uninstalled` handler (or any other topic handler) executes against the victim shop's session — e.g., deleting the victim's stored access token, causing a cross-tenant denial of service.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-59)
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L88-102)
```typescript
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
