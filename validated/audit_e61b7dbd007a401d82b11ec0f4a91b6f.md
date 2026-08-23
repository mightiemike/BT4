Note: this analysis is against `shopify-app-express`'s `validateAuthenticatedSession` middleware (Auth Code flow branch), which is the closest analog to the reported bug class — a legitimate, already-authenticated actor can trigger cheap, repeated client requests that each force an expensive uncached outbound call to a third-party service (Shopify's Admin API), with no rate limiting or caching, similarly to how the oracle callback allowed cheap repeated triggering of oracle-node work.

### Title
Uncached, per-request live Admin API call in `hasValidAccessToken` allows cheap DoS of the session-validation auth handler - (File: `packages/apps/shopify-app-express/src/middlewares/has-valid-access-token.ts`)

### Summary
`validateAuthenticatedSession`'s Auth Code flow branch calls `hasValidAccessToken(api, session)` on **every single request** to any protected route, even when the stored session is already marked `isActive`. This function performs a live GraphQL query against the shop's Admin API on every call, with no caching, memoization, or minimum-interval throttling. A single authenticated user of the embedded app (a "legitimate" holder of a valid session/cookie) can flood any protected endpoint with rapid requests, forcing the app server to make an outbound Admin API call per request.

### Finding Description [1](#0-0) 
Once a session is loaded and found `isActive`, the middleware unconditionally calls `hasValidAccessToken` before proceeding, regardless of how recently that check last succeeded: [2](#0-1) 
`hasValidAccessToken` builds a fresh `api.clients.Graphql` client and issues a live `shop { name }` query to Shopify's Admin API on every invocation — there is no result caching, no debounce, and no backoff. Because this check runs per HTTP request (not per session lifetime), a client that repeatedly hits any `validateAuthenticatedSession`-protected route (e.g., a buggy or malicious polling loop from the embedded iframe, which requires nothing more than the app's own valid session cookie/JWT — something any ordinary merchant/staff user already possesses) forces one outbound Admin GraphQL call per request, with no attacker cost beyond normal HTTP requests to their own app. This directly parallels the reported bug class: a cheap, repeatable client action forces the server to burn a limited, rate-capped external resource (there, oracle nodes; here, the shop's Admin API rate-limit bucket) on every attempt.

Additionally, any error other than a 401 (e.g., a `429` throttled response from Shopify, or any transient network failure) is caught in the outer handler and treated as "invalid" (`hasValidToken = false`) rather than differentiated from a genuinely invalid token: [3](#0-2) 
This means once the shop's Admin API rate limit is exhausted by the flood, subsequent legitimate requests through this same auth handler will also fail the check and fall through to `redirectOutOfApp`, forcing the app to bounce every request into the OAuth re-authentication flow — effectively a self-inflicted denial of service of the authentication handler for that shop, triggered purely by request volume rather than any privileged/administrative action.

### Impact Explanation
Every protected page load consumes one unit of the shop's Admin API rate-limit bucket merely to validate a session that was already marked active. A user (or a script running in their browser context, e.g. via a compromised extension, aggressive client polling, or intentional self-abuse to test their own app) can drive this middleware at will, exhausting the shop's Admin API call budget with no legitimate GraphQL work performed, and — due to the fail-closed error handling — collapse the auth handler into a redirect loop for that shop for the duration of the flood.

### Likelihood Explanation
Likelihood is moderate-to-high: no special crafting, secret knowledge, or elevated privilege is required — only the ability to issue many HTTP requests to a protected route while holding a normal, valid session (which any embedded-app user already has). This is trivially automatable from client-side JS already running in the merchant's browser session.

### Recommendation
- Cache the result of `hasValidAccessToken` for a short TTL (or track "last verified at" on the `Session` object) so the live Admin API check is not repeated on every request.
- Distinguish transient/rate-limit errors (e.g., 429, 5xx) from genuine 401s in `hasValidAccessToken`'s error handling, and avoid treating them as "invalid session" (which currently forces a redirect to OAuth).
- Consider validating access-token freshness lazily (e.g., only when the client makes an actual Admin API call inside the route handler) rather than probing with a dedicated test query on every request.

### Proof of Concept
1. Authenticate normally as an embedded-app merchant/staff user, obtaining a valid session cookie/JWT for a route protected by `shopify.validateAuthenticatedSession()`.
2. From the browser (or any script holding that session), fire a burst of GET requests to that route (e.g., hundreds per second).
3. Observe that each request triggers a live `shop { name }` GraphQL call in `hasValidAccessToken` — confirm via server logs/network capture that call volume scales 1:1 with request volume rather than being cached.
4. Continue until the shop's Admin API rate limit is exhausted; observe subsequent legitimate requests through the same middleware now fail the `hasValidAccessToken` check (due to 429s being treated as invalid) and get redirected into the OAuth flow, denying normal use of the app for that shop.

### Citations

**File:** packages/apps/shopify-app-express/src/middlewares/validate-authenticated-session.ts (L131-163)
```typescript
  if (session) {
    config.logger.debug('Request session found and loaded', {
      shop: session.shop,
    });

    if (session.isActive(api.config.scopes)) {
      config.logger.debug('Request session exists and is active', {
        shop: session.shop,
      });

      let hasValidToken: boolean;
      try {
        hasValidToken = await hasValidAccessToken(api, session);
      } catch (error) {
        config.logger.error(`Could not check if session was valid: ${error}`, {
          shop: session.shop,
        });
        hasValidToken = false;
      }

      if (hasValidToken) {
        config.logger.debug('Request session has a valid access token', {
          shop: session.shop,
        });

        res.locals.shopify = {
          ...res.locals.shopify,
          session,
        };
        return next();
      }
    }
  }
```

**File:** packages/apps/shopify-app-express/src/middlewares/has-valid-access-token.ts (L9-25)
```typescript
export async function hasValidAccessToken(
  api: Shopify,
  session: Session,
): Promise<boolean> {
  try {
    const client = new api.clients.Graphql({session});
    await client.request(TEST_GRAPHQL_QUERY);
    return true;
  } catch (error) {
    if (error instanceof HttpResponseError && error.response.code === 401) {
      // Re-authenticate if we get a 401 response
      return false;
    } else {
      throw error;
    }
  }
}
```
