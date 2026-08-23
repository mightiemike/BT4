## Title
Webhook signature validation only covers the request body, not the `domain`/`topic`/`webhookId` headers, allowing cross-tenant webhook impersonation - (File: packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
`shopify.webhooks.validate()` verifies a webhook's authenticity by HMAC-signing only the raw request body and comparing it to the `X-Shopify-Hmac-Sha256` header. The shop identity (`X-Shopify-Shop-Domain`), event `topic`, `webhookId`, and `apiVersion` headers are read afterward, unauthenticated, and are trusted as-is. This mirrors the `SwapperCallbackValidation` bug class: the check proves *a* fact ("this body was signed with our app secret") but not the fact the caller actually depends on ("this body belongs to shop X / event Y"), so a validated signature can be replayed against a different claimed identity.

### Finding Description
`validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` computes the local HMAC purely from `rawBody`: [1](#0-0) 

It never folds in `domain`, `topic`, `webhookId`, or `apiVersion`. Once this check passes, `validateFactory` in `packages/apps/shopify-api/lib/webhooks/validate.ts` independently extracts those unauthenticated headers via `checkWebhookHeaders`/`checkWebhooksHeaders` and returns them as trusted fields (`domain`, `topic`, etc.): [2](#0-1) 

Downstream, `authenticateWebhookFactory` (used identically in both `shopify-app-remix` and `shopify-app-react-router`) uses `check.domain` directly to look up and attach an authenticated offline session/admin client, and `check.topic` to route to app logic: [3](#0-2) [4](#0-3) 

Because the signature check never binds `hmac` to `domain`/`topic`/`webhookId`, an attacker who legitimately receives one authentic webhook (e.g., by installing the app on their own store and triggering any webhook, capturing `rawBody` + its valid `X-Shopify-Hmac-Sha256`) can resend the exact same `rawBody`+HMAC pair while freely rewriting `X-Shopify-Shop-Domain` to any other shop that has installed the app, and rewriting `X-Shopify-Topic`/webhook-type headers to any topic they want. `shopify.webhooks.validate()` will still report `valid: true`, `checkWebhookHeaders` returns the attacker-chosen `domain`/`topic`, and `authenticateWebhookFactory` will load the victim shop's real offline session/admin client and invoke the app's handler for the attacker-chosen topic against that victim session.

### Impact Explanation
This is a cross-tenant confused-deputy vulnerability: an unprivileged attacker (any merchant who has installed the app once) can trigger the app's server-side webhook handlers as if the event came from an arbitrary other shop that has the app installed, complete with that victim shop's authenticated offline session/admin API client. Depending on which webhook topics the app registers, this can be abused to trigger destructive handlers (e.g. `app/uninstalled` cleanup, GDPR `shop/redact` data deletion, order/fulfillment mutations) against a shop the attacker does not control, using the victim's own access token via the `admin` client the framework hands to the handler.

### Likelihood Explanation
The prerequisite (installing the app on any shop, including the attacker's own, to obtain one valid signed webhook body) is trivial for any developer/merchant who can install a public app, making this reachable from a single unprivileged actor with no insider access, leaked secrets, or MITM required. The only extra requirement is that a target shop already has a stored offline session, which is the normal state for any installed app.

### Recommendation
Bind the identity headers into the signed material, or otherwise cryptographically tie `domain`/`topic`/`webhookId` to the verified payload before trusting them:
- Verify that `X-Shopify-Shop-Domain` matches the shop context expected for the endpoint (if the route is shop-scoped), and/or
- Include the relevant headers in the HMAC input (this would require protocol-level changes on Shopify's signing side), or at minimum
- Cross-check the `domain` header against an authenticated source of truth (e.g., only accept it if it matches a shop whose current webhook-registration record you independently track, keyed by `webhookId`) rather than trusting attacker-controlled headers outright.

### Proof of Concept
1. Install the vulnerable app on an attacker-controlled shop `attacker.myshopify.io`; trigger any webhook subscription (e.g. `products/create`) and capture the raw POST body and its `X-Shopify-Hmac-Sha256` header value exactly as sent by Shopify.
2. Replay that captured request to the app's webhook endpoint, keeping `rawBody` and the `X-Shopify-Hmac-Sha256` header unchanged, but set:
   - `X-Shopify-Shop-Domain: victim.myshopify.io` (any shop with the app installed)
   - `X-Shopify-Topic: app/uninstalled` (or any topic the app handles)
3. `shopify.webhooks.validate()` returns `{valid: true, domain: 'victim.myshopify.io', topic: 'APP_UNINSTALLED', ...}` because only `rawBody` was checked.
4. `authenticateWebhookFactory` loads the offline session for `victim.myshopify.io` and invokes the app's `APP_UNINSTALLED` handler with an authenticated `admin` client scoped to the victim shop, even though the request never originated from Shopify for that shop/topic.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-66)
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
