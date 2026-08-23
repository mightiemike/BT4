This confirms the vulnerability: `getOfflineId` produces a **deterministic** session id (`offline_<shop>`) for every offline OAuth/token-exchange flow, and `performTokenExchange` calls `config.sessionStorage.storeSession(offlineSession)` on every request that lacks a currently-valid session [1](#0-0) [2](#0-1) .

### Title
Unbounded growth of the per-shop session-id index in `KVSessionStorage` enables denial of service of session lookup/uninstall handling - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.storeSession` unconditionally appends the stored session's id to a per-shop index list (`shop:<shop>`) via `addShopIds`, without checking whether that id is already present [3](#0-2) [4](#0-3) . Because offline session ids are deterministic per shop (`offline_<shop>`), any repeated call to `storeSession` for the same offline session (which happens on ordinary, unprivileged app usage such as token exchange/refresh) pushes a duplicate id into the array forever, growing it without bound — directly analogous to the reported `addReward`/`rewardTokens` unbounded-array bug class.

### Finding Description
The Redis implementation of the same interface explicitly guards against this by checking `idKeysArray.includes(idKey)` before pushing [5](#0-4) , but the KV implementation's `addShopIds` has no such guard: it simply spreads the existing array with the new ids and writes it back [4](#0-3) .

Every offline session id is derived deterministically from the shop domain via `getOfflineId` (`offline_<shop>`) [1](#0-0) . `performTokenExchange`, which runs on ordinary authenticated requests whenever no currently valid session is cached, calls `exchangeToken` and then `config.sessionStorage.storeSession(offlineSession)` using this same deterministic id [6](#0-5) . Each such call appends another copy of `offline_<shop>` to the `shop:<shop>` KV list, without ever removing the earlier duplicate.

The list is consumed by `findSessionsByShop`, which loads every id in the array (including duplicates) with `Promise.all(sessionIds.map(...))` [7](#0-6) . This method is relied on by `AppInstallations.includes` and `AppInstallations.delete` in `shopify-app-express`, which gate uninstall-webhook processing and app-installation checks [8](#0-7) .

### Impact Explanation
As the shop's session-id array grows without bound:
- The JSON-serialized KV value for `shop:<shop>` grows linearly with every re-authentication/token-exchange event, and can eventually exceed the KV value size limit, causing `namespace.put` to fail and breaking all future `storeSession`/`deleteSession` calls for that shop.
- `findSessionsByShop` performs one KV `get` per (duplicated) id, so its cost and read-quota consumption grows unbounded, eventually causing timeouts/failures.
- Because `AppInstallations.includes`/`delete` depend on `findSessionsByShop`/`deleteSessions`, uninstall-webhook handling and installation checks for the shop can be effectively bricked, denying legitimate session cleanup and app-uninstall flows — a availability/DoS impact on an auth-related handler.

### Likelihood Explanation
Reachable by an ordinary, unprivileged actor: any embedded app user whose app repeatedly triggers token exchange for offline access (e.g., normal session-token refresh cycles, or reloading the embedded app after the cached offline session appears inactive) causes repeated `storeSession` calls with the same deterministic offline session id, each one appending a duplicate to the per-shop KV list. No secret leakage or privileged access is required — it's driven purely by the standard OAuth/token-exchange request flow that `shopify-app-express` performs automatically.

### Recommendation
In `KVSessionStorage.addShopIds`, deduplicate before writing back to KV (e.g., use a `Set` or check `shopIds.includes(id)` before appending), matching the guard already present in `RedisSessionStorage.addKeyToShopList`. Consider also bounding/pruning stale ids during `addShopIds`/`removeShopIds` to keep the per-shop index size proportional to the actual number of distinct sessions.

### Proof of Concept
1. Configure a Cloudflare Workers app using `KVSessionStorage`.
2. For a given shop, repeatedly trigger the token-exchange path in `performTokenExchange` without a currently active cached session (e.g., by having the embedded app call it on every load, or by simulating repeated calls to `config.sessionStorage.storeSession(offlineSession)` with the same `offline_<shop>` id).
3. Observe that `namespace.get(this.getShopSessionIdsKey(shop))` returns an array containing the same `offline_<shop>` id repeated N times, growing with every call, since `addShopIds` in `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts` (lines 75-79) never checks for existing membership before pushing.
4. Continue this loop until the JSON array approaches KV value size limits or `findSessionsByShop`'s `Promise.all` over the duplicated ids becomes prohibitively slow/costly, demonstrating the DoS condition on session lookup and uninstall handling.

### Citations

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L22-26)
```typescript
export function getOfflineId(config: ConfigInterface) {
  return (shop: string): string => {
    return `offline_${sanitizeShop(config)(shop, true)}`;
  };
}
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L70-101)
```typescript
    const sessionId = config.useOnlineTokens
      ? api.session.getJwtSessionId(shop, sub)
      : api.session.getOfflineId(shop);

    let session: Session | undefined;
    try {
      session = await config.sessionStorage.loadSession(sessionId);
      sessionToInvalidate = session;
    } catch (error) {
      logger.error(`Error when loading session from storage: ${error}`);
      res.status(500).send('Internal Server Error');
      return;
    }

    if (session && session.isActive(undefined, WITHIN_MILLISECONDS_OF_EXPIRY)) {
      logger.debug('Request is valid, session is active', {shop: session.shop});
      res.locals.shopify = {...res.locals.shopify, session};
      next();
      return;
    }

    logger.info('No valid session found', {shop});
    logger.info('Requesting offline access token', {shop});

    const offlineSession = await exchangeToken(
      api,
      config,
      sessionToken,
      shop,
      RequestedTokenType.OfflineAccessToken,
    );
    await config.sessionStorage.storeSession(offlineSession);
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
