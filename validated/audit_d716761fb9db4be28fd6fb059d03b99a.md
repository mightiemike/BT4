### Title
Cross-tenant webhook confusion via unauthenticated `X-Shopify-Shop-Domain` header not covered by HMAC signature - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`)

### Summary
Webhook authenticity in this library is established solely by validating an HMAC over the raw request body against the app's shared secret. The `X-Shopify-Shop-Domain` header, which the library trusts as the tenant identity (`domain`/`shop`) for looking up the offline session and dispatching the webhook, is read directly from request headers and is never included in the HMAC computation.

### Finding Description
`validateHmacFromRequestFactory` computes/validates the HMAC using only `rawBody` and the header-provided HMAC value: [1](#0-0) 

`checkWebhooksHeaders`/`checkEventsHeaders` then read the `domain` (shop identity) straight from the `X-Shopify-Shop-Domain` header without any cryptographic binding to the signed body: [2](#0-1) 

That unauthenticated `domain` value is passed downstream as the trusted tenant identifier to load the app's offline session/access token and to invoke the shop-specific handler callback, e.g.: [3](#0-2) [4](#0-3) 

This mirrors the `VaultRouter` bug class: the code authenticates *that a request came from someone holding the shared secret* (analogous to "the redeem command is valid"), but never authenticates *which identity ("owner"/tenant) the privileged operation should be attributed to* — that identity is taken from an untrusted, unsigned field instead of being derived from the verified/signed material.

### Impact Explanation
Because the app's client secret (`apiSecretKey`) is shared across all shops that install the app, any single merchant that has installed the app (an "unprivileged" actor from the app's perspective, i.e. a legitimate but low-trust caller) can capture a validly-HMAC-signed webhook body from their own store and resend it to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header rewritten to a victim shop that also has the app installed. Since `domain` is not part of the signed content, the HMAC check still passes. The webhook is then processed as if it originated from the victim shop: the app looks up and uses the victim's offline session (`ensureValidOfflineSession(params, check.domain)`), and dispatches handler callbacks keyed to the victim's shop and topic (e.g., `APP_UNINSTALLED`, billing, or other lifecycle topics), potentially causing the victim's session/state to be invalidated, mutated, or acted upon using attacker-chosen (but validly-signed-elsewhere) body content — a cross-tenant confusion / DoS against a specific shop's session and webhook-driven business logic.

### Likelihood Explanation
Exploitability requires only that the attacker be able to install the app on their own store (a normal, unprivileged action) and can trigger or capture at least one legitimately signed webhook for a topic they control, then replay it against the endpoint with a modified domain header. No secrets need to be leaked and no MITM is required, satisfying the "cross-tenant access" acceptance criterion. However, the practical impact is bounded by what handler logic does with `check.domain`/`shop` (most handlers just delete/refresh offline sessions or run idempotent side effects), so severity is moderate and highly handler-dependent — this limits confidence that it rises above informational/low without concrete high-impact handler code in a specific downstream app, which is outside this library.

### Recommendation
Do not trust `X-Shopify-Shop-Domain` (or any header) as tenant identity unless it is cryptographically bound to the signed payload. Options:
- Include the shop domain (and other identity-bearing headers) in the HMAC-signed content, or
- Cross-check the header-provided `domain` against a shop identifier embedded in the JSON body of the webhook payload (many webhook topics include the shop or resource ownership data) before using it to key session lookups or dispatch business logic, or
- At minimum, document this trust boundary prominently and require consuming apps to independently corroborate `shop`/`domain` from body content for any privileged or destructive webhook-driven operation.

### Proof of Concept
1. Install the target app on `attacker.myshopify.com`.
2. Trigger a webhook topic the app subscribes to (e.g., `APP_UNINSTALLED` or any registered topic) so Shopify sends a request to the app's webhook endpoint with a valid `X-Shopify-Hmac-Sha256` computed over the raw body using the shared `client_secret`.
3. Capture the raw body and HMAC header from that request.
4. Replay the exact same body + HMAC header to the same webhook endpoint, but replace `X-Shopify-Shop-Domain: attacker.myshopify.com` with `X-Shopify-Shop-Domain: victim.myshopify.com` (a shop that also installed the app).
5. `validateHmacFromRequestFactory` validates successfully because the HMAC only covers `rawBody`, which is unchanged (see `packages/apps/shopify-api/lib/utils/hmac-validator.ts` lines 189-197).
6. `checkWebhooksHeaders` accepts the forged `domain` (`packages/apps/shopify-api/lib/webhooks/validate.ts` lines 99-134), and downstream code (`ensureValidOfflineSession(params, check.domain)` / `handler.callback(topic, webhookCheck.domain, ...)`) operates using the victim shop's identity/session rather than the attacker's.

Note: I was unable to fully trace every consuming app's webhook handler implementation to confirm a maximally destructive downstream action for every topic (that logic lives in app-specific handler callbacks outside this library), so the concrete severity ceiling (e.g., forced session deletion vs. more damaging state mutation) could not be exhaustively verified within the indexed code.

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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L52-59)
```typescript
    const session = await ensureValidOfflineSession(params, check.domain);

    let webhookContext: WebhookContextWithoutSession<Topics>;

    if (check.webhookType === WebhookType.Webhooks) {
      webhookContext = {
        apiVersion: check.apiVersion,
        shop: check.domain,
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
