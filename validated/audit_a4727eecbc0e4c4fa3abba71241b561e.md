This confirms the analog: `storeSession` is only invoked when a session is missing or expired (session token exchange creates a fresh offline/online session whenever no active session exists), so calling `storeSession` repeatedly requires the token to expire or be invalidated — not simply "every request." Still, expiry naturally recurs (access tokens expire, `invalidateAccessToken` explicitly deletes+forces a fresh exchange on 401 from Shopify), so over the life of an install `storeSession` for the same `shop`/session id will legitimately fire many times, each one calling `KVSessionStorage.addShopIds`.

### Title
Unbounded duplicate growth of the KV per-shop session-id index leads to denial of service in `KVSessionStorage` - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.storeSession` unconditionally appends the session id to the `shop:<shop>` index array on every call, with no deduplication, unlike the equivalent Redis adapter (`addKeyToShopList`) which checks `includes()` before pushing.

### Finding Description
`storeSession` calls `addShopIds(session.shop, [session.id])` on every store, and `addShopIds` does `JSON.stringify([...shopIds, ...ids])` without checking whether `ids` are already present in `shopIds`. [1](#0-0) [2](#0-1) 
Because `storeSession` is re-invoked for the same offline/online session id whenever the token-exchange strategy re-establishes a session (no active session found, or the app explicitly invalidates a stale access token on a 401 from Shopify and re-exchanges), the same session id gets appended to the shop's index array repeatedly over the app's lifetime. [3](#0-2) 
This mirrors the analog bug class: an ever-growing array associated with an entity (shop) that is fully iterated (`findSessionsByShop`) on every element, with duplicates never pruned, exactly as described for `_removeParticipant()`'s unbounded stake array. [4](#0-3) 
`findSessionsByShop` is itself relied upon by `AppInstallations.includes`/`delete`, which gate install-status checks and session cleanup during OAuth/uninstall flows. [5](#0-4) 

### Impact Explanation
As the `shop:<shop>` array grows, `findSessionsByShop` performs redundant `loadSession` KV reads for every (duplicated) id, degrading latency and read-quota usage linearly with the duplicate count. Cloudflare KV values are also capped (25 MiB), so unbounded growth could eventually make `namespace.put` for the index key fail, breaking `storeSession`/`addShopIds` entirely for that shop and thus breaking the auth/token-exchange flow that depends on it — a denial of service of an auth-adjacent handler.

### Likelihood Explanation
This requires no attacker action beyond normal, repeated legitimate use — every offline-token re-exchange (e.g., triggered by Shopify session-token expiry, or the app's own `invalidateAccessToken` + retry logic on a 401) appends another duplicate. It is a durability/degradation issue under normal long-running operation rather than a single-request exploit, so likelihood of eventual impact is moderate but requires sustained usage/time to manifest as a real DoS.

### Recommendation
In `KVSessionStorage.addShopIds`, deduplicate before writing (e.g., mirror the Redis adapter's `includes()` check, or use a `Set`), and consider periodically compacting/pruning the shop index, or storing it keyed by `session.id` directly (a KV list/prefix scan) instead of a single JSON array subject to unbounded growth.

### Proof of Concept
1. Configure an app to use `KVSessionStorage` with `useOnlineTokens` (or rely on offline-token expiry/invalidation).
2. Repeatedly trigger the token-exchange path so the same offline (or online) session id is re-obtained N times — e.g., let the access token expire, or force `invalidateAccessToken` via a simulated 401 from the Admin API, then have the app re-exchange the token. [6](#0-5) 
3. Each cycle calls `storeSession` → `addShopIds`, appending the same session id to `shop:<shop>` again with no dedup check. [2](#0-1) 
4. Inspect the `shop:<shop>` KV value after N cycles — it contains N duplicate copies of the same session id, and `findSessionsByShop` now performs N redundant `loadSession` calls, growing without bound as the app continues normal operation.

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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L84-116)
```typescript
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

    let newSession = offlineSession;

    if (config.useOnlineTokens) {
      logger.info('Requesting online access token', {shop});
      const onlineSession = await exchangeToken(
        api,
        config,
        sessionToken,
        shop,
        RequestedTokenType.OnlineAccessToken,
      );
      await config.sessionStorage.storeSession(onlineSession);
      newSession = onlineSession;
    }
```

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L150-161)
```typescript
    if (error instanceof HttpResponseError && error.response.code === 401) {
      if (sessionToInvalidate?.accessToken) {
        await invalidateAccessToken(sessionToInvalidate, config);
      }
      respondToInvalidSessionToken({
        api,
        req,
        res,
        message: error.message,
      });
      return;
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
