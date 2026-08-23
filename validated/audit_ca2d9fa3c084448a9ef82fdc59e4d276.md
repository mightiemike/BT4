## Title
Webhook and Fulfillment-Service authentication verifies only the request body's HMAC but never binds the `X-Shopify-Shop-Domain` header to that signature, allowing any installed merchant to hijack another shop's offline session/admin API client - (File: `packages/apps/shopify-api/lib/utils/hmac-validator.ts`)

### Summary
`shopify.webhooks.validate` / `shopify.fulfillmentService.validate` authenticate an incoming request purely by recomputing an HMAC over the raw request **body** with the app's shared `apiSecretKey`. The `X-Shopify-Shop-Domain` header — the value later used to select *which shop's offline session/admin client* gets attached to the request — is never included in the signed data. Because the HMAC key is per-app (not per-shop), any merchant who has legitimately installed the app can capture one of their own valid `(rawBody, X-Shopify-Hmac-Sha256)` pairs and replay it to the app's webhook or fulfillment-service endpoint while forging the `X-Shopify-Shop-Domain` header to point at a different shop that also has the app installed. The request still passes HMAC validation (only the body is checked), but the "shop" component used to authorize/attribute the request is attacker-controlled and unverified — the same class of bug as the referenced report, where the top-level artifact (Vault / webhook body) is authenticated but a critical dependent component (TwabController-PrizePool / shop domain) is not.

### Finding Description
`validateHmacFromRequestFactory` computes the local HMAC solely from `rawBody` and compares it to the `X-Shopify-Hmac-Sha256` header: [1](#0-0) 

`checkWebhooksHeaders` then reads the `domain` (shop) straight from the `X-Shopify-Shop-Domain` header with no cryptographic tie to the just-validated HMAC: [2](#0-1) 

`authenticateWebhookFactory` trusts `check.domain` and uses it directly to look up/refresh and attach the shop's offline session and admin client: [3](#0-2) 

The identical pattern exists for fulfillment-service requests, where the shop is taken from the same unauthenticated header after only the body's HMAC is validated: [4](#0-3) [5](#0-4) 

`ensureValidOfflineSession`/`createOrLoadOfflineSession` then load and return the real offline `Session` (with its access token) for whatever `shop` string was supplied, without any check that the shop matches an authenticated identity: [6](#0-5) [7](#0-6) 

Because `apiSecretKey` is a single, app-wide secret shared across every shop that installs the app (not derived per-shop), a valid `(rawBody, hmac)` pair generated for Shop A's own webhook deliveries remains cryptographically valid when replayed with the `X-Shopify-Shop-Domain` header changed to Shop B. The library has no mechanism analogous to a "factory" check that binds the verified body to the specific shop domain claimed in the header, mirroring the reported bug class where authenticity is checked at one level (the vault/body) but not for a dependent value used for security-critical decisions (TwabController-PrizePool/shop domain).

### Impact Explanation
An attacker who is simply a legitimate, unprivileged merchant/customer of the target app (i.e., has installed it on their own store and thus can trigger/capture real webhook or fulfillment-service deliveries) can:
- Replay a captured valid webhook body+HMAC with a forged `X-Shopify-Shop-Domain` targeting another installed shop.
- Cause the app to load and hand back that victim shop's offline `Session`/access token via `admin`/`storefront` clients inside the webhook handler, and to process attacker-controlled JSON payload as if it originated from the victim shop.
- This results in cross-tenant session use: the attacker's payload is executed against another merchant's store/session context, and depending on the app's webhook handler logic, could leak or corrupt victim data, trigger unwanted admin API mutations against the victim shop, or exfiltrate data tied to the victim's session.

### Likelihood Explanation
Exploitation only requires becoming an app user (installing the app on any store or being sent webhooks by the app), capturing one legitimate `(rawBody, hmac)` pair for any topic, and resending it to the app's own webhook/fulfillment-service endpoint with a modified `X-Shopify-Shop-Domain` header — no secret material or MITM is needed since the header is not covered by the signature. This is directly reachable via anonymous/unprivileged HTTP requests to the app's webhook route.

### Recommendation
Bind the shop/domain claim to the signed payload rather than trusting the header independently:
- Include `X-Shopify-Shop-Domain` (and ideally `X-Shopify-Topic`, `X-Shopify-Webhook-Id`) in the HMAC-covered canonical string, or
- After HMAC success, cross-check the `domain` header against an authoritative source (e.g., verify the shop is registered/expected for the given webhook subscription, or require the topic+domain pair to be looked up and confirmed against the app's own webhook registration records) before using it to select a session, or
- At minimum, rate-limit/replay-protect and log mismatches between the claimed domain and any independently known shop context before granting `admin`/`storefront` API access.

### Proof of Concept
1. Merchant A installs the app; the app registers a webhook subscription and Shopify later POSTs a legitimate webhook (any topic) to the app with a valid `X-Shopify-Hmac-Sha256` computed over the raw body using the app's shared `apiSecretKey`, and header `X-Shopify-Shop-Domain: shop-a.myshopify.com`.
2. Attacker (Merchant A) captures this `(rawBody, hmac)` pair.
3. Attacker sends a new POST to the same app's webhook endpoint with the identical `rawBody` and `X-Shopify-Hmac-Sha256`, but sets `X-Shopify-Shop-Domain: shop-b.myshopify.com` (a different shop that has also installed the app).
4. `validateHmacFromRequestFactory` validates successfully because it only checks `rawBody` against the HMAC — see [1](#0-0) .
5. `checkWebhooksHeaders` returns `domain: 'shop-b.myshopify.com'` unchecked — see [2](#0-1) .
6. `authenticateWebhookFactory` loads Shop B's offline session/admin client and passes it to the app's webhook handler along with the attacker's forged payload — see [3](#0-2) .

### Citations

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/webhooks/authenticate.ts (L35-65)
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
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts (L33-60)
```typescript
    const rawBody = await request.text();
    const result = await api.fulfillmentService.validate({
      rawBody,
      rawRequest: request,
    });

    if (!result.valid) {
      logger.error('Received an invalid fulfillment service request', {
        reason: result.reason,
      });

      throw new Response(undefined, {
        status: 400,
        statusText: 'Bad Request',
      });
    }

    const payload = JSON.parse(rawBody);
    const shop = request.headers.get(ShopifyHeader.Domain) || '';

    logger.debug(
      'Fulfillment service request is valid, looking for an offline session',
      {
        shop,
      },
    );

    const session = await ensureValidOfflineSession(params, shop);
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/fulfillment-service/authenticate.ts (L33-60)
```typescript
    const rawBody = await request.text();
    const result = await api.fulfillmentService.validate({
      rawBody,
      rawRequest: request,
    });

    if (!result.valid) {
      logger.error('Received an invalid fulfillment service request', {
        reason: result.reason,
      });

      throw new Response(undefined, {
        status: 400,
        statusText: 'Bad Request',
      });
    }

    const payload = JSON.parse(rawBody);
    const shop = request.headers.get(ShopifyHeader.Domain) || '';

    logger.debug(
      'Fulfillment service request is valid, looking for an offline session',
      {
        shop,
      },
    );

    const session = await ensureValidOfflineSession(params, shop);
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
