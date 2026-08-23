### Title
Unbounded webhook body read before HMAC authentication enables DoS of the webhook authentication handler - (File: `packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts`)

### Summary
The C4 report describes an on-chain analog where an unauthenticated actor supplies unbounded-length data that is unconditionally processed by privileged/critical code paths (`_settleAuction`/`_createAuction`), causing gas exhaustion and denial of service. The equivalent flaw class here — "attacker-controlled data whose size is never bounded before being fully processed by a protected code path" — is present in the webhook authentication handler, which buffers and HMAC-hashes the *entire* raw request body **before** performing any authentication check.

### Finding Description
`authenticateWebhookFactory` in both the Remix and React Router adapters accepts any `POST` request to the app's public webhook endpoint and immediately reads the full request body into memory with no size limit, prior to verifying the Shopify HMAC signature: [1](#0-0) 

The `rawBody` is then passed straight into `api.webhooks.validate`, which computes a SHA-256 HMAC over the *entire* body before deciding whether the request is authentic: [2](#0-1) [3](#0-2) 

Nowhere in this path — `request.text()`, `validateHmacFromRequestFactory`, or `createSHA256HMAC` — is there any check on the size of `rawBody` before it is buffered into memory and hashed. The React Router adapter has the identical pattern: [4](#0-3) 

This webhook endpoint is, by design, reachable by any unauthenticated caller on the internet (Shopify itself calls it without any prior handshake other than the shared secret used solely for HMAC verification after the fact). Because authentication (the HMAC check) is only performed *after* the entire body has been read and hashed, the cost of handling a request scales with attacker-supplied body size with no library-enforced ceiling — directly mirroring the "art piece size not limited" root cause in the reference report, where unbounded attacker-controlled data was processed in full before/without a bound check, degrading or blocking a critical operation.

### Impact Explanation
An anonymous attacker can send arbitrarily large POST bodies to the app's public `/webhooks` route. Each such request forces the process to:
1. Buffer the entire body into memory (`request.text()`), and
2. Compute a SHA-256 HMAC over that entire buffer,

before rejecting the request for an invalid HMAC. Repeated or concurrent large-body requests can exhaust server memory/CPU, degrading or denying the webhook authentication handler for legitimate Shopify-originated webhooks — a direct analog to the referenced report's "DoS of `AuctionHouse`" impact, translated to "DoS of an auth handler" in the shopify-app-js runtime.

### Likelihood Explanation
The webhook route is intentionally public and unauthenticated at the transport layer (that's the point of webhooks), so no privileged access, leaked secret, or MITM is required — a single anonymous HTTP POST is sufficient to trigger the unbounded read/hash. Any hosting layer that does not impose its own separate body-size limit (which is not part of this library and not documented as a requirement) is exposed to this behavior for every request that reaches the shopify-app-js webhook authenticate/validate code path.

### Recommendation
Enforce a maximum request/body size before reading or hashing the webhook payload — e.g., check `Content-Length` (or a streaming byte-count limit) against a sane maximum (such as Shopify's documented payload limits) in `authenticateWebhookFactory`/`validateHmacFromRequestFactory`, and reject oversized requests with an early 4xx response before calling `request.text()` or `createSHA256HMAC`.

### Proof of Concept
1. Deploy a shopify-app-js (Remix or React Router) app exposing the default webhook route.
2. As an anonymous client, send a `POST` to that route with a body of several hundred MB (or GB) and an invalid/absent `X-Shopify-Hmac-Sha256` header.
3. Observe that `authenticate()` calls `await request.text()` (buffering the full body) and then `api.webhooks.validate` computes `createSHA256HMAC` over the full body before returning `InvalidHmac`/`MissingHmac` — i.e., memory/CPU cost is paid proportional to attacker-chosen size for every such request, regardless of authenticity.
4. Repeating this concurrently demonstrates resource exhaustion of the process handling webhook authentication.

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

**File:** packages/apps/shopify-api/runtime/crypto/utils.ts (L19-44)
```typescript
export async function createSHA256HMAC(
  secret: HMACSecret,
  payload: string,
  returnFormat: HashFormat = HashFormat.Base64,
): Promise<string> {
  const cryptoLib = getCryptoLib();
  const key = await cryptoLib.subtle.importKey(
    'raw',
    hmacKeyData(secret),
    {
      name: 'HMAC',
      hash: {name: 'SHA-256'},
    },
    false,
    ['sign'],
  );

  const signature = await cryptoLib.subtle.sign(
    'HMAC',
    key,
    enc.encode(payload),
  );
  return returnFormat === HashFormat.Base64
    ? asBase64(signature)
    : asHex(signature);
}
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
