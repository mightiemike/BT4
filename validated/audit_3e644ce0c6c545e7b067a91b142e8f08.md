This is exactly the pattern I found: `invalidateAccessToken` in both `packages/apps/shopify-app-remix/src/server/authenticate/helpers/invalidate-access-token.ts` and `packages/apps/shopify-app-express/src/helpers/invalidate-access-token.ts` calls `config.sessionStorage.storeSession(session)` after clearing `session.accessToken`, but never checks the boolean return value that indicates whether the write actually succeeded.

### Title
Unchecked `storeSession()` return value when invalidating access tokens allows a stale/compromised access token to remain usable - (File: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/invalidate-access-token.ts`, `packages/apps/shopify-app-express/src/helpers/invalidate-access-token.ts`)

### Summary
`SessionStorage.storeSession()` is documented to return `Promise<boolean>`, `true` if the write succeeded and `false` otherwise, as stated explicitly in `packages/apps/session-storage/shopify-app-session-storage/implementing-session-storage.md:7`. `invalidateAccessToken()` relies on this write to actually persist the access-token removal, but discards the returned boolean: [1](#0-0) [2](#0-1) 

### Finding Description
Both helpers set `session.accessToken = undefined` in memory and call `await config.sessionStorage.storeSession(session)`, treating the operation as fire-and-forget. If the underlying storage adapter cannot persist the write (lock contention, connection drop, unique-constraint conflict, driver error swallowed and converted to `false`, etc.) it returns `false` per the interface contract, but the caller never inspects this, logs nothing, and never retries or surfaces an error. Every stock adapter shipped in this repo (`Redis`, `MongoDB`, `MySQL`, `PostgreSQL`, `SQLite`, `Prisma`, `KV`, `DynamoDB`, `Memory`) implements `storeSession` to signal failure via this same boolean channel rather than by throwing, per `packages/apps/session-storage/shopify-app-session-storage/src/types.ts:6-12`. This mirrors the H-01 analog precisely: a call whose failure is communicated only through a return value, but the return value is unconditionally ignored by the caller, so the caller proceeds as if the sensitive state change (revoking the access token) had taken effect.

`invalidateAccessToken` is invoked from the token-refresh/expiry-handling and re-authentication error paths (e.g., `ensureOfflineTokenIsNotExpired`/`refreshToken` failure handling and OAuth-callback error handling), i.e. code paths reachable during normal request handling for a shop's stored session, not requiring any privileged actor.

### Impact Explanation
If the storage write silently fails, the in-memory `session.accessToken = undefined` mutation is lost as soon as the request completes; the underlying stored session record still contains the old (potentially revoked-on-Shopify's-side, rotated, or otherwise stale/compromised) access token. Subsequent `loadSession()` calls will keep returning the *original*, non-invalidated access token, defeating the security purpose of the invalidation call and allowing continued use of a token that the app explicitly tried to revoke from its own storage.

### Likelihood Explanation
Requires a transient storage failure (network blip, DB unavailability, write conflict) to occur exactly during this specific write — the same class of "storage/backend hiccup" needed for any unchecked-write bug. It is not attacker-triggerable directly, but it is a real, reachable failure mode of the session storage abstraction, and the framework provides no logging or fallback (e.g., retry, alert, or forced session deletion) when this specific write fails, unlike some other spots in the codebase that do check the boolean (see `packages/apps/shopify-app-express/src/middlewares/__tests__/*` and `docs/example-migration-v5-node-template-to-v6.md:247-249`, which explicitly logs when `storeSession` returns `false`).

### Recommendation
Check the boolean returned by `storeSession()` in `invalidateAccessToken()` in both packages; on `false`, log the failure and either retry the write or fall back to `deleteSession()` (which is also unchecked in several other locations) to guarantee the compromised/expired token cannot be reused from storage.

### Proof of Concept
1. Configure a `SessionStorage` implementation whose backend is temporarily unavailable/returns `false` from `storeSession` (e.g., a custom or flaky adapter, or any of the bundled adapters during a connection failure that the adapter maps to `false` instead of throwing).
2. Trigger a path that calls `invalidateAccessToken` (e.g., a failed token refresh in `ensureOfflineTokenIsNotExpired` for shopify-app-remix, or the equivalent express flow).
3. Observe that `storeSession` returns `false`, but `invalidateAccessToken` returns normally with no error and no log entry.
4. On the next request, `loadSession(session.id)` still returns the session object containing the original `accessToken`, which the app will continue to use for Admin API calls, showing the invalidation had no effect.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/invalidate-access-token.ts (L1-17)
```typescript
import {Session} from '@shopify/shopify-api';

import type {BasicParams} from '../../types';

export async function invalidateAccessToken(
  params: BasicParams,
  session: Session,
): Promise<void> {
  const {logger, config} = params;

  logger.debug(`Invalidating access token for session - ${session.id}`, {
    shop: session.shop,
  });

  session.accessToken = undefined;
  await config.sessionStorage!.storeSession(session);
}
```

**File:** packages/apps/shopify-app-express/src/helpers/invalidate-access-token.ts (L1-11)
```typescript
import {Session} from '@shopify/shopify-api';

import {AppConfigInterface} from '../config-types';

export async function invalidateAccessToken(
  session: Session,
  config: AppConfigInterface,
): Promise<void> {
  config.logger.debug('Invalidating stale access token', {shop: session.shop});
  session.accessToken = undefined;
  await config.sessionStorage.storeSession(session);
```
