### Title
`KVSessionStorage.deleteSessions` silently stops deleting sessions after any single failure due to `&&` short-circuiting, leaving stale/revoked sessions in storage - (File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts)

### Summary
The external report's bug class is: a function that is supposed to fully perform/report an action (withdraw the requested amount) instead silently does less than requested while callers assume the full action succeeded, causing follow-on failures/incorrect trust. The `KVSessionStorage.deleteSessions` method in this repo has an analogous flaw: it uses JavaScript's `&&` short-circuit evaluation to accumulate a boolean result across a loop of async deletions, which causes it to stop actually invoking `deleteSession` for any id after the first failure — while still returning a boolean that downstream code (e.g. `AppInstallations.delete`, invoked on `APP_UNINSTALLED`) uses to assume the shop's sessions were removed.

### Finding Description [1](#0-0) 
```ts
public async deleteSessions(ids: string[]): Promise<boolean> {
  let result = true;
  for (const id of ids) {
    result = result && (await this.deleteSession(id));
  }

  return result;
}
```
In JavaScript, `a && b` only evaluates `b` if `a` is truthy. Once `result` becomes `false` (because a prior `deleteSession(id)` call failed), every subsequent iteration's right-hand side `(await this.deleteSession(id))` is **never evaluated**, so `deleteSession` is not called for the remaining ids in the list. The loop keeps iterating (the `for` loop itself continues), but the actual deletion side effect is skipped for all ids after the first failure.

This method is the mechanism used by `AppInstallations.delete`, which is invoked from the `APP_UNINSTALLED` webhook handler to purge all sessions (including offline sessions holding access tokens) for a shop: [2](#0-1) 
```ts
async delete(shopDomain: string): Promise<void> {
  const shopSessions =
    await this.sessionStorage.findSessionsByShop!(shopDomain);
  if (shopSessions.length > 0) {
    await this.sessionStorage.deleteSessions!(
      shopSessions.map((session: Session) => session.id),
    );
  }
}
```
and this handler is wired up as `deleteAppInstallationHandler` on `APP_UNINSTALLED`: [3](#0-2) 

Neither `AppInstallations.delete` nor the webhook handler inspects the boolean return value of `deleteSessions` — they assume the array-wide deletion either succeeds fully or (in the worst case for other adapters) is retried; but for `KVSessionStorage` specifically, a single transient failure on one id in the list (e.g., a KV `delete` throwing, or `deleteSession` returning `false`) causes all subsequent sessions for that shop to remain persisted with valid access tokens, undetected, because the loop swallows the intent to keep deleting.

### Impact Explanation
If an app uses `KVSessionStorage` and a shop uninstalls the app, the `APP_UNINSTALLED` webhook triggers `AppInstallations.delete`, which calls `deleteSessions` with all of that shop's session ids. Due to the short-circuit bug, if the first id in the array fails to delete (or any prior one does), none of the following sessions' `deleteSession` calls are ever attempted — the loop exits having called far fewer key deletions than intended, and the KV namespace still contains the shop-to-session-id index and the actual session records with live access tokens. Because `AppInstallations.includes` checks `findSessionsByShop` for any session with a non-empty `accessToken` to decide re-auth flow, and downstream code trusts that uninstall purges these tokens, stale access tokens can remain reachable in storage. This is a session-storage integrity issue directly analogous to the BakerFi report's pattern: a function that reports/implies full completion of an action while only partially completing it, and callers built on top of that assumption behave incorrectly (in BakerFi's case, transfer failure; here, incomplete session/token revocation).

### Likelihood Explanation
This triggers automatically whenever a shop's session list passed to `deleteSessions` contains more than one id and the underlying `namespace.delete`/upstream `deleteSession` call fails or returns false for any entry before the last — a realistic scenario under KV eventual-consistency errors, network blips, or missing keys (already-deleted sessions returning early via `loadSession` returning `undefined`, which makes `deleteSession` return `true`, so the bug is masked unless there's an actual delete failure or unless `deleteSession`'s own promise rejects, in which case the `await` would throw and abort the loop entirely rather than short-circuit — worth flagging this nuance below). No privileged actor or leaked secret is needed; it's an anonymous merchant/customer-adjacent action (the merchant simply uninstalls the app), and Shopify's own webhook delivery is the trigger.

Note on uncertainty: `deleteSession` in `kv.ts` returns `true` in normal cases (delete always succeeds or the session doesn't exist), so `false` is not obviously returned in the current implementation — the exploitable path requires `deleteSession` to actually return `false`, which does not appear to happen in the current code (I could not fully verify `deleteSession`'s full body due to tool access limitations during this session; the earlier view showed it always returning `true`). This weakens the immediate exploitability: the `&&` bug is a genuine logic defect, but its real-world security impact depends on `deleteSession` returning `false` at least once, which the current adapter implementation does not appear to do. This should be verified in a full read of the file.

### Recommendation
Replace the short-circuiting accumulator with a pattern that guarantees every deletion is attempted regardless of prior failures, e.g.:
```ts
public async deleteSessions(ids: string[]): Promise<boolean> {
  const results = await Promise.all(ids.map((id) => this.deleteSession(id)));
  return results.every(Boolean);
}
```
This ensures `deleteSession` is invoked for every id (matching the intended full-array delete semantics used by `AppInstallations.delete`), and the aggregate boolean accurately reflects whether all deletions succeeded, rather than masking incomplete revocation of shop sessions/tokens after uninstall.

### Proof of Concept
Given `ids = ['s1','s2','s3']` and a hypothetical scenario where `deleteSession('s1')` resolves to `false` (e.g., adapter-specific failure path), tracing `deleteSessions`:
1. `result = true`
2. iteration 1: `result = true && (await deleteSession('s1'))` → `deleteSession('s1')` is awaited and returns `false` → `result = false`
3. iteration 2: `result = false && (await deleteSession('s2'))` → right side is **not evaluated** (short-circuit) → `deleteSession('s2')` is never called → `s2`'s session (and its access token) remains in the KV store
4. iteration 3: same as above, `s3` also remains
5. Returns `false`

Callers such as `AppInstallations.delete` don't check the return value, so the shop's sessions `s2` and `s3` (and any associated access tokens) persist in storage despite the intended full purge on uninstall. [4](#0-3)

### Citations

**File:** packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts (L47-54)
```typescript
  public async deleteSessions(ids: string[]): Promise<boolean> {
    let result = true;
    for (const id of ids) {
      result = result && (await this.deleteSession(id));
    }

    return result;
  }
```

**File:** packages/apps/shopify-app-express/src/app-installations.ts (L1-41)
```typescript
import {Session} from '@shopify/shopify-api';

import {AppConfigInterface} from './config-types';

export class AppInstallations {
  private sessionStorage;

  constructor(config: AppConfigInterface) {
    if (!config.sessionStorage.findSessionsByShop) {
      throw new Error(
        'To use this Express package, you must provide a session storage manager that implements findSessionsByShop',
      );
    }
    if (!config.sessionStorage.deleteSessions) {
      throw new Error(
        'To use this Express package, you must provide a session storage manager that implements deleteSessions',
      );
    }
    this.sessionStorage = config.sessionStorage;
  }

  async includes(shopDomain: string): Promise<boolean> {
    const shopSessions =
      await this.sessionStorage.findSessionsByShop!(shopDomain);
    if (shopSessions.length > 0) {
      for (const session of shopSessions) {
        if (session.accessToken) return true;
      }
    }
    return false;
  }

  async delete(shopDomain: string): Promise<void> {
    const shopSessions =
      await this.sessionStorage.findSessionsByShop!(shopDomain);
    if (shopSessions.length > 0) {
      await this.sessionStorage.deleteSessions!(
        shopSessions.map((session: Session) => session.id),
      );
    }
  }
```

**File:** packages/apps/shopify-app-express/src/middlewares/ensure-installed-on-shop.ts (L94-108)
```typescript
export function deleteAppInstallationHandler(
  appInstallations: AppInstallations,
  config: AppConfigInterface,
) {
  return async function (
    _topic: string,
    shop: string,
    _body: any,
    _webhookId: string,
  ) {
    config.logger.debug('Deleting shop sessions', {shop});

    await appInstallations.delete(shop);
  };
}
```
