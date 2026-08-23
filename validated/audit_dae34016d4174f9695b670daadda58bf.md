### Title
Unbounded growth of per-shop session ID list in `KVSessionStorage` due to missing de-duplication - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.storeSession` appends the session id to a per-shop index list (`shop:{shop}`) on every call via `addShopIds`, but unlike the Redis adapter's equivalent method, it never checks whether the id is already present before appending. Because `storeSession` is called by the SDK repeatedly for the same session id/shop (e.g. every time an offline token is (re-)exchanged), the `shop:{shop}` KV value grows without bound, mirroring the `delegated[]` growth issue in the reference report: a per-key mapping used for lookups is never cleaned/deduplicated, only ever growing.

### Finding Description
`addShopIds` reads the current array of ids for a shop and blindly concatenates the new id(s) onto it, with no `includes` check: [1](#0-0) 

Compare this to the Redis adapter's `addKeyToShopList`, which explicitly guards against duplicates: [2](#0-1) 

`storeSession` calls `addShopIds` unconditionally on every store, even when the session id already exists in KV: [3](#0-2) 

`storeSession` is invoked repeatedly for the same offline session id in normal authenticated (but unprivileged, non-admin) request flows whenever the stored session is missing/inactive, e.g. in the Remix/React Router token-exchange strategies and the Express `performTokenExchange` middleware — every request from a merchant/customer bearing a session token that doesn't currently have an active stored session triggers another `storeSession(offlineSession)` call using the same deterministic offline session id (`offline_{shop}`): [4](#0-3) [5](#0-4) 

Each such call appends another duplicate `offline_{shop}` entry to the `shop:{shop}` array in KV, which is never deduplicated or capped. The polluted array is subsequently consumed by `findSessionsByShop`, which blindly loads every id in the array (including all duplicates): [6](#0-5) 

`findSessionsByShop` is in turn used by `AppInstallations.includes`/`delete`, which are called from the (anonymously/Shopify-triggered) `APP_UNINSTALLED` webhook handler and other auth-adjacent flows: [7](#0-6) 

### Impact Explanation
As the `shop:{shop}` array grows unbounded, `findSessionsByShop` performs increasingly many redundant `loadSession` calls (Cloudflare KV `get` operations), inflating latency and KV request cost linearly with the number of times token-exchange re-storage occurred for that shop. Because Cloudflare KV values have a hard size limit (25 MiB), the JSON-encoded id array can eventually hit that ceiling, causing `namespace.put` in `addShopIds`/`storeSession` to fail — breaking the token-exchange/auth flow (a DoS of the auth handler) for that shop, and making `findSessionsByShop`/`AppInstallations` operations (including uninstall cleanup) increasingly expensive or eventually failing to parse/handle the oversized value. This is scoped to a single shop's KV entry (not cross-tenant), consistent with a medium-severity, single-tenant DoS analog to the referenced `delegated[]` issue rather than a network-wide one.

### Likelihood Explanation
This can be triggered without any special privilege: any request that carries a valid session token for a shop but for which the offline session is momentarily missing or inactive (which is a routine, frequent condition — e.g., cold starts, evictions, cache misses, or session-storage flakiness) causes another `storeSession` call and another duplicate append. A user/integration issuing many such requests (or simply normal high-traffic usage over time) will grow the array; a motivated single merchant/user could accelerate this deliberately by repeatedly forcing token exchange (e.g., clearing/invalidating their own stored offline session or hammering endpoints that trigger the token-exchange strategy).

### Recommendation
In `KVSessionStorage.addShopIds`, de-duplicate before writing back, mirroring the Redis adapter's `includes` check:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```
Additionally, consider skipping the `addShopIds` call entirely in `storeSession` when the id is already present, and adding a periodic/consistency check to prune stale ids whose underlying session no longer exists.

### Proof of Concept
1. Configure an app to use `KVSessionStorage` with `useOnlineTokens: false` (offline sessions only), so the deterministic session id is `offline_{shop}`.
2. Simulate the token-exchange path being taken repeatedly for the same shop by calling `storeSession(offlineSession)` (same `session.id`) N times in a row, as occurs in `performTokenExchange`/`token-exchange.ts` whenever a valid stored active session isn't found:
```ts
const storage = new KVSessionStorage(mockNamespace);
const session = new Session({id: 'offline_test-shop.myshopify.com', shop: 'test-shop.myshopify.com', state: 's', isOnline: false});
for (let i = 0; i < 10000; i++) {
  await storage.storeSession(session);
}
const shopIds = JSON.parse(await mockNamespace.get('shop:test-shop.myshopify.com'));
console.log(shopIds.length); // 10000, all identical duplicate ids
```
3. Observe that `shop:test-shop.myshopify.com` now holds 10,000 duplicate entries of the same session id, and `findSessionsByShop('test-shop.myshopify.com')` performs 10,000 redundant `loadSession` KV reads instead of 1 — demonstrating the unbounded, non-deduplicated growth analogous to the `delegated[]` mapping never being cleared.

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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L94-101)
```typescript
    const offlineSession = await exchangeToken(
      api,
      config,
      sessionToken,
      shop,
      RequestedTokenType.OfflineAccessToken,
    );
    await config.sessionStorage.storeSession(offlineSession);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L60-67)
```typescript
      const {session: offlineSession} = await this.exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });

      await config.sessionStorage!.storeSession(offlineSession);
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
