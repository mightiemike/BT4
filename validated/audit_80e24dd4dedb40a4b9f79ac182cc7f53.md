### Title
Fulfillment-service request authentication trusts an unsigned `X-Shopify-Shop-Domain` header for session lookup - ([File: packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts])

### Summary
The Sherlock bug is a class of "validation gap between two code paths that should enforce the same invariant": one path (`requestToClosePosition`) checks a value, but an alternate path (`closeQuote`'s `CANCEL_CLOSE_PENDING` branch) mutates the same state without re-checking the invariant, letting an actor land the system in a state that should be impossible. The shopify-app-js analog I looked for is a place where one signed/validated field is used to authorize a request, but a *different, unsigned* field derived from the same request is used to determine *whose* data to act on — i.e., the invariant "the shop being authenticated is the shop the signature covers" is enforced in one path but not carried through consistently.

### Finding Description
In `authenticateFulfillmentServiceFactory` (present near-identically in both the `shopify-app-remix` and `shopify-app-react-router` packages), the request is authenticated via `api.fulfillmentService.validate({rawBody, rawRequest: request})`, which validates the `X-Shopify-Hmac-Sha256` header against the **raw body** only [1](#0-0) . Immediately afterward, the code reads the shop identity used for session lookup from the `X-Shopify-Shop-Domain` header, **not from the HMAC-covered body**: [2](#0-1) 

That `shop` value is passed straight into `ensureValidOfflineSession(params, shop)`, which loads/derives the offline session id as `offline_${shop}` and returns a stored access-token session and an authenticated Admin API client bound to that shop [3](#0-2) .

Because the HMAC in `validateHmacFromRequestFactory` is computed purely over `rawBody` and never binds the `X-Shopify-Shop-Domain` header into the signed material [4](#0-3) , the header is not part of the cryptographic invariant that "this request was signed by Shopify for shop X." This mirrors the Sherlock pattern exactly: the check ("is this authentic Shopify traffic") is enforced on one artifact (body+HMAC), while a second, security-relevant artifact used downstream to select *which tenant's* credentials to hand out (the shop header) is left unvalidated/unbound to that check — an invariant gap between the "validate" step and the "use" step.

### Impact Explanation
If an attacker can influence or replay the `X-Shopify-Shop-Domain` header independently of the signed body (e.g., a misconfigured reverse proxy/CDN/load balancer that forwards a legitimate Shopify-signed fulfillment-service payload but rewrites/duplicates the domain header, or any intermediary that does not treat this header as trusted-and-bound), the app will authenticate the request as valid (HMAC passes) but then look up and hand out the Admin API session/access token for a **different shop** than the one that actually produced the signed body. This is a cross-tenant session/credential disclosure vector: `adminClientFactory({params, session})` is returned to the caller bound to the wrong merchant's offline access token.

### Likelihood Explanation
Exploitability depends entirely on the deployment's network topology — the shop domain header must be attacker-influenceable somewhere between Shopify and the app (e.g., through infrastructure that does not lock this header down, or a bug that lets a client set it directly on a request path reachable without going through Shopify's actual delivery). shopify-app-js documents `ShopifyHeader.Domain` as authoritative but does not itself defend against header spoofing since the HMAC scheme was designed to cover only the body. This is a real, concrete gap in the code's own invariant enforcement (unlike a MITM-only or purely infra-configuration issue elsewhere), reachable from an unauthenticated HTTP endpoint (the fulfillment-service webhook route), so it meets the "accepted forged Shopify request" bar if the header can be forged or mismatched relative to the signed body in any accepted deployment configuration.

### Recommendation
Do not trust `request.headers.get(ShopifyHeader.Domain)` as the sole source of shop identity for session lookup after HMAC validation. Instead, derive/cross-check the shop from data that is actually covered by the HMAC signature (e.g., a `shop_domain`/similar field inside the signed JSON body, if present in the fulfillment-service callback payload), or explicitly validate that the header value is consistent with data in the signed body before using it to select which tenant's session to load. At minimum, log/reject when the header-derived shop cannot be corroborated by signed payload content.

### Proof of Concept
Conceptual (not verified against a live deployment, since this requires control over request headers independent of the signed body, which is outside the scope of the SDK's own test suite):
1. Attacker captures or otherwise obtains a validly-signed fulfillment-service callback body+HMAC for `shop-a.myshopify.com` (e.g., via a compromised/misconfigured intermediary that forwards Shopify webhooks and allows header rewriting, or a proxy bug).
2. Attacker replays the same body+HMAC to the app's fulfillment-service endpoint but sets `X-Shopify-Shop-Domain: shop-b.myshopify.com`.
3. `api.fulfillmentService.validate` in `packages/apps/shopify-api/lib/fulfillment-service/validate.ts` validates the HMAC against the (unchanged) body and returns `valid: true`.
4. `authenticateFulfillmentServiceFactory` reads `shop = 'shop-b.myshopify.com'` from the header [2](#0-1)  and returns an authenticated `admin` client for shop-b using shop-b's stored offline access token, even though the signed payload originated as shop-a's callback.

Note: I could not fully verify whether the fulfillment-service callback's signed JSON body also independently contains a shop domain field that downstream app code is expected to cross-check (the payload schema itself is opaque to the SDK), so the practical severity depends on how the app uses `payload` versus the returned `session`/`admin` client. This is flagged as uncertain due to the index not containing the full third-party payload schema documentation.

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

**File:** packages/apps/shopify-app-express/src/helpers/ensure-valid-offline-session.ts (L1-27)
```typescript
import {Session, ShopifyError} from '@shopify/shopify-api';

import {ApiAndConfigParams} from '../types';

import {ensureOfflineTokenIsNotExpired} from './ensure-offline-token-is-not-expired';
import {loadOfflineSession} from './load-offline-session';

export async function ensureValidOfflineSession(
  params: ApiAndConfigParams,
  shop: string,
): Promise<Session | undefined> {
  const {config} = params;

  if (!config.future?.expiringOfflineAccessTokens) {
    throw new ShopifyError(
      'ensureValidOfflineSession requires the `expiringOfflineAccessTokens` future flag to be enabled.',
    );
  }

  const session = await loadOfflineSession(params, shop);

  if (!session) return undefined;

  return ensureOfflineTokenIsNotExpired(params, session);
}


```
