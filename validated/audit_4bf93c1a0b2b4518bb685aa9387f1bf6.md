### Title
Shop-domain header is excluded from HMAC signature, allowing cross-tenant session hijack via `authenticateFulfillmentServiceFactory` - (File: packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts)

### Summary
`authenticateFulfillmentServiceFactory` validates the fulfillment-service request HMAC via `api.fulfillmentService.validate`, but that validation only covers `rawBody` and never binds the `X-Shopify-Shop-Domain` header to the signature. The function then trusts the unauthenticated `shop` header value directly to look up and return an offline session/admin client for that shop. [1](#0-0) [2](#0-1) 

### Finding Description
`authenticateFulfillmentServiceFactory` reads the raw body and calls `api.fulfillmentService.validate({rawBody, rawRequest})`, which internally delegates to `validateHmacFromRequestFactory`. That function computes the local HMAC over `rawBody` alone (`createSHA256HMAC(config.apiSecretKey, data, format)`/`validateHmacString`) and never includes the shop-domain header in the signed data. [3](#0-2) 

After `result.valid` is confirmed, `authenticate.ts` reads `shop` straight from the (unsigned) `X-Shopify-Shop-Domain` header and passes it to `ensureValidOfflineSession(params, shop)`, which loads the offline session for that shop from session storage and returns `admin: adminClientFactory({params, session})` bound to that session's access token. [4](#0-3) [5](#0-4) [6](#0-5) 

Because the apiSecretKey is shared across every shop that installs the same app, and the HMAC only signs the body (not the domain), an attacker who legitimately controls one installed store (Shop A, "attacker's own shop") can:
1. Cause/receive a legitimately signed fulfillment-service POST for their own shop A (e.g., by triggering a `FULFILLMENT_REQUEST`/`CANCELLATION_REQUEST` from Shopify to their own registered endpoint, which they fully control/observe since it is their own traffic — not MITM of a third party).
2. Capture the exact `rawBody` and its valid `X-Shopify-Hmac-Sha256` value.
3. Replay that identical `rawBody` + HMAC pair to the target app's fulfillment endpoint, but with the `X-Shopify-Shop-Domain` header rewritten to the victim shop B's domain.
4. Because the HMAC never covered the domain header, `result.valid` is still `true`. `authenticate.ts` then loads shop B's offline session and returns an `admin` client scoped to shop B's real access token to the attacker's handler code.

This breaks the intended invariant that "dispatch/session-binding happens only post full verification" — verification only proves the *body* came from Shopify (or the same-secret sender), not which shop it was for.

### Impact Explanation
This is a forged/cross-tenant authenticated request: an attacker with a valid installation of the app on their own store can spoof the shop-domain claim to gain an `admin` API context and `session` (with offline access token) belonging to any other victim shop that has the app installed, purely by controlling the `X-Shopify-Shop-Domain` header value. This directly maps to "forged authenticated request causing state change/data access" (cross-tenant session/data access via forged handler invocation), matching the target's expected impact class.

### Likelihood Explanation
- Preconditions: attacker must have (or be able to obtain) one valid `rawBody` + HMAC pair for the same app (trivial: install the app on any store and trigger any fulfillment-service webhook, or use any other event that produces a signed body/HMAC combination accepted by this validator, since HMAC validation here is body-only and the secret is shared per-app, not per-shop).
- No app-developer privilege, no leaked secret, and no MITM against a third party is required — only the attacker's own legitimately obtained signed traffic and knowledge of the victim's shop domain (public/guessable, e.g. `victim.myshopify.com`).
- Fully repeatable: replaying the same body/HMAC pair with a different domain header always passes validation since domain is not part of the signed data.

### Recommendation
Bind the shop-domain (and preferably webhookId/topic/timestamp to prevent replay) into the HMAC-verified data, or otherwise cryptographically tie the claimed shop to the signature — e.g., verify the domain against Shopify-issued metadata that is itself covered by the signature, or require an additional signed assertion of the shop before calling `ensureValidOfflineSession`. At minimum, reject/flag mismatches where the same body+HMAC pair is replayed for a different domain (nonce/webhookId-based replay protection combined with domain binding).

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/__tests__/authenticate.spoof.test.ts
import {MemorySessionStorage} from '@shopify/shopify-app-session-storage-memory';
import {shopifyApp} from '../../..';
import {getHmac, setUpValidSession, testConfig, TEST_SHOP} from '../../../__test-helpers';

const FULFILLMENT_URL =
  'https://example.myapp.io/authenticate/fulfillment_order_notification';

it('accepts a body+HMAC pair signed for one shop but relabeled with a victim shop domain', async () => {
  const sessionStorage = new MemorySessionStorage();
  const shopify = shopifyApp(testConfig({sessionStorage}));

  // Victim shop has an offline session in storage (e.g. real installed shop)
  const victimSession = await setUpValidSession(sessionStorage, {shop: 'victim-shop.myshopify.com'});

  const body = {kind: 'FULFILLMENT_REQUEST'};
  const bodyString = JSON.stringify(body);
  const validHmac = getHmac(bodyString); // attacker's own legitimately-obtained HMAC for this body

  // Attacker replays body+HMAC but swaps the shop-domain header to the victim's shop
  const forgedRequest = new Request(FULFILLMENT_URL, {
    method: 'POST',
    body: bodyString,
    headers: {
      'X-Shopify-Hmac-Sha256': validHmac,
      'X-Shopify-Shop-Domain': 'victim-shop.myshopify.com', // not the shop that owns validHmac's origin traffic
    },
  });

  const {session} = await shopify.authenticate.fulfillmentService(forgedRequest);

  // Attacker-controlled request obtains victim's session/access token
  expect(session).toEqual(victimSession);
});
```
Expected result: the request is accepted (`result.valid === true`) and `session`/`admin` are bound to the victim shop, because `validateHmacFromRequestFactory` only checks `rawBody` against `X-Shopify-Hmac-Sha256`, never the `X-Shopify-Shop-Domain` value. Fast-validation counterpart: a bad-HMAC request (`getHmac` mismatched) yields `result.valid === false` and the fulfillment handler count remains 0 (401/400 thrown before session lookup), confirming the flaw is specifically the unauthenticated domain claim, not the HMAC check itself.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts (L33-80)
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

    if (!session) {
      logger.info('Fulfillment service request could not find session', {
        shop,
      });
      throw new Response(undefined, {
        status: 400,
        statusText: 'Bad Request',
      });
    }

    logger.debug('Found a session for the fulfillment service request', {
      shop,
    });

    return {
      session,
      payload,
      admin: adminClientFactory({params, session}),
    };
```

**File:** packages/apps/shopify-api/lib/fulfillment-service/validate.ts (L10-20)
```typescript
export function validateFactory(config: ConfigInterface) {
  return async function validate({
    rawBody,
    ...adapterArgs
  }: ValidateParams): Promise<ValidationInvalid | ValidationValid> {
    return validateHmacFromRequestFactory(config)({
      type: HmacValidationType.FulfillmentService,
      rawBody,
      ...adapterArgs,
    });
  };
```

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

**File:** packages/apps/shopify-app-remix/src/server/helpers/ensure-valid-offline-session.ts (L1-17)
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
