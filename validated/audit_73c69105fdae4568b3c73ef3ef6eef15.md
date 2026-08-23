### Title
`handleInvalidOfflineSession` silently swallows unrecognized errors, allowing an embedded app to load despite a failed offline-session validation - (File: `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts`)

### Summary
`AuthCodeFlowStrategy.ensureInstalledOnShop` validates that an offline session is still usable before embedding the app by calling `testSession`, wrapped in a try/catch that delegates error handling to `handleInvalidOfflineSession`. That handler only has explicit branches for `error instanceof HttpResponseError` and `error instanceof GraphqlQueryError`; any other exception type falls through and the function returns normally without throwing a `Response` or redirecting to OAuth. [1](#0-0) [2](#0-1) 

### Finding Description
This mirrors the reported bug class: a try/catch construct that only recognizes a narrow set of error shapes (`Error(string)` in Solidity vs. `instanceof HttpResponseError` / `instanceof GraphqlQueryError` here) and treats every other error as unhandled, letting execution proceed as if nothing went wrong.

In `ensureInstalledOnShop`, when `config.isEmbeddedApp && !isEmbedded`, the code calls `await this.testSession(offlineSession)` inside a try block, and on failure calls `await this.handleInvalidOfflineSession(error, request, shop)` without `throw`: [1](#0-0) 

`testSession` performs a live GraphQL request against the shop: [3](#0-2) 

`handleInvalidOfflineSession` only reacts to `HttpResponseError` (redirects to OAuth on 401, or returns an error `Response` for other codes) and `GraphqlQueryError` (returns a 500 `Response`). If the underlying HTTP client, network stack, JSON parsing, or any other library layer throws a different exception type (e.g., a timeout error, `TypeError`, or any error class not matching those two `instanceof` checks), neither branch executes, and the method returns `undefined` instead of throwing: [4](#0-3) 

Because the caller does not `throw` and does not otherwise check the return value, `ensureInstalledOnShop` (and consequently `respondToOAuthRequests`) completes normally, which means the request handling continues as though the offline session validation succeeded — effectively embedding the app / continuing the flow with a session that could not be confirmed valid.

### Impact Explanation
This is analogous to the referenced Medium-severity finding: the intent of `testSession` is a security check ("ensure the offline access token is still valid before letting an already-embedded/authenticated flow continue"), and a narrow catch means an error class outside the two `instanceof` checks is effectively ignored rather than causing a safe fallback (redirect to OAuth or error response). The result is that the app may proceed to serve embedded content or continue as "installed"/authenticated despite the session-validation call having failed for a reason the code didn't anticipate, rather than forcing re-authentication or surfacing an error. This does not itself forge credentials, but it undermines a defense-in-depth session-liveness check in the authenticated request path.

### Likelihood Explanation
Reachability requires only an unauthenticated top-level document request to an embedded app while `config.isEmbeddedApp && !isEmbedded`, i.e., a normal request flow, no privileged actor needed. Triggering the "other" error branch requires the underlying GraphQL client to throw something other than `HttpResponseError`/`GraphqlQueryError` (e.g., network/timeout/parsing failures), which is plausible but not something I can prove occurs in the exact `api.clients.Graphql` implementation from the available index — the concrete class hierarchy of client-thrown errors could not be fully verified within tool limits, so likelihood is uncertain/best assessed as low-to-medium.

### Recommendation
Add a default/fallback branch in `handleInvalidOfflineSession` that throws a `Response` (e.g., 500 Internal Server Error) or otherwise redirects to OAuth for any error not matching `HttpResponseError` or `GraphqlQueryError`, and ensure the caller in `ensureInstalledOnShop` always throws/returns the result of `handleInvalidOfflineSession` rather than allowing execution to fall through silently.

### Proof of Concept
1. Configure an embedded app (`isEmbeddedApp: true`) with a stored, seemingly-active offline session.
2. Make `testSession`'s underlying `client.request` throw an error that is neither `HttpResponseError` nor `GraphqlQueryError` (e.g., mock a `TypeError` or a generic `Error` from the HTTP layer, simulating a network failure or unexpected client exception).
3. Send a non-embedded document GET request without a session token header so `ensureInstalledOnShop` is invoked.
4. Observe that `handleInvalidOfflineSession` returns without throwing, and `respondToOAuthRequests`/`ensureInstalledOnShop` completes normally, allowing the app to proceed as if the offline session were valid instead of redirecting to OAuth or returning an error response. [1](#0-0) [2](#0-1)

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L149-160)
```typescript
    if (config.isEmbeddedApp && !isEmbedded) {
      try {
        logger.debug('Ensuring offline session is valid before embedding', {
          shop,
        });
        await this.testSession(offlineSession);

        logger.debug('Offline session is still valid, embedding app', {shop});
      } catch (error) {
        await this.handleInvalidOfflineSession(error, request, shop);
      }
    }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L248-262)
```typescript
  private async testSession(session: Session): Promise<void> {
    const {api} = this;

    const client = new api.clients.Graphql({
      session,
    });

    await client.request(`#graphql
      query shopifyAppShopName {
        shop {
          name
        }
      }
    `);
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts (L292-331)
```typescript
  private async handleInvalidOfflineSession(
    error: Error,
    request: Request,
    shop: string,
  ) {
    const {api, logger, config} = this;
    if (error instanceof HttpResponseError) {
      if (error.response.code === 401) {
        logger.info('Shop session is no longer valid, redirecting to OAuth', {
          shop,
        });
        throw await beginAuth({api, config, logger}, request, false, shop);
      } else {
        const message = JSON.stringify(error.response.body, null, 2);
        logger.error(`Unexpected error during session validation: ${message}`, {
          shop,
        });

        throw new Response(undefined, {
          status: error.response.code,
          statusText: error.response.statusText,
        });
      }
    } else if (error instanceof GraphqlQueryError) {
      const context: Record<string, string> = {shop};
      if (error.response) {
        context.response = JSON.stringify(error.body);
      }

      logger.error(
        `Unexpected error during session validation: ${error.message}`,
        context,
      );

      throw new Response(undefined, {
        status: 500,
        statusText: 'Internal Server Error',
      });
    }
  }
```
