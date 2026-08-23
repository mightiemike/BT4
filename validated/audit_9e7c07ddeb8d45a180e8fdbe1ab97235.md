### Title
Lost-update race condition in shop→session index causes sessions to silently drop out of `findSessionsByShop`, letting revoked/uninstalled sessions survive - ([File: packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts], [File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts])

### Summary
Both the Redis and Cloudflare KV session-storage adapters maintain a secondary "shop → list of session ids" index using a non-atomic read-modify-write pattern. When two `storeSession`/`deleteSession` calls for the same shop race (which happens naturally under normal, unprivileged traffic such as two staff members of the same store authenticating concurrently, multiple embedded-app tabs, or online+offline token exchange overlapping across requests), one write clobbers the other, permanently dropping a session id from the shop index. This mirrors the Lighthouse `getBlobs` race class: a race between a read and a subsequent write of shared state causes data (blob columns there, session ids here) to silently fail to persist.

### Finding Description
`RedisSessionStorage.addKeyToShopList` and `removeKeyFromShopList` implement the shop index as: GET current array → mutate in memory → SET back the whole array, with no locking, optimistic concurrency check (CAS/WATCH), or atomic Redis set/list operation: [1](#0-0) 

The same unguarded read-modify-write pattern exists in the Cloudflare KV adapter: [2](#0-1) 

`storeSession` calls this helper after writing the session record itself, so the window for the race spans the full storage round-trip of every OAuth callback and token-exchange completion: [3](#0-2) 

Because `useOnlineTokens` apps store an offline session and then an online session per user, and multiple users/tabs of the same shop can authenticate concurrently, two independent `storeSession` calls for the *same shop* but *different session ids* routinely overlap in time - this is not an edge case requiring an attacker, just normal multi-user usage of an embedded app, reachable from unauthenticated/merchant-driven token-exchange requests handled in: [4](#0-3) 

When the race triggers, one of the two valid session ids never gets appended to (or, on deletion, never gets removed from) the shop's index array. `findSessionsByShop(shop)` — the API this package exposes specifically so host apps can enumerate and purge all of a shop's sessions (e.g., for GDPR shop-redact / app-uninstalled cleanup) — then returns an incomplete list: [5](#0-4) 

### Impact Explanation
A session id lost from the shop index is still fully present and loadable via `loadSession(id)` — it just becomes invisible to any code path (uninstall/GDPR cleanup, admin tooling) that enumerates sessions through `findSessionsByShop`. If a merchant uninstalls the app or a shop-redact webhook triggers bulk `deleteSessions(ids)` based on `findSessionsByShop`, the orphaned session (with a still-valid access token) is skipped and never revoked/deleted. That session remains usable indefinitely for authenticated Admin API calls, effectively defeating token revocation. This is a persistence/consistency defect in the credential-storage layer, directly analogous to the referenced advisory's "data not persisting due to a race condition."

### Likelihood Explanation
No attacker action is required — the race is triggered by ordinary concurrent activity from legitimate, unprivileged users (e.g., two staff accounts of the same shop loading the embedded app at nearly the same time, each causing an online-token `storeSession` call for the same shop). Any deployment using `RedisSessionStorage` or `KVSessionStorage` with more than light traffic per shop is exposed. The race window covers the full network round trip to the backing store, making collisions realistic under production load.

### Recommendation
Replace the read-then-write shop index maintenance with an atomic data structure/operation:
- Redis: use a native Set (`SADD`/`SREM`) for the shop's session-id index instead of a JSON-encoded array stored via `GET`/`SET`.
- Cloudflare KV: KV has no atomic list mutation primitive; use a Durable Object, a secondary storage backend with transactions, or a per-shop mutex around `addShopIds`/`removeShopIds`, or key sessions by `shop:id` and use `list()` with a prefix instead of maintaining a manually-updated index array.
- Regardless of backend, ensure `findSessionsByShop`-driven cleanup routines used for uninstall/GDPR are backed by a consistent index, and audit these adapters' battery-of-tests for concurrent-write scenarios.

### Proof of Concept
1. Configure an embedded app with `useOnlineTokens: true` and `RedisSessionStorage` (or `KVSessionStorage`).
2. Have two different staff users of the same shop load the app at (nearly) the same instant, each completing token exchange and calling `storeSession` for their own online session id concurrently.
3. Both calls execute `addKeyToShopList`: both `GET shopKey` before either has `SET` the updated array back; each independently appends its own id to the array it read and writes it back — the second `SET` overwrites the first, so one valid session id is missing from the shop index.
4. Call `findSessionsByShop(shop)` — only one of the two sessions is returned even though both exist and are loadable via `loadSession(id)`.
5. Simulate uninstall cleanup as `deleteSessions(await findSessionsByShop(shop).map(s => s.id))` — the orphaned session's access token is never deleted and remains usable.

### Citations

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L90-99)
```typescript
  public async storeSession(session: Session): Promise<boolean> {
    await this.ready;

    await this.client.set(
      session.id,
      JSON.stringify(session.toPropertyArray(true)),
    );
    await this.addKeyToShopList(session);
    return true;
  }
```

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L128-145)
```typescript
  public async findSessionsByShop(shop: string): Promise<Session[]> {
    await this.ready;

    const idKeysArrayString = await this.client.get(shop);
    if (!idKeysArrayString) return [];

    const idKeysArray = JSON.parse(idKeysArrayString);
    const results: Session[] = [];
    for (const idKey of idKeysArray) {
      const rawResult = await this.client.get(idKey, false);
      if (!rawResult) continue;

      const session = Session.fromPropertyArray(JSON.parse(rawResult), true);
      results.push(session);
    }

    return results;
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

**File:** packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts (L75-88)
```typescript
  private async addShopIds(shop: string, ids: string[]) {
    const key = this.getShopSessionIdsKey(shop);
    const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
    await this.namespace.put(key, JSON.stringify([...shopIds, ...ids]));
  }

  private async removeShopIds(shop: string, ids: string[]) {
    const key = this.getShopSessionIdsKey(shop);
    const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
    await this.namespace.put(
      key,
      JSON.stringify(shopIds.filter((id) => !ids.includes(id))),
    );
  }
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L60-82)
```typescript
      const {session: offlineSession} = await this.exchangeToken({
        request,
        sessionToken,
        shop,
        requestedTokenType: RequestedTokenType.OfflineAccessToken,
      });

      await config.sessionStorage!.storeSession(offlineSession);

      let newSession = offlineSession;

      if (config.useOnlineTokens) {
        logger.info('Requesting online access token', {shop});
        const {session: onlineSession} = await this.exchangeToken({
          request,
          sessionToken,
          shop,
          requestedTokenType: RequestedTokenType.OnlineAccessToken,
        });

        await config.sessionStorage!.storeSession(onlineSession);
        newSession = onlineSession;
      }
```
