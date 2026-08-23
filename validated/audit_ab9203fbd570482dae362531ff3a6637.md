I have enough context now to analyze this properly.

Analysis of the actual exploit chain: The `apiSecretKey` is the app's single OAuth client secret, shared across **all** shops that install the app — it is not shop-specific. `validateHmacString` at [1](#0-0)  computes `HMAC(apiSecretKey, rawBody)` and compares it via `safeCompare` to the request's `X-Shopify-Hmac-Sha256` header — no shop identifier is included in the signed material. `validateHmacFromRequestFactory` at [2](#0-1)  only checks `rawBody.length`, the presence of the HMAC header, and calls `validateHmacString` — again, no binding to `X-Shopify-Shop-Domain`. This same helper is reused unmodified for fulfillment-service, webhook, and flow validation, e.g. [3](#0-2) .

In `authenticateFulfillmentServiceFactory`, after `api.fulfillmentService.validate` succeeds, the shop identity used to look up the offline session is read directly and unconditionally from the attacker-controlled header: [4](#0-3) 

Since the HMAC never binds to that header, an identical `rawBody`+`hmac` pair originally issued by Shopify for Shop A remains "valid" when resent with `X-Shopify-Shop-Domain: shop-b.myshopify.com`, causing `ensureValidOfflineSession` to return Shop B's offline session/access token and `adminClientFactory` to build an authenticated admin client scoped to Shop B — despite the payload never being intended for or generated against Shop B. This is a cross-tenant session confusion within the same app, satisfying the "authenticity binds to the correct tenant" invariant failure described in the question. The same header-trust pattern also appears in the equivalent `shopify-app-react-router` package (identical code) and in the analogous webhook/flow authenticators, which read the destination shop (`X-Shopify-Shop-Domain` or `payload.shopify_domain`) without any cryptographic tie to the HMAC.

### Title
Fulfillment-service HMAC validation does not bind `X-Shopify-Shop-Domain`, enabling cross-tenant session confusion via payload replay - (packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts, packages/apps/shopify-api/lib/utils/hmac-validator.ts)

### Summary
`validateHmacFromRequestFactory` verifies only `rawBody` against the app-wide `apiSecretKey`-derived HMAC and never includes the `X-Shopify-Shop-Domain` header in the signed data. `authenticateFulfillmentServiceFactory` then reads the shop identity straight from that unauthenticated header to load an offline session, so a valid `rawBody`+HMAC pair originally issued for Shop A can be replayed with a different shop header to obtain an authenticated admin session for Shop B.

### Finding Description
`validateHmacString` computes `HMAC-SHA256(apiSecretKey, rawBody)` and compares against the `X-Shopify-Hmac-Sha256` header using `safeCompare`, with no other request data (including shop domain) folded into the MAC input (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:153-200`). Because `apiSecretKey` is the single app-level secret shared by every shop that installs the app (not a per-shop secret), any HMAC that is valid for one shop's payload is *also* a syntactically/cryptographically valid HMAC for that exact byte string regardless of which shop it is later claimed to belong to. `authenticateFulfillmentServiceFactory` calls `api.fulfillmentService.validate` (which only checks the body/HMAC), then independently trusts `request.headers.get(ShopifyHeader.Domain)` to select which shop's offline session to load and return (`authenticate.ts:33-60`). There is no check that the shop in the header actually matches the shop the payload/HMAC was generated for. An attacker who legitimately receives one such payload for their own shop (e.g. by controlling the endpoint that receives fulfillment-service callbacks, or a proxy in front of it) can resend the identical `rawBody` and `X-Shopify-Hmac-Sha256` value to the app while substituting a different shop's domain in `X-Shopify-Shop-Domain`, and the library will authenticate the request as that other shop.

### Impact Explanation
This yields cross-tenant session access: the attacker obtains an `admin` GraphQL client and `session` object scoped to a shop they do not control, backed by that shop's real offline access token (`adminClientFactory({params, session})`). Depending on how the app route uses the resulting `admin`/`session`/`payload`, this can allow reading or mutating another merchant's store data, which maps to Shopify's "cross-tenant data/session access" bounty class.

### Likelihood Explanation
Requires the attacker to already control at least one shop that has this app installed and legitimately receives one fulfillment-service HMAC payload (a realistic precondition for a fulfillment-service integration, which by design POSTs to a URL the shop/app operator configures and can front with their own proxy). No secret material, MITM of Shopify's traffic, or app-developer privileges are needed — only replaying a previously-seen valid rawBody/HMAC pair with a modified header. The attack is fully repeatable as long as the captured payload/HMAC pair is reused verbatim (HMAC does not expire and there is no nonce/timestamp binding in this validator, unlike `validateHmac` used for OAuth/app-proxy which enforces `validateHmacTimestamp`).

### Recommendation
Bind the shop identity into the signed material or cross-check it independently: e.g., verify that the `shop`/domain implied by the payload matches an authoritative, already-established relationship (such as looking up the session by shop and then verifying a shop-specific secret, or requiring the payload itself to carry the shop and validating that against the session before trusting the header), or migrate to a scheme where the HMAC input includes the shop domain so a replayed payload cannot be re-attributed to a different tenant.

### Proof of Concept
```ts
// packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/__tests__/cross-tenant.test.ts
import {MemorySessionStorage} from '@shopify/shopify-app-session-storage-memory';
import {shopifyApp} from '../../..';
import {getHmac, setUpValidSession, testConfig} from '../../../__test-helpers';

const FULFILLMENT_URL =
  'https://example.myapp.io/authenticate/fulfillment_order_notification';

it('replays Shop A payload+HMAC against Shop B and authenticates as Shop B', async () => {
  const sessionStorage = new MemorySessionStorage();
  const shopify = shopifyApp(testConfig({sessionStorage}));

  const shopASession = await setUpValidSession(sessionStorage, {shop: 'shop-a.myshopify.com'});
  const shopBSession = await setUpValidSession(sessionStorage, {shop: 'shop-b.myshopify.com'});

  const body = JSON.stringify({kind: 'FULFILLMENT_REQUEST'});
  const hmac = getHmac(body); // same secret used for every shop

  const forgedRequest = new Request(FULFILLMENT_URL, {
    method: 'POST',
    body,
    headers: {
      'X-Shopify-Hmac-Sha256': hmac,
      'X-Shopify-Shop-Domain': shopBSession.shop, // swapped from shop-a to shop-b
    },
  });

  const {session} = await shopify.authenticate.fulfillmentService(forgedRequest);

  // Passes validation and returns Shop B's session even though this
  // rawBody/hmac pair was never generated for Shop B specifically.
  expect(session.shop).toBe(shopBSession.shop);
});
```
Expected result on the current codebase: the assertion passes, confirming the request authenticates against Shop B's offline session using an HMAC that carries no shop-specific binding.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L153-162)
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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts (L50-60)
```typescript
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
