### Title
Webhook shop-domain header is unauthenticated (HMAC covers only rawBody), allowing cross-tenant webhook attribution and admin session loading for an arbitrary shop - (File: packages/apps/shopify-api/lib/webhooks/process.ts)

### Summary
`validateHmacFromRequestFactory` in `hmac-validator.ts` computes the webhook HMAC over `rawBody` only, and `checkWebhooksHeaders` in `validate.ts` reads the `X-Shopify-Shop-Domain` header without any sanitization or cross-check against the signed content. As a result, `process.ts`'s `callWebhookHandlers` passes an attacker-controlled `domain` value into `handler.callback`, and downstream consumers such as `authenticateWebhookFactory` in `shopify-app-remix` use that same unverified domain to load and return a legitimate offline session/admin client for that shop.

### Finding Description
`validateHmacFromRequestFactory` (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:168-201`) validates the `X-Shopify-Hmac-Sha256` header strictly against `rawBody` via `validateHmacString`/`safeCompare`, never touching any other header. `checkWebhooksHeaders` (`packages/apps/shopify-api/lib/webhooks/validate.ts:99-146`) separately pulls `domain` straight from the `X-Shopify-Shop-Domain` header via `getRequiredHeader`/`getHeader`, with no `sanitizeShop` call and no binding to the HMAC. `validateFactory` (`validate.ts:46-75`) only calls `checkWebhookHeaders` after HMAC succeeds, but success only proves the body wasn't tampered with — not that the domain header is genuine.

In `process.ts`, `callWebhookHandlers` (`packages/apps/shopify-api/lib/webhooks/process.ts:99-171`) forwards `webhookCheck.domain` verbatim into `handler.callback(topic, domain, rawBody, webhookId, apiVersion, context)` at lines 149-156. Downstream, `authenticateWebhookFactory` in `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts:35-102` takes `check.domain` from this same unverified header and calls `ensureValidOfflineSession(params, check.domain)`, which loads the stored offline session for that shop (`createOrLoadOfflineSession` → `config.sessionStorage.loadSession(...)`) and constructs an authenticated `admin` client for it if found. The `webhookContext.shop` field also reflects the forged domain.

An attacker who owns one dev/test shop can legitimately trigger a webhook to obtain a valid `(rawBody, X-Shopify-Hmac-Sha256)` pair signed with the real app secret (since Shopify signs the shared `apiSecretKey`, and every installed shop shares that secret). The attacker then resends that exact body+HMAC directly to the app's webhook endpoint but swaps `X-Shopify-Shop-Domain` to a target shop's domain. HMAC validation succeeds (body unchanged), header check succeeds (no sanitization/binding), and the handler/session logic treats the request as originating from the victim shop.

### Impact Explanation
If the target app has an existing offline session for the named victim shop, the forged request causes the webhook handler to run with `shop` = victim domain and, in `shopify-app-remix`, with a real admin API client scoped to the victim's stored access token — while the payload is the attacker's own webhook body. This can lead to app business logic acting incorrectly on the victim's data/store, cross-tenant misattribution of events, or triggering privileged admin API calls against a shop that never sent this request. This matches Shopify's bounty impact class of cross-tenant data/state access via authentication bypass in a first-party library used across many apps.

### Likelihood Explanation
The attacker needs: (1) their own store with the app installed (low bar — free dev/trial shop), (2) knowledge/guess of a target shop's `*.myshopify.com` domain (often discoverable or guessable), and (3) direct HTTP access to the app's public webhook endpoint (always internet-reachable by design). No secret, no privileged role, and no host-app misconfiguration is required — the gap exists in the library's default validation and is repeatable at will since the attacker fully controls their own valid HMAC/body pair.

### Recommendation
Do not treat the `X-Shopify-Shop-Domain` header as trusted solely because the body HMAC matches. Either (a) include the domain/webhook-id/topic in the value that's HMAC-verified where feasible, or (b) require host apps/library helpers to cross-check the header value against a known/installed shop list (and call `sanitizeShop`) before using it to load sessions or attribute events, and document this explicitly since the current API surface (`webhookCheck.valid === true`) implies more trust than it provides.

### Proof of Concept
```ts
// packages/apps/shopify-api/lib/webhooks/__tests__/process.test.ts (illustrative)
it('accepts a resigned webhook body with a forged shop domain header', async () => {
  const rawBody = JSON.stringify({id: 1, test: true});
  const hmac = createSHA256HMAC(config.apiSecretKey, rawBody, HashFormat.Base64); // computed by attacker's own legitimate webhook

  const forgedRequest = {
    headers: {
      'X-Shopify-Hmac-Sha256': hmac,
      'X-Shopify-Topic': 'products/create',
      'X-Shopify-Shop-Domain': 'victim-shop.myshopify.com', // forged, attacker doesn't own this shop
      'X-Shopify-Api-Version': '2024-01',
      'X-Shopify-Webhook-Id': 'abc-123',
    },
  };

  const result = await shopify.webhooks.process({
    rawBody,
    ...normalizeRequest(forgedRequest),
  });

  // Handler is invoked with the forged domain despite webhookCheck.valid === true
  expect(registeredHandlerCallback).toHaveBeenCalledWith(
    'products/create',
    'victim-shop.myshopify.com',
    rawBody,
    'abc-123',
    '2024-01',
    expect.anything(),
  );
});
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L168-201)
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
}
```

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L99-146)
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

  const name = getHeader(headers, headerNames.name);
  if (name) fields.name = name;

  const triggeredAt = getHeader(headers, headerNames.triggeredAt);
  if (triggeredAt) fields.triggeredAt = triggeredAt;

  const eventId = getHeader(headers, headerNames.eventId);
  if (eventId) fields.eventId = eventId;

  return {valid: true, ...fields};
}
```

**File:** packages/apps/shopify-api/lib/webhooks/process.ts (L99-156)
```typescript
async function callWebhookHandlers(
  config: ConfigInterface,
  webhookRegistry: WebhookRegistry<HttpWebhookHandlerWithCallback>,
  webhookCheck: WebhookValidationValid,
  rawBody: string,
  context: any,
): Promise<HandlerCallResult> {
  const log = logger(config);
  const {hmac: _hmac, valid: _valid, ...loggingContext} = webhookCheck;

  await log.debug(
    'Webhook request is valid, looking for HTTP handlers to call',
    loggingContext,
  );

  const handlers = webhookRegistry[webhookCheck.topic] || [];

  const response: HandlerCallResult = {statusCode: StatusCode.Ok};

  let found = false;
  for (const handler of handlers) {
    if (handler.deliveryMethod !== DeliveryMethod.Http) {
      continue;
    }
    if (!handler.callback) {
      response.statusCode = StatusCode.InternalServerError;
      response.errorMessage =
        "Cannot call webhooks.process with a webhook handler that doesn't have a callback";

      throw new ShopifyErrors.MissingWebhookCallbackError({
        message: response.errorMessage,
        response,
      });
    }

    found = true;

    await log.debug('Found HTTP handler, triggering it', loggingContext);

    // process() only handles programmatically-registered HTTP webhooks;
    // events are registered via app TOML and don't use this code path.
    if (webhookCheck.webhookType !== WebhookType.Webhooks) {
      throw new ShopifyErrors.InvalidWebhookError({
        message: 'process() only supports traditional webhooks, not events',
        response,
      });
    }
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-102)
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

    return {
      ...webhookContext,
      session,
      admin,
    };
```

**File:** packages/apps/shopify-app-remix/src/server/helpers/create-or-load-offline-session.ts (L1-21)
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
