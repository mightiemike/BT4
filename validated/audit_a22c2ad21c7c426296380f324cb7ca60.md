### Title
Unbounded webhook body read before HMAC validation enables memory-exhaustion DoS in Remix/React Router webhook authenticate handlers - (File: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts`)

### Summary
The `authenticateWebhookFactory` used by both `@shopify/shopify-app-remix` and `@shopify/shopify-app-react-router` buffers the entire incoming webhook request body into memory via `await request.text()` before any HMAC/authenticity validation is performed, with no size limit enforced anywhere in the call chain. This is the same bug class as the reported issue: an externally-triggerable, unbounded read of attacker-controlled network data that is fully materialized in memory prior to any validation, allowing a remote unauthenticated actor to exhaust process memory and freeze the auth/webhook-processing handler — analogous to how oversized `eth_getLogs` responses froze the Gravity Bridge oracle loop.

### Finding Description
The webhook authenticate flow is: [1](#0-0) 

`request.text()` is a Fetch-API call that reads and buffers the *entire* request body as a JS string before the code ever calls `api.webhooks.validate()` to check the HMAC. There is no `Content-Length` check, no streaming size cap, and no early rejection of oversized bodies.

This is identical in `shopify-app-react-router`: [2](#0-1) 

Contrast this with the dedicated `@shopify/shopify-app-express` package, which explicitly caps the webhook body size at 500 KB via Express body-parser middleware *before* the shared `webhooks.process`/`webhooks.validate` core is invoked: [3](#0-2) 

The shared `@shopify/shopify-api` core (`validateFactory`/`validateHmacFromRequestFactory`) also does not itself enforce any body-size limit — it only rejects on missing/invalid HMAC after the string has already been received: [4](#0-3) [5](#0-4) 

Because size enforcement is left entirely to the adapter package, two of the three official framework adapters (Remix and React Router) provide no protection at all, while the Express adapter does. The webhook endpoint is a public HTTP route that must accept unauthenticated POST requests from "Shopify" (in practice, from anyone who can guess/know the webhook URL, since HMAC validation happens only *after* the full body is read into memory).

### Impact Explanation
An attacker can send POST requests with arbitrarily large bodies (limited only by the underlying Node HTTP server/reverse proxy, which in many self-hosted Remix/React Router deployments has no strict body cap) to the app's webhook endpoint. Each request forces the process to allocate memory equal to the entire request body before the HMAC check ever runs and rejects it. Concurrent or repeated large-body requests can exhaust available memory/CPU, causing the Node process to crash or become unresponsive — a denial of service of the webhook authentication handler, which is the same "freeze the bridge/oracle" impact class as the referenced report (an auth/event-processing loop rendered permanently unavailable by oversized externally-supplied payloads).

### Likelihood Explanation
Webhook endpoints are, by design, publicly reachable POST routes that must accept requests without pre-existing sessions (the app cannot require a session before validating a webhook). No privileged access, secret leakage, or MITM is required — a single anonymous actor can trigger this repeatedly. The only variable is the effective body-size ceiling imposed by the deployment's own HTTP layer (e.g., a reverse proxy); the shopify-app-js library itself imposes none for these two adapters, whereas it explicitly does for Express, showing the gap is a library-level omission rather than an inherent platform limit.

### Recommendation
Enforce an explicit maximum body size for the webhook authenticate handlers in `shopify-app-remix` and `shopify-app-react-router`, mirroring the 500 KB cap already used in `shopify-app-express`. This can be done by checking `Content-Length` before calling `request.text()`, and/or by reading the body via a size-limited stream reader that aborts once a threshold is exceeded, returning an early 413/400 response instead of buffering unbounded attacker-controlled data.

### Proof of Concept
1. Identify an app's webhook route (e.g., `/webhooks` configured via `shopify.app.toml` or `addHandlers` `callbackUrl`).
2. Send a POST request to that route with a body of several hundred MB to several GB (e.g., using `curl --data-binary @largefile /webhooks`), without a valid HMAC header.
3. Observe that `authenticateWebhookFactory` calls `await request.text()` and fully buffers the payload into memory before `api.webhooks.validate()` is ever invoked and rejects it for a bad/missing HMAC — the rejection only happens after the costly full read.
4. Repeat with multiple concurrent large-body requests to exhaust server memory, causing degraded performance or process crash, effectively freezing the app's webhook processing (and potentially the whole Node process) for legitimate Shopify-originated webhooks.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L19-38)
```typescript
  return async function authenticate(
    request: Request,
  ): Promise<WebhookContext<Topics>> {
    if (request.method !== 'POST') {
      logger.debug(
        'Received a non-POST request for a webhook. Only POST requests are allowed.',
        {url: request.url, method: request.method},
      );
      throw new Response(undefined, {
        status: 405,
        statusText: 'Method not allowed',
      });
    }

    const rawBody = await request.text();

    const check = await api.webhooks.validate({
      rawBody,
      rawRequest: request,
    });
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L19-38)
```typescript
  return async function authenticate(
    request: Request,
  ): Promise<WebhookContext<Topics>> {
    if (request.method !== 'POST') {
      logger.debug(
        'Received a non-POST request for a webhook. Only POST requests are allowed.',
        {url: request.url, method: request.method},
      );
      throw new Response(undefined, {
        status: 405,
        statusText: 'Method not allowed',
      });
    }

    const rawBody = await request.text();

    const check = await api.webhooks.validate({
      rawBody,
      rawRequest: request,
    });
```

**File:** packages/apps/shopify-app-express/src/webhooks/index.ts (L20-35)
```typescript
  return function ({webhookHandlers}: ProcessWebhooksMiddlewareParams) {
    mountWebhooks(api, config, webhookHandlers);

    return [
      express.text({type: '*/*', limit: '500kb'}),
      async (req: Request, res: Response) => {
        await process({
          req,
          res,
          api,
          config,
        });
      },
    ];
  };
}
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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L46-61)
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
```
