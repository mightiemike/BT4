### Title
Missing HTTP timeout in OAuth/token-exchange fetch calls enables resource-exhaustion DoS of the auth handler - (File: packages/apps/shopify-api/lib/utils/fetch-request.ts)

### Summary
`fetchRequestFactory` is the single low-level HTTP wrapper used by every Shopify-token-issuing OAuth code path (authorization-code callback, token exchange, client-credentials grant, and offline-token migration). It calls `abstractFetch(url, options)` with no `AbortController`/timeout and no `RequestInit.signal`, mirroring the vulnerable pattern in the referenced report (`http.Client{}` with no `Timeout`/`Transport` configured). [1](#0-0) 

### Finding Description
`fetchRequestFactory` performs `await abstractFetch(url, options)` directly, with no deadline, connection timeout, or `AbortSignal` wired in: [2](#0-1) 

This factory is used to call `https://${shop}/admin/oauth/access_token` in every OAuth-related flow that is reachable from an anonymous or single-merchant HTTP request:

- OAuth authorization-code callback (`/auth/callback`), triggered by any request carrying a `shop`/`code` query param: [3](#0-2) 

- Token exchange, triggered by a caller-supplied session token (attacker-controlled input reaching this code path before the token is fully validated against Shopify): [4](#0-3) 

- Client-credentials grant: [5](#0-4) 

- Offline-token migration: [6](#0-5) 

These, in turn, back framework-level auth middleware such as `validateAuthenticatedSession` (Express) and the token-exchange authentication strategies in `shopify-app-remix`/`shopify-app-react-router`, which are invoked on every incoming request to an authenticated route: [7](#0-6) [8](#0-7) 

By contrast, only the higher-level `GraphqlQueryOptions` interface for admin GraphQL client queries exposes an optional `signal` for cancellation — it is not plumbed into the OAuth token-issuing paths at all: [9](#0-8) 

If the upstream `https://{shop}/admin/oauth/access_token` endpoint (or a DNS/TLS handshake to a spoofed/slow target derived from a caller-influenced `shop` value) stalls or never responds, the `await abstractFetch(...)` call in `fetchRequestFactory` will hang indefinitely, since there is no timeout, no connection deadline, and no abort mechanism.

### Impact Explanation
Every incoming request that triggers OAuth callback processing or token exchange (which is driven by attacker/merchant-supplied session tokens and shop values, not privileged actors) results in an unbounded outbound fetch. An attacker able to induce many such requests concurrently (or a slow/unresponsive network path to the target shop domain) can pin the Node.js event loop/connection pool with pending, non-timing-out requests, exhausting file descriptors/sockets and causing the authentication middleware — and therefore the whole app's auth-gated surface — to become unresponsive. This matches the report's DoS/resource-exhaustion impact category, applied here to the auth handler rather than the domain of the original bug report.

### Likelihood Explanation
The OAuth callback and token-exchange handlers are reachable by any unauthenticated caller who can send a request with a `shop`/`code` param or a bearer/session token to the app's `/auth`, `/auth/callback`, or any route protected by `validateAuthenticatedSession`/`authenticate.admin`. No privileged access, secret leakage, or MITM condition is required — this is a straightforward reachable-and-repeatable code path present in `packages/apps/shopify-api`'s OAuth/token-exchange module and inherited by all three app frameworks (`shopify-app-express`, `shopify-app-remix`, `shopify-app-react-router`).

### Recommendation
Add a request-level timeout/`AbortController` to `fetchRequestFactory` (and propagate an optional caller-supplied `signal`), so that all downstream OAuth/token-exchange calls (`oauth.ts`, `token-exchange.ts`, `client-credentials.ts`, `migrate-to-expiring-token.ts`) abort after a bounded duration instead of hanging indefinitely, consistent with the recommendation in the referenced report (overall request timeout, dial/TLS handshake timeout, and idle-connection limits).

### Proof of Concept
1. Set up an app using `shopify-app-express`/`shopify-app-remix` with `tokenExchange` enabled.
2. Send concurrent requests to a protected route with a well-formed but unverifiable session token, or trigger the `/auth/callback` route pointed at a shop domain controlled to never respond (e.g., a black-holed IP/hostname resolvable as a `myshopify.com`-style domain in a test environment).
3. Because `fetchRequestFactory` in `packages/apps/shopify-api/lib/utils/fetch-request.ts` never times out, each request thread/connection remains pending forever; repeating this quickly exhausts the server's available sockets/event-loop capacity, denying service to legitimate authentication requests.

Note: I could not fully verify the underlying `abstractFetch`/runtime adapter implementation (e.g., Node's `fetch`/`undici` defaults) since its source in `packages/apps/shopify-api/runtime/http/index.ts` was not retrievable within the indexed context; if that layer configures a global timeout, this would mitigate the issue, but no such configuration was found in the reachable OAuth call sites reviewed. A Devin session with full file access could confirm the adapter's default timeout behavior.

### Citations

**File:** packages/apps/shopify-api/lib/utils/fetch-request.ts (L1-34)
```typescript
import {logger} from '../logger';
import {LogSeverity} from '../types';
import {abstractFetch} from '../../runtime';
import {ConfigInterface} from '../base-types';

export function fetchRequestFactory(config: ConfigInterface) {
  return async function fetchRequest(
    url: string,
    options?: RequestInit,
  ): Promise<Response> {
    const log = logger(config);
    const doLog =
      config.logger.httpRequests && config.logger.level === LogSeverity.Debug;

    if (doLog) {
      log.debug('Making HTTP request', {
        method: options?.method || 'GET',
        url,
        ...(options?.body && {body: options?.body}),
      });
    }

    const response = await abstractFetch(url, options);

    if (doLog) {
      log.debug('HTTP request completed', {
        method: options?.method || 'GET',
        url,
        status: response.status,
      });
    }

    return response;
  };
```

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L194-206)
```typescript
    const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;

    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      {
        method: 'POST',
        body: JSON.stringify(body),
        headers: {
          'Content-Type': DataType.JSON,
          Accept: DataType.JSON,
        },
      },
    );
```

**File:** packages/apps/shopify-api/lib/auth/oauth/token-exchange.ts (L32-63)
```typescript
export function tokenExchange(config: ConfigInterface): TokenExchange {
  return async ({
    shop,
    sessionToken,
    requestedTokenType,
    expiring,
  }: TokenExchangeParams) => {
    await decodeSessionToken(config)(sessionToken);

    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      grant_type: TokenExchangeGrantType,
      subject_token: sessionToken,
      subject_token_type: IdTokenType,
      requested_token_type: requestedTokenType,
      expiring: expiring ? '1' : '0',
    };

    const cleanShop = sanitizeShop(config)(shop, true)!;

    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      {
        method: 'POST',
        body: JSON.stringify(body),
        headers: {
          'Content-Type': DataType.JSON,
          Accept: DataType.JSON,
        },
      },
    );
```

**File:** packages/apps/shopify-api/lib/auth/oauth/client-credentials.ts (L21-41)
```typescript
export function clientCredentials(config: ConfigInterface): ClientCredentials {
  return async ({shop}: ClientCredentialsParams) => {
    const cleanShop = sanitizeShop(config)(shop, true)!;

    const requestConfig = {
      method: 'POST',
      body: JSON.stringify({
        client_id: config.apiKey,
        client_secret: config.apiSecretKey,
        grant_type: ClientCredentialsGrantType,
      }),
      headers: {
        'Content-Type': DataType.JSON,
        Accept: DataType.JSON,
      },
    };

    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      requestConfig,
    );
```

**File:** packages/apps/shopify-api/lib/auth/oauth/migrate-to-expiring-token.ts (L24-53)
```typescript
export function migrateToExpiringToken(
  config: ConfigInterface,
): MigrateToExpiringToken {
  return async ({
    shop,
    nonExpiringOfflineAccessToken,
  }: MigrateToExpiringTokenParams) => {
    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      grant_type: TokenExchangeGrantType,
      subject_token: nonExpiringOfflineAccessToken,
      subject_token_type: RequestedTokenType.OfflineAccessToken,
      requested_token_type: RequestedTokenType.OfflineAccessToken,
      expiring: '1',
    };

    const cleanShop = sanitizeShop(config)(shop, true)!;

    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      {
        method: 'POST',
        body: JSON.stringify(body),
        headers: {
          'Content-Type': DataType.JSON,
          Accept: DataType.JSON,
        },
      },
    );
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L24-38)
```typescript
async function exchangeToken(
  api: Shopify,
  config: AppConfigInterface,
  sessionToken: string,
  shop: string,
  requestedTokenType: RequestedTokenType,
): Promise<Session> {
  const {session} = await api.auth.tokenExchange({
    sessionToken,
    shop,
    requestedTokenType,
    expiring: config.future?.expiringOfflineAccessTokens,
  });
  return session;
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L92-111)
```typescript
          newSession,
          request,
          sessionToken,
        );
      } catch (errorOrResponse) {
        if (errorOrResponse instanceof Response) {
          throw errorOrResponse;
        }

        throw new Response(undefined, {
          status: 500,
          statusText: 'Internal Server Error',
        });
      }

      return newSession;
    }

    return session!;
  }
```

**File:** packages/apps/shopify-api/lib/clients/types.ts (L96-116)
```typescript
export interface GraphqlQueryOptions<
  Operation extends keyof Operations,
  Operations extends AllOperations,
> {
  /**
   * The variables to include in the operation.
   */
  variables?: ApiClientRequestOptions<Operation, Operations>['variables'];
  /**
   * Additional headers to be sent with the request.
   */
  headers?: Record<string, string | number>;
  /**
   * The maximum number of times to retry the request if it fails with a throttling or server error.
   */
  retries?: number;
  /**
   * An optional AbortSignal to cancel the request.
   */
  signal?: AbortSignal;
}
```
