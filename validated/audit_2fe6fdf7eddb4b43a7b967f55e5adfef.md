### Title
Session storage array in `KVSessionStorage` can grow unbounded with duplicate entries via repeated offline-token exchanges, causing session-lookup corruption and DoS of the auth handler - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
The Cloudflare KV session storage adapter appends session IDs to a per-shop array without checking whether the ID is already present, unlike the equivalent Redis adapter which explicitly guards against duplicates. Because `storeSession()` is invoked on every offline-token exchange/refresh cycle with the *same* deterministic session ID, this "add without checking existence" pattern is directly analogous to the reported `LiquidInfrastructureERC20` bug, where pushing to an array without checking for prior presence (or zero-effect operations) let an unprivileged actor duplicate entries and eventually corrupt/brick the iteration logic.

### Finding Description
`KVSessionStorage.storeSession()` calls `addShopIds(session.shop, [session.id])` on every store: [1](#0-0) 

`addShopIds` simply spreads the existing array and the new id(s) onto it with no de-duplication check: [2](#0-1) 

This is the exact analog of the original bug: an array used for iteration (`holders` in the Solidity contract, the shop's session-id list here) is appended to without checking whether the entry already "exists," allowing the same identifier to be pushed repeatedly. Contrast this with the Redis implementation of the same operation, which does perform the existence check before pushing: [3](#0-2) 

The reachability path for an unprivileged/single-merchant actor: for embedded apps using token exchange, `storeSession()` for the offline session (id = `offline_${shop}`, a deterministic, non-random ID) is called every time the stored offline session is missing, expired, or invalid: [4](#0-3) 

The same pattern exists in `shopify-app-express`'s `performTokenExchange` middleware and in `shopify-app-react-router`: [5](#0-4) 

This re-exchange/re-store happens routinely and repeatably: when `expiringOfflineAccessTokens` is enabled the offline token has a bounded lifetime and is refreshed periodically; when an access token is revoked/401s, `invalidateAccessToken` clears the local session, forcing the next request to redo the exchange and `storeSession()` call. In both cases, the *same* `offline_${shop}` id gets pushed into the `shop:{shop}` KV array again and again, since `addShopIds` never checks for its prior presence.

### Impact Explanation
- `findSessionsByShop(shop)` iterates the (now duplicated) id array and calls `loadSession` for every entry, returning the same `Session` object multiple times: [6](#0-5)  Any consumer that iterates "all sessions for a shop" to perform an action per session (e.g., webhook re-registration, bulk background jobs) will perform that action redundantly, multiplying outbound Admin API calls and risking rate-limit exhaustion — a functional DoS side effect, mirroring the original report's "miscalculation of rewards"/duplicate-processing impact.
- The `shop:{shop}` KV value grows unboundedly over the life of an installation as tokens are refreshed/invalidated. Cloudflare KV values have hard size limits; once exceeded, `namespace.put` for that key fails, breaking `storeSession`/`addShopIds` and therefore breaking the auth/token-exchange handler for that shop going forward — a concrete DoS of an authentication handler, directly matching the report's "bricking certain functions" impact class.
- No leaked secret, privileged actor, or MITM is required — any legitimate app user (a single merchant/customer) naturally drives this growth simply by using the app across the offline token's refresh/invalidation lifecycle; a malicious/curious user could accelerate it by intentionally causing repeated 401s (e.g. triggering `invalidateAccessToken`) to force more frequent re-exchange cycles.

### Likelihood Explanation
Likely for any deployment using `KVSessionStorage` with the `expiringOfflineAccessTokens` future flag or normal 401-triggered re-authentication, since these are standard, unprivileged, expected app flows (not edge cases requiring elevated access). The growth is slow under normal use but is fully attacker-influenceable via triggering repeated invalid/expired-session conditions, and it silently degrades over the app's operational lifetime with no corrective/self-healing code path (only `deleteSession`/`removeShopIds`, which is dedup-safe via `filter`, ever shrinks the array, and only for explicit session deletions).

### Recommendation
In `addShopIds` (`packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`), de-duplicate before writing, mirroring the Redis adapter's existence check:
```diff
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
- await this.namespace.put(key, JSON.stringify([...shopIds, ...ids]));
+ const merged = Array.from(new Set([...shopIds, ...ids]));
+ await this.namespace.put(key, JSON.stringify(merged));
}
```

### Proof of Concept
1. Configure an embedded app with `future.expiringOfflineAccessTokens: true` (or a token-exchange flow) and `KVSessionStorage`.
2. As a normal merchant/app user, let the offline token expire (or trigger a 401 to invalidate it via `invalidateAccessToken`), causing `performTokenExchange`/`authenticate` to re-run `exchangeToken` + `config.sessionStorage.storeSession(offlineSession)` with `id = offline_${shop}` each cycle: [7](#0-6) 
3. Repeat this cycle N times (naturally over time, or forced by repeatedly invalidating the token).
4. Inspect the KV value at key `shop:${shop}`; it will contain `offline_${shop}` N times instead of once, as `addShopIds` never checks for existing membership: [2](#0-1) 
5. Calling `findSessionsByShop(shop)` returns N duplicate `Session` objects, and continued growth risks exceeding the KV value size limit, breaking future `storeSession` calls for that shop.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-67)
```typescript
    if (
      !session ||
      !session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)
    ) {
      logger.info('No valid session found', {shop});
      logger.info('Requesting offline access token', {shop});
      const {session: offlineSession} = await this.exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });

      await config.sessionStorage!.storeSession(offlineSession);
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
