### Title
Forged `X-Shopify-Shop-Domain` header lets a webhook body signed for one shop grant Admin API access as a different (victim) shop - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
Webhook authenticity is verified only over the raw request body's HMAC, computed with the app's single, shop-independent `apiSecretKey`. The shop-identifying `X-Shopify-Shop-Domain` header used afterwards to select which shop's offline session (and therefore Admin API client) to attach to the request is never bound to that HMAC. An attacker who can obtain any one genuinely-signed webhook delivery for the app (trivially available by installing the app on their own store) can resend that exact body+HMAC pair while swapping the domain header to a victim shop, causing the handler to authenticate the request as the victim and expose the victim's session/admin client.

### Finding Description
`validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts` computes the local HMAC purely over `rawBody` with `config.apiSecretKey`, which is identical for every shop that has installed a given app: [1](#0-0) 

`validateFactory` in `packages/apps/shopify-api/lib/webhooks/validate.ts` calls this body-only check, and then separately extracts `domain`, `topic`, `apiVersion`, and `webhookId` straight from request headers with no cryptographic linkage to the body or the HMAC: [2](#0-1) 

Downstream, `authenticateWebhookFactory` (present identically in both `shopify-app-remix` and `shopify-app-react-router`) trusts `check.domain` unconditionally to load the shop's offline session and build an authenticated Admin API client that is handed to the developer's webhook handler: [3](#0-2) [4](#0-3) 

This is structurally the same flaw pattern as the Connext report: a signature check validates that a given artifact (the router signature / the webhook body) is authentic for *some* value the attacker controls (their own router key / their own shop's webhook body), but the system never confirms that the *specific identity* attached to that artifact and used for privileged downstream action (which router gets paid / which shop's session is loaded) is the one the trusted party (sequencer / Shopify) actually intended.

### Impact Explanation
Because the HMAC secret is shared across all shops for a given app, any actor who has installed the app on a shop they control can obtain a validly-signed webhook body (with attacker-influenceable content, e.g. via product/customer/order fields that get echoed into the payload). By replaying that body with a forged `X-Shopify-Shop-Domain` header naming another installed shop, the attacker's request passes `check.valid === true`, and `ensureValidOfflineSession` will load and return the **victim shop's** offline session, attaching a working `admin` GraphQL/REST client scoped to the victim to the webhook context object that is passed into the app's own handler code. Depending on how the app's webhook handler uses `shop`/`session`/`admin`, this can result in cross-tenant data disclosure or cross-tenant mutations performed with the victim's access token, and the attacker-chosen `payload`/`topic` can also be used to trigger business logic meant only for genuine victim-originated events.

### Likelihood Explanation
Exploitation only requires an anonymous HTTP POST to the app's public webhook endpoint; no privileged access, secret leakage, or MITM is needed. The attacker needs: (1) their own installation of the target app to harvest a validly-HMAC'd body, and (2) knowledge of the victim's `myshopify.com` domain, which is often public/guessable. Both are attacker-controlled prerequisites unrelated to compromising Shopify or the target merchant, making this reachable from a single hostile merchant/developer account.

### Recommendation
Bind the claimed shop domain (and other trust-relevant headers such as topic/webhook id) into the same authenticated artifact that is verified, e.g. include the `X-Shopify-Shop-Domain` value in the HMAC computation (or otherwise sign it), or cross-check that the domain header matches an out-of-band verified identity for the delivery (Shopify does support this by validating that the `X-Shopify-Hmac-Sha256` corresponds specifically to a subscription/shop pairing recorded at registration time). Alternatively, do not trust the header-derived `domain` for loading a session/admin client unless the webhook subscription itself was registered by, and can be correlated back to, that exact shop (e.g., store and check the expected shop per registered webhook id).

### Proof of Concept
1. Install the target app on an attacker-controlled shop `attacker.myshopify.com`; trigger a webhook event whose payload fields are attacker-influenceable (e.g. `products/update` with a crafted title/body_html).
2. Capture the resulting POST request, including its raw body and the `X-Shopify-Hmac-Sha256` header Shopify computed with the app's shared secret.
3. Replay the identical body + HMAC header to the app's webhook endpoint, but replace `X-Shopify-Shop-Domain` with `victim.myshopify.com` (a shop known to have installed the app).
4. `validateHmacFromRequestFactory` passes because the body and HMAC still match (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:189-197`).
5. `authenticateWebhookFactory` loads `victim.myshopify.com`'s stored offline session via `ensureValidOfflineSession(params, check.domain)` and returns an `admin` client and `session` scoped to the victim shop to the app's webhook handler (`packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:52-102`), even though the request never actually originated from, nor was authorized by, `victim.myshopify.com`.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L33-52)
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
