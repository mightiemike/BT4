Confirmed: session IDs are deterministic (`offline_${shop}` and `${shop}_${userId}`) [1](#0-0) , and `storeSession`/token exchange are re-run every time a session becomes inactive within the expiry window [2](#0-1) , which is triggered automatically on ordinary requests from a single merchant/app-user, not just a malicious admin.

### Title
Unbounded, non-deduplicated shop-session-id array growth in `KVSessionStorage.addShopIds` leads to DoS of session lookups and OAuth/token-exchange — ([File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts])

### Summary
`KVSessionStorage.storeSession` calls `addShopIds`, which appends the session id to the shop's id-list value in Cloudflare KV **without checking for duplicates**, unlike the equivalent Redis implementation. Because session ids used in the auth/token-exchange flow are deterministic per shop (`offline_<shop>`) or per shop+user (`<shop>_<userId>`), every re-authentication/token-refresh for the same shop/user re-appends the *same* id to the array, causing it to grow without bound over the life of an installation.

### Finding Description
`addShopIds` reads the existing id array for a shop key and blindly concatenates the new ids, then writes it back: [3](#0-2) 

This is called from `storeSession` on every session persist: [4](#0-3) 

Compare this to `RedisSessionStorage.addKeyToShopList`, which explicitly checks `if (!idKeysArray.includes(idKey))` before pushing, preventing duplicate growth: [5](#0-4) 

`storeSession` is invoked automatically on the standard token-exchange / OAuth authentication path whenever a session is missing or near expiry (`WITHIN_MILLISECONDS_OF_EXPIRY`), which happens routinely during normal app usage by a single merchant/customer, not only via admin action: [6](#0-5) 

Session ids for both offline and online tokens are deterministic, so each refresh cycle appends the exact same string to the shop's array: [1](#0-0) 

`findSessionsByShop` then reads and iterates the entire (ever-growing, mostly duplicate) id list, issuing a `loadSession` KV get for every entry: [7](#0-6) 

This is the direct analog of the reported bug class: an admin-controlled/append-only array (`poolInfo` in the original report) that is iterated by core logic (`GLPbackingNeeded`) grows without bound and eventually causes core operations to fail due to a hard platform limit (block gas limit there; Cloudflare KV's 25 MiB per-value size limit here).

### Impact Explanation
As the shop's id-array value grows indefinitely, two things happen: (1) `findSessionsByShop` becomes progressively slower and more expensive since it performs one KV read per (duplicate) id, and (2) once the JSON-serialized array approaches Cloudflare KV's per-value size limit, `namespace.put` in `addShopIds` (and thus `storeSession`) will start failing. Because `storeSession` is required to complete the OAuth callback and token-exchange flows, this failure blocks new authentications/token refreshes for the affected shop — a denial of service of core auth functionality, matching the "bricks core protocol functionality" impact in the original report.

### Likelihood Explanation
Unlike the original report (which required a malicious/erring admin calling `addPool` many times), this variant requires no privileged action at all: normal, expected session-token expiry and refresh cycles during ordinary embedded-app usage by a single merchant or app user repeatedly re-trigger `storeSession` with the same deterministic id, so the array grows through routine, unprivileged use over time. This makes the likelihood higher than the original finding, though the time to reach KV's size limit depends on token lifetime/refresh frequency and installation age.

### Recommendation
In `KVSessionStorage.addShopIds`, deduplicate before writing, mirroring the Redis implementation's `includes` check:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```
Additionally, consider pruning expired session ids from the shop list (or bounding its size) so `findSessionsByShop` and the underlying KV value cannot grow unbounded even under repeated legitimate use.

### Proof of Concept
1. Install the app on a shop and go through OAuth/token-exchange to obtain the deterministic offline session id `offline_<shop>.myshopify.com`.
2. Repeatedly cause the offline (or online) session to be treated as expired/inactive (e.g., wait past `WITHIN_MILLISECONDS_OF_EXPIRY` or force expiry) and issue a normal authenticated request, triggering `performTokenExchange`/`authenticate` to call `exchangeToken` + `config.sessionStorage.storeSession(offlineSession)` again [8](#0-7) .
3. Observe (e.g., via a test `KVNamespace` mock) that `namespace.get(this.getShopSessionIdsKey(shop))` returns an array containing the same id repeated once per refresh cycle, growing linearly with the number of refreshes, since `addShopIds` never deduplicates [3](#0-2) .
4. Extrapolating, after enough refresh cycles the JSON-serialized array approaches KV's value size limit, at which point `namespace.put` in `addShopIds`/`storeSession` fails, breaking subsequent OAuth/token-exchange completions for that shop.

### Citations

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L16-26)
```typescript
export function getJwtSessionId(config: ConfigInterface) {
  return (shop: string, userId: string): string => {
    return `${sanitizeShop(config)(shop, true)}_${userId}`;
  };
}

export function getOfflineId(config: ConfigInterface) {
  return (shop: string): string => {
    return `offline_${sanitizeShop(config)(shop, true)}`;
  };
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-82)
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
