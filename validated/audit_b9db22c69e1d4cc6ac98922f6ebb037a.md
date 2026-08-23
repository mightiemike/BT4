This confirms the vulnerability pattern: the webhook HMAC in `validateHmacString` at [1](#0-0)  only signs `rawBody` — the `X-Shopify-Shop-Domain` header is never included in the HMAC computation and is not checked against any shop whitelist (`sanitizeShop`) before being trusted as the webhook's origin shop, as seen in `checkWebhooksHeaders`/`checkEventsHeaders` at [2](#0-1)  and [3](#0-2) . This `domain` value is then consumed directly downstream (`check.domain`) to look up/create the offline session in `authenticateWebhookFactory`, without any `sanitizeShop`/domain-whitelist check, at [4](#0-3)  and the analogous react-router version at [5](#0-4) .

### Title
Webhook HMAC does not bind the `X-Shopify-Shop-Domain` header, allowing cross-tenant shop spoofing via signature replay - (File: `packages/apps/shopify-api/lib/webhooks/validate.ts`, `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
The reported bug class is "lack of whitelisting" of an untrusted identifier (chain ID) that is trusted by a contract function without being cryptographically bound to the authenticated payload, enabling cross-tenant/unauthorized state changes. The `shopify-app-js` webhook validation pipeline has an analogous flaw: the HMAC signature (`X-Shopify-Hmac-Sha256`) is computed only over the raw request body, not over the `X-Shopify-Shop-Domain` header. That header is subsequently trusted as the "shop" that owns the webhook without any additional whitelist/binding check.

### Finding Description
`validateHmacString`/`validateHmacFromRequestFactory` compute and compare the HMAC strictly against `rawBody`: [6](#0-5) . The domain header is read separately in `checkWebhooksHeaders`/`checkEventsHeaders` purely to check for presence, never to verify it matches a value cryptographically tied to the signed body: [2](#0-1) .

Because the app's HMAC secret (`apiSecretKey`) is shared across *every shop that installs the app*, any merchant who installs the app can trigger Shopify to deliver a legitimately-signed webhook (e.g., a benign topic on their own store) and capture a valid `(rawBody, hmac)` pair. Since the domain header is not part of the signed material, that same `(rawBody, hmac)` pair remains valid when replayed with the `X-Shopify-Shop-Domain` header rewritten to point at a victim shop. `validateFactory` will report `valid: true` with `domain` set to the attacker-chosen value: [7](#0-6) .

The webhook authentication handler then uses this unverified `check.domain` directly to resolve or create the offline session used to build the `admin`/`storefront` API clients returned to the app's route handler, with no `sanitizeShop`/domain whitelist check applied to it: [8](#0-7) .

### Impact Explanation
An attacker who installs the target app on their own (attacker-controlled) shop can obtain a validly-HMAC-signed webhook payload from Shopify, then replay it against the app's webhook endpoint with the `X-Shopify-Shop-Domain` header swapped to any victim shop domain the attacker knows is installed. The app's webhook handler will process it as though it originated from the victim shop — loading the victim's offline session and invoking the registered handler/business logic (e.g., app/uninstalled processing, data deletion, GDPR triggers, or app-specific side effects) using the victim's `shop` context. This is a cross-tenant authentication bypass on the webhook channel, directly comparable to the "unauthorized chain ID" contract issue: an unvalidated identifier field is trusted to select security-relevant, tenant-scoped state.

### Likelihood Explanation
Exploitation requires only: (1) the attacker being able to install the app on any shop (public apps are installable by anyone), and (2) knowledge of a target shop's domain (public, e.g., `victim.myshopify.com`). No secrets need to be leaked and no privileged access to the target is required — the existing repo test at [9](#0-8)  already demonstrates that a signature computed over one payload/domain combination is accepted when the domain field is swapped, confirming domain is unauthenticated.

### Recommendation
Include the `X-Shopify-Shop-Domain` (and ideally `X-Shopify-Topic`, `X-Shopify-Webhook-Id`) header values as part of the HMAC-signed material verified in `validateHmacFromRequestFactory`, or otherwise cryptographically bind the domain header to the signed body before trusting it in `checkWebhooksHeaders`/`checkEventsHeaders`. At minimum, apps should be required to verify that `check.domain` corresponds to a shop with an active install/session before processing sensitive webhook side effects, rather than trusting the header value outright.

### Proof of Concept
1. Attacker installs the target app on `attacker.myshopify.com`.
2. Attacker triggers any webhook delivery (e.g., `app/uninstalled`) and captures the raw POST body and the `X-Shopify-Hmac-Sha256` header value Shopify sent.
3. Attacker replays the exact same body and HMAC header to the app's webhook endpoint, but sets `X-Shopify-Shop-Domain: victim.myshopify.com`.
4. `shopify.webhooks.validate` / `validateFactory` returns `{valid: true, domain: 'victim.myshopify.com', ...}` because the HMAC check in `validateHmacFromRequestFactory` only checks `rawBody` against the secret, ignoring the domain header [10](#0-9) .
5. `authenticateWebhookFactory` loads/creates the offline session for `victim.myshopify.com` and proceeds to invoke app logic with that shop's session context [11](#0-10) .

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L153-200)
```typescript
export async function validateHmacString(
  config: ConfigInterface,
  data: string,
  hmac: string,
  format: HashFormat,
) {
  const localHmac = await createSHA256HMAC(config.apiSecretKey, data, format);

  return safeCompare(hmac, localHmac);
}

export function getCurrentTimeInSec() {
  return Math.trunc(Date.now() / 1000);
}

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

**File:** packages/apps/shopify-api/lib/webhooks/validate.ts (L148-189)
```typescript
function checkEventsHeaders(
  headers: Headers,
): WebhookValidationMissingHeaders | WebhookValidationValid {
  const headerNames = WEBHOOK_HEADER_NAMES[WebhookType.Events];
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
  const eventId = getRequiredHeader(
    headers,
    headerNames.eventId,
    missingHeaders,
  );

  if (missingHeaders.length) {
    return {
      valid: false,
      reason: WebhookValidationErrorReason.MissingHeaders,
      missingHeaders,
    };
  }

  const fields: EventsWebhookFields = {
    webhookType: WebhookType.Events,
    hmac: hmac!,
    topic: topicForStorage(topic!),
    domain: domain!,
    apiVersion: apiVersion!,
    webhookId: webhookId!,
    eventId: eventId!,
  };
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

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/webhooks/authenticate.ts (L35-52)
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
```

**File:** packages/apps/shopify-api/lib/webhooks/__tests__/validate.test.ts (L97-125)
```typescript
  it('returns false when a cookie signature is replayed as the webhook HMAC', async () => {
    const shopify = shopifyApi(testConfig());
    const app = getTestApp(shopify);
    const cookieValue = 'oauth-state-nonce';
    const cookieResponse = {} as NormalizedResponse;
    const cookieJar = new Cookies(
      {headers: {}} as NormalizedRequest,
      cookieResponse,
      {keys: [shopify.config.apiSecretKey]},
    );
    await cookieJar.setAndSign('shopify_app_state', cookieValue);

    const response = await request(app)
      .post('/webhooks')
      .set(
        headers({
          hmac: cookieJar.outgoingCookieJar['shopify_app_state.sig'].value,
          topic: 'app/uninstalled',
          domain: 'victim-shop.myshopify.io',
        }),
      )
      .send(cookieValue)
      .expect(200);

    expect(response.body.data).toEqual({
      valid: false,
      reason: WebhookValidationErrorReason.InvalidHmac,
    });
  });
```
