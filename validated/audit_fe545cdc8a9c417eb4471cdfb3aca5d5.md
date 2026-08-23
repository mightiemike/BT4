### Title
Fulfillment service webhook authenticator trusts an HMAC-unauthenticated `X-Shopify-Shop-Domain` header to select the offline session, enabling cross-tenant admin session reuse via signed-body replay - ([File: packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts])

### Summary
Analogous to the Union Finance bug (a privileged accounting update keyed off the wrong identity — `borrower` instead of `staker`), `authenticateFulfillmentServiceFactory` validates only the raw request *body* with HMAC, then reads the *shop identity* from a header that is never covered by that signature, and uses that unauthenticated header value to fetch and hand back another tenant's offline `Session`/admin client.

### Finding Description
`authenticateFulfillmentServiceFactory` computes validity via `api.fulfillmentService.validate({rawBody, rawRequest: request})`, which under the hood (`validateHmacFromRequestFactory` in `packages/apps/shopify-api/lib/utils/hmac-validator.ts`) only HMACs `rawBody`: [1](#0-0) 
No header, including the shop domain header, is part of the signed material.

After validation succeeds, the handler picks the shop identity straight from the unauthenticated header and uses it to fetch the offline session that becomes the returned `admin` API client: [2](#0-1) 

Just as `repayBorrow()` called `updateFrozenInfo(borrower, 0)` — using the wrong subject for a sensitive state read/update instead of the actually-affected `staker` — this handler authenticates the *request body* but then keys a sensitive lookup (which tenant's offline access token to hand back) off a *different, unverified* field (`shop`), rather than binding shop identity into the signed content or otherwise cross-checking it.

### Impact Explanation
Since `shop` is not bound into the HMAC, a party who possesses one validly-signed fulfillment-service request body sent to their own endpoint (e.g. captured from their own webhook traffic/logs) can resend that exact body with an attacker-chosen `X-Shopify-Shop-Domain` header value. The signature will still validate (it only checks the body), and `ensureValidOfflineSession(params, shop)` will load and return the *offline session* (and thus an authenticated `admin` GraphQL/REST client) for whatever shop domain the attacker put in the header — not necessarily the shop that actually sent the payload. This is a cross-tenant session/API-client exposure: the requester obtains a working admin client for a shop that isn't theirs, using only a replayed signed payload plus a spoofed header.

### Likelihood Explanation
Requires the attacker to already have one legitimate signed body (e.g. from their own app installation's fulfillment-service traffic) and knowledge/guess of a target shop's domain — both realistic for a "single merchant" actor as allowed by the validation rules. No secret key or MITM position is needed; the header is simply never authenticated, so replay + header substitution is a direct network request from the attacker's own client.

### Recommendation
Bind the shop identity into the authenticated material for fulfillment-service (and any other webhook-style) requests — e.g. include the domain header in the HMAC input, or independently corroborate the header value against a shop identifier embedded in the signed payload — before using it to select which tenant's offline session/admin client is returned.

### Proof of Concept
1. Attacker's own app instance for `attacker-shop.myshopify.com` receives (or the attacker otherwise obtains) a valid fulfillment-service POST with body `B` and header `X-Shopify-Hmac-Sha256: H`, where `H = HMAC(secret, B)`.
2. Attacker sends `POST /webhooks/fulfillment-service` with the same body `B`, same header `X-Shopify-Hmac-Sha256: H`, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
3. `api.fulfillmentService.validate` returns `valid: true` because only `B`/`H` are checked (`packages/apps/shopify-api/lib/utils/hmac-validator.ts:185-197`).
4. `authenticate()` reads `shop = 'victim-shop.myshopify.com'` from the header and calls `ensureValidOfflineSession(params, shop)`, returning `victim-shop`'s offline session and an authenticated `admin` client to the attacker (`packages/apps/shopify-app-remix/src/server/authenticate/fulfillment-service/authenticate.ts:51-79`).

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
