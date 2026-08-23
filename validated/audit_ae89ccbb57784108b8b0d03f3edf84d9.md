### Title
Unbounded, non-deduplicated shop→session-id list in `KVSessionStorage` enables session-store DoS - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.addShopIds()` appends a session id to the `shop:<shop>` KV list on every `storeSession()` call without checking for duplicates or enforcing any size limit. Several normal, single-merchant-reachable auth flows (offline token refresh, access-token invalidation on a `401`) repeatedly call `storeSession()` for the *same, deterministic* offline session id (`offline_<shop>`), so the shop's id list grows unboundedly with duplicate entries over the life of a single legitimate installation. `findSessionsByShop()` — used by `AppInstallations.includes()`/`delete()` inside the `ensureInstalledOnShop` auth middleware — then iterates and issues one KV `get` per entry in that list, so its cost and subrequest count scale linearly with the (attacker/merchant-inflatable) list size.

### Finding Description
`storeSession()` unconditionally calls `addShopIds(session.shop, [session.id])`: [1](#0-0) 

and `addShopIds` simply concatenates the new id array onto whatever is already stored, with no de-duplication and no cap: [2](#0-1) 

This is the direct analog of the linked-list `addAddress` growth in the external report: an append-only structure keyed by an attacker/merchant-influenced dimension (here, the shop) with no bound, later consumed by an unbounded loop.

The offline session id is deterministic per shop (`offline_<shop>`), so any code path that re-stores that same session id will keep pushing duplicate entries into the shop's KV list. Two such paths are reachable from ordinary app operation triggered by the merchant/shop itself:

- `invalidateAccessToken()`, invoked whenever an admin API call returns `401` (e.g. after the merchant revokes/rotates the access token), clears `accessToken` and calls `storeSession()` again for the same offline id: [3](#0-2) 

- `ensureOfflineTokenIsNotExpired()`, run on the expiring-token refresh cycle, also re-stores the (same-id) refreshed offline session: [4](#0-3) 

Every one of these `storeSession()` calls appends another `offline_<shop>` entry to the `shop:<shop>` KV array via `addShopIds`, with no check that the id is already present (contrast this with `RedisSessionStorage.addKeyToShopList`, which explicitly checks `idKeysArray.includes(idKey)` before pushing — the Redis and KV implementations diverge here, and KV lacks the guard): [5](#0-4) 

That inflated list is consumed by `findSessionsByShop`, which fetches every id in the array: [6](#0-5) 

`findSessionsByShop` is itself invoked from `AppInstallations.includes()`/`delete()`: [7](#0-6) 

which is used by the `ensureInstalledOnShop` auth middleware (checked on ordinary, non-token-exchange embedded app page loads) and by the app-uninstalled webhook handler: [8](#0-7) 

### Impact Explanation
As the shop's KV id list grows into the thousands/tens of thousands of duplicate entries (purely from normal token-refresh/invalidation churn over the app's lifetime, no exotic input needed), `findSessionsByShop` — and therefore `AppInstallations.includes`/`delete` and the uninstall-webhook cleanup handler — must issue a proportional number of sequential KV `get` calls (`Promise.all(sessionIds.map(...))`). On platforms like Cloudflare Workers this can exceed per-invocation subrequest/CPU limits, causing that shop's install checks or uninstall webhook processing to fail/timeout — a denial of service against an auth-adjacent handler for that tenant.

### Likelihood Explanation
Reaching the growth condition requires no special privilege beyond being the shop that installed the app: normal API 401s (which merchants can trigger simply by revoking/rotating credentials) and the expiring-offline-token refresh cycle both re-store the same offline session id repeatedly over time. No admin/owner action on the shopify-app-js side is needed; a single merchant's app usage over an extended period, or a merchant intentionally repeating auth-invalidating actions, is sufficient to inflate the list.

### Recommendation
In `KVSessionStorage.addShopIds` (and any other array-based shop→id tracking), de-duplicate before writing (`if (!shopIds.includes(id)) shopIds.push(id)`), and additionally consider capping list size / pruning expired entries, matching the guard already present in `RedisSessionStorage.addKeyToShopList`.

### Proof of Concept
1. Configure an embedded app with `KVSessionStorage` and `expiringOfflineAccessTokens` (or simply let API calls hit `401`s, e.g. by having the merchant revoke the app's access token via the Partner/Admin UI without uninstalling).
2. Each time the app calls `invalidateAccessToken()` or `ensureOfflineTokenIsNotExpired()`'s refresh path, `storeSession()` is invoked with the same `offline_<shop>` id.
3. Observe that `namespace.get(shop:<shop>)` returns an ever-growing array with repeated `offline_<shop>` entries (`addShopIds` never checks for existing membership).
4. After sufficient growth, call `findSessionsByShop(shop)` (or hit an endpoint guarded by `ensureInstalledOnShop`) and observe the linear number of KV `get` calls performed, degrading/failing the request once platform subrequest/CPU limits are hit.

### Citations

**File:** packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts (L17-24)
```typescript
  public async storeSession(session: Session): Promise<boolean> {
    await this.namespace.put(
      session.id,
      JSON.stringify(session.toPropertyArray(true)),
    );
    await this.addShopIds(session.shop, [session.id]);
    return true;
  }
```

**File:** packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts (L56-69)
```typescript
  public async findSessionsByShop(shop: string): Promise<Session[]> {
    const sessionIds = await this.namespace.get<string[]>(
      this.getShopSessionIdsKey(shop),
      {type: 'json'},
    );

    if (!sessionIds) {
      return [];
    }

    return Promise.all(
      sessionIds.map(async (id) => (await this.loadSession(id))!),
    );
  }
```

**File:** packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts (L75-79)
```typescript
  private async addShopIds(shop: string, ids: string[]) {
    const key = this.getShopSessionIdsKey(shop);
    const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
    await this.namespace.put(key, JSON.stringify([...shopIds, ...ids]));
  }
```

**File:** packages/apps/shopify-app-express/src/helpers/invalidate-access-token.ts (L5-11)
```typescript
export async function invalidateAccessToken(
  session: Session,
  config: AppConfigInterface,
): Promise<void> {
  config.logger.debug('Invalidating stale access token', {shop: session.shop});
  session.accessToken = undefined;
  await config.sessionStorage.storeSession(session);
```

**File:** packages/apps/shopify-app-express/src/helpers/ensure-offline-token-is-not-expired.ts (L21-29)
```typescript
    const offlineSession = await refreshToken(
      params,
      session.shop,
      session.refreshToken,
    );

    await config.sessionStorage.storeSession(offlineSession);
    return offlineSession;
  }
```

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L151-166)
```typescript
  private async addKeyToShopList(session: Session) {
    const shopKey = session.shop;
    const idKey = this.client.generateFullKey(session.id);
    const idKeysArrayString = await this.client.get(shopKey);

    if (idKeysArrayString) {
      const idKeysArray = JSON.parse(idKeysArrayString);

      if (!idKeysArray.includes(idKey)) {
        idKeysArray.push(idKey);
        await this.client.set(shopKey, JSON.stringify(idKeysArray));
      }
    } else {
      await this.client.set(shopKey, JSON.stringify([idKey]));
    }
  }
```

**File:** packages/apps/shopify-app-express/src/app-installations.ts (L22-41)
```typescript
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
