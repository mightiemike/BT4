## Analysis

The Beanstalk bug class is: an unbounded, attacker-growable on-chain array (`plotIndexes`) that is later iterated/searched linearly, allowing a low-privilege actor to inflate the array until legitimate operations (`removePlotIndexFromAccount`) run out of gas and revert — a DoS of a core state-mutating flow.

Searching shopify-app-js for an analogous "per-key array that grows unboundedly and is later enumerated/searched by an auth-relevant handler," the closest match is the shop→session-id index maintained by the Cloudflare KV session storage adapter.

### Title
Unbounded, non-deduplicated growth of per-shop session-ID index causes DoS of install-check/auth handlers - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage` maintains, for each shop, a JSON array of session IDs at key `shop:${shop}` used by `findSessionsByShop`. Unlike the Redis adapter's equivalent method, the KV adapter's `addShopIds` never checks whether an ID is already present before appending it, so every call to `storeSession` for the same session ID (e.g. every offline-token exchange/refresh) appends a duplicate entry that is never cleaned up.

### Finding Description
`storeSession` unconditionally calls `addShopIds(session.shop, [session.id])`, which reads the current array and appends the new id without a duplicate check: [1](#0-0) 

Compare this with the Redis adapter's `addKeyToShopList`, which explicitly guards against duplicates with `if (!idKeysArray.includes(idKey))`: [2](#0-1) 

Because offline session IDs are deterministic (`offline_<shop>`), any code path that repeatedly calls `storeSession` for the same shop's offline session — such as `performTokenExchange`, which stores a freshly exchanged offline session whenever the previously loaded session is missing/near-expiry — will keep appending to the KV array without bound: [3](#0-2) 

The bloated array is later fully materialized and iterated by `findSessionsByShop`, which loads every listed ID (including all duplicates) via `Promise.all`: [4](#0-3) 

`findSessionsByShop` is itself relied upon by `AppInstallations.includes`/`.delete`, which back the embedded-app install-check middleware and the app-uninstalled webhook handler — both auth-adjacent handlers: [5](#0-4) [6](#0-5) 

This mirrors the reported bug class: an array that only grows and is walked/loaded in full on every lookup, with no cap or dedup, eventually degrading or breaking the operation that depends on it.

### Impact Explanation
As the per-shop ID array grows (duplicates accumulate on every repeated `storeSession` call for the same shop), `findSessionsByShop` performs an ever-larger number of KV `get` calls and JSON parses per invocation. This directly slows/DoSes:
- `AppInstallations.includes`/`.delete`, used by the install-check middleware and the uninstall webhook handler.
- Any app relying on `findSessionsByShop` for multi-user shop session enumeration.

Given Cloudflare KV's per-key value size limits and per-request subrequest limits, sufficiently large arrays can also cause `put`/`get` failures outright, not just slowness.

### Likelihood Explanation
Reaching this only requires triggering `storeSession` for the same shop repeatedly, which happens automatically whenever `performTokenExchange` re-mints an offline token (e.g., near-expiry sessions, or any legitimate re-authentication flow) — no special privilege beyond normal app usage by the shop itself is needed, and this specifically only affects the KV adapter because it lacks the dedup guard other adapters (Redis) already implement. Likelihood scales with how frequently the app’s token-exchange/re-auth path executes for a given shop over its lifetime.

### Recommendation
Add a duplicate check in `addShopIds` (mirroring the Redis adapter's `includes` guard) before appending to the shop's ID array, and/or switch the shop→session index to a `Set`-like deduplicated structure. Additionally, `removeShopIds` should be verified to correctly prune stale IDs so the array does not grow indefinitely across the session lifecycle.

### Proof of Concept
1. Configure an embedded app to use `KVSessionStorage` with `useOnlineTokens=false` and token exchange enabled.
2. Repeatedly force offline-session re-storage for the same shop (e.g., invoke `performTokenExchange` in a loop with an offline session whose `isActive` check fails each time, causing `exchangeToken` + `storeSession(offlineSession)` to run repeatedly).
3. Observe that `shop:${shop}` in KV accumulates the same `offline_<shop>` ID many times (`addShopIds` never dedups).
4. Call `findSessionsByShop(shop)` (as done inside `AppInstallations.includes`/`.delete`) and observe it performs one KV `get` + JSON parse per duplicate entry, with cost growing linearly/unbounded with the number of re-auth events, eventually timing out or exceeding KV limits.

### Citations

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
