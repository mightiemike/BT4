### Title
OAuth admin callback silently collapses duplicated `shop`/security query parameters, bypassing the "must not appear more than once" HMAC hardening - (File: packages/apps/shopify-api/lib/auth/oauth/oauth.ts)

### Summary
The bug class in the report is: a duplicate-detection check that relies on a sentinel/default value which can collide with a legitimately assigned value, silently allowing a duplicate entry to slip past validation. In `shopify-app-js`, the recently added app-proxy hardening (`normalizeQuery`) implements exactly this kind of duplicate-parameter defense for HMAC-signed security parameters, but the admin OAuth callback path builds its query object in a way that bypasses this defense entirely, using inconsistent representations of duplicated `shop` values between the value that is HMAC-validated and the value that is actually used for the token exchange.

### Finding Description
`normalizeQuery` in [1](#0-0)  is designed to reject repeated occurrences of security-sensitive parameters (`hmac`, `shop`, `signature`, `timestamp`) when the input is a `URLSearchParams` instance, by tracking whether a key has already been seen (`existingValue === undefined`) and throwing `InvalidHmacError` if a single-value param (per `APP_PROXY_SINGLE_VALUE_PARAMS`) is repeated: [2](#0-1) .

However, when `query` is **not** an instance of `URLSearchParams` (i.e., already a plain object), the function only checks `Array.isArray(query[key])` and only for `signator === 'appProxy'`, then just returns the query unchanged: [3](#0-2) . This branch performs no duplicate detection for the `admin` signator at all.

The admin OAuth callback in `oauth.ts` triggers exactly this weaker branch. It extracts `shop` via `URLSearchParams.get()` (which returns the **first** occurrence of a repeated key) for logging and, later, for the actual token exchange (`cleanShop`): [4](#0-3)  and [5](#0-4) . But it converts the same `URLSearchParams` to a plain object via `Object.fromEntries(query.entries())` before HMAC validation: [6](#0-5) . `Object.fromEntries` keeps the **last** occurrence of a duplicated key, silently discarding earlier ones — with no error, no array, and no throw. Because this object is no longer a `URLSearchParams` instance, `validateHmac`/`normalizeQuery` treats it as already-normalized and performs zero duplicate-parameter checking for the `admin` signator (`validQuery` calls `validateHmac(config)(query)` without specifying `signator`, defaulting to `'admin'`): [7](#0-6) .

The net effect: if a request to the OAuth callback contains a duplicated `shop` parameter, the value used to validate the HMAC (`authQuery.shop`, the **last** occurrence) can differ from the value used to build the shop that receives the token-exchange call and becomes `session.shop` (`query.get('shop')`, the **first** occurrence). This is the same root-cause pattern as the Saddle Finance bug: a "no duplicates" invariant that is supposed to hold is silently violated because of how the underlying data structure resolves ambiguity for repeated/default values, and different parts of the code observe different resolutions of that ambiguity.

### Impact Explanation
An attacker who can obtain one Shopify-issued, validly-HMAC-signed OAuth callback URL for a shop under their control (which any developer/merchant can trivially do by starting the OAuth flow for their own shop) can attempt to smuggle a second, victim `shop=` parameter ahead of their own in the query string. Because the HMAC is validated against the *last* `shop` value (their own, correctly signed shop) while `sanitizeShop`/`cleanShop`/session creation use the *first* `shop` value (the victim's), this creates a cross-tenant confusion primitive in the OAuth callback: HMAC validation "for shop A" is used to authorize actions performed "for shop B." Depending on downstream behavior (e.g., logging, session creation with a mismatched `shop`, or hooks keyed by `shop`), this can lead to session/state corruption or shop-confusion issues in the OAuth handshake. The severity is bounded by the fact that the subsequent token exchange itself requests a code against `cleanShop` (the victim/first shop) using a `code` value that Shopify issued for the other domain, so full offline-token theft for the victim shop is not directly achieved by this path alone — Shopify's own `/admin/oauth/access_token` endpoint should reject a code minted for one shop when redeemed against another domain. Still, the discrepancy between the value that passes security validation and the value used for subsequent state changes is a genuine defense bypass of the duplicate-parameter hardening that was explicitly added for security reasons (see changelog entry 857c598), and it undermines the invariant the fix was meant to guarantee for all HMAC signators.

### Likelihood Explanation
Reachability is straightforward: the OAuth callback endpoint is unauthenticated and processes attacker/merchant-controlled query strings directly from HTTP requests, exactly the kind of "anonymous HTTP request" surface called out in the validation rules. Constructing a request with a duplicated `shop=` query parameter requires no special access, only a valid signed callback URL for a shop the attacker controls (freely obtainable by installing the app themselves). The likelihood of the discrepancy actually manifesting as an exploitable cross-tenant issue depends on what downstream code does with the mismatched `shop`/`cleanShop`/`session.shop`, which I could not fully trace to a concrete session-hijack or token-disclosure outcome within the available context — this is the main source of uncertainty in this analog. The `normalizeQuery` array check for the non-`URLSearchParams` branch also appears effectively dead code for realistic inputs, since `Object.fromEntries` never produces arrays, reinforcing that the admin path has no working duplicate-parameter defense despite the appProxy path having one.

### Recommendation
- Do not convert the `URLSearchParams` query to a plain object via `Object.fromEntries` before HMAC validation in the OAuth callback. Instead, pass the `URLSearchParams` instance itself into `validateHmac`, so the existing duplicate-detection logic in `normalizeQuery` (currently appProxy-only) applies uniformly.
- Extend `APP_PROXY_SINGLE_VALUE_PARAMS`-style duplicate rejection to the `admin` signator as well, so that a repeated `shop`, `hmac`, `code`, `state`, or `timestamp` parameter causes `InvalidHmacError` regardless of signator.
- Ensure a single, consistent extraction of `shop` (e.g., always first occurrence, and reject duplicates outright) is used both for HMAC validation and for the token-exchange/session-creation logic, so the value that is "proven safe" is the same value that is subsequently trusted.
- Add tests asserting that a callback request with duplicate `shop` (or other single-value) parameters is rejected, mirroring the existing app-proxy duplicate-parameter tests in `hmac-validator.test.ts`.

### Proof of Concept
Conceptual PoC (not fully validated end-to-end due to the need for a live Shopify OAuth grant, hence flagged as uncertain in Likelihood):
1. Attacker installs the app on their own shop `attacker.myshopify.com` and captures the legitimate callback URL Shopify redirects to, e.g.:
   `https://app.example.com/auth/callback?code=...&hmac=<valid-for-attacker>&shop=attacker.myshopify.com&state=...&timestamp=...`
2. Attacker crafts a modified request by prepending a duplicate `shop` parameter for the victim before their own:
   `https://app.example.com/auth/callback?shop=victim.myshopify.com&code=...&hmac=<valid-for-attacker>&shop=attacker.myshopify.com&state=...&timestamp=...`
3. In `oauth.ts`, `query.get('shop')` (line 147) returns `victim.myshopify.com` (first occurrence), and this same first-occurrence value is used later for `cleanShop` (line 194) and thus for `session.shop`.
4. `authQuery = Object.fromEntries(query.entries())` (line 178) collapses the duplicate `shop` key to the **last** occurrence, `attacker.myshopify.com`, which is what `validateHmac` checks against the attacker's known-valid HMAC — so `validQuery` returns `true` even though the "effective" shop context (`victim.myshopify.com`) was never actually authorized by that HMAC.
5. Whether this leads to full compromise depends on whether the subsequent access-token exchange against `victim.myshopify.com`'s `/admin/oauth/access_token` endpoint accepts the attacker's `code` (expected to fail server-side on Shopify's end) and on how `session.shop`/logging are used downstream — this final step could not be verified against the live Shopify OAuth server behavior from the available code and is the primary residual uncertainty in this finding.

### Citations

**File:** packages/apps/shopify-api/lib/utils/hmac-validator.ts (L118-151)
```typescript
function normalizeQuery(query: HmacQuery, signator: HMACSignator): AuthQuery {
  if (!(query instanceof URLSearchParams)) {
    if (signator === 'appProxy') {
      for (const key of APP_PROXY_SINGLE_VALUE_PARAMS) {
        if (Array.isArray(query[key])) {
          throw new ShopifyErrors.InvalidHmacError(
            `Query parameter "${key}" must not appear more than once.`,
          );
        }
      }
    }

    return query;
  }

  const normalizedQuery = Object.create(null) as AuthQuery;
  for (const [key, value] of query.entries()) {
    const existingValue = normalizedQuery[key];
    if (existingValue === undefined) {
      normalizedQuery[key] = value;
    } else if (
      signator === 'appProxy' &&
      APP_PROXY_SINGLE_VALUE_PARAMS.has(key)
    ) {
      throw new ShopifyErrors.InvalidHmacError(
        `Query parameter "${key}" must not appear more than once.`,
      );
    } else {
      normalizedQuery[key] = `${existingValue},${value}`;
    }
  }

  return normalizedQuery;
}
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L143-147)
```typescript
    const query = new URL(
      request.url,
      `${config.hostScheme}://${config.hostName}`,
    ).searchParams;
    const shop = query.get('shop')!;
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L178-183)
```typescript
    const authQuery: AuthQuery = Object.fromEntries(query.entries());
    if (!(await validQuery({config, query: authQuery, stateFromCookie}))) {
      log.error('Invalid OAuth callback', {shop, stateFromCookie});

      throw new ShopifyErrors.InvalidOAuthError('Invalid OAuth callback.');
    }
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L194-194)
```typescript
    const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L242-255)
```typescript
async function validQuery({
  config,
  query,
  stateFromCookie,
}: {
  config: ConfigInterface;
  query: AuthQuery;
  stateFromCookie: string;
}): Promise<boolean> {
  return (
    (await validateHmac(config)(query)) &&
    safeCompare(query.state!, stateFromCookie)
  );
}
```
