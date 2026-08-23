### Title
Unbounded Duplicate Session-ID Accumulation in KV Shop-Session Index Causes Session-Storage DoS - ([File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts])

### Summary
`KVSessionStorage.storeSession()` unconditionally appends a session's id to the shop's session-id index (`shop:<shop>`) via `addShopIds()` without checking whether that id is already present in the index array, mirroring the reported "no duplicate check" pattern from the ynLSD `initialize()` bug class (adding to an array without verifying existing membership).

### Finding Description
`storeSession()` calls `addShopIds(session.shop, [session.id])` on every store, and `addShopIds` simply reads the current `shop:<shop>` array and concatenates the new id(s) with no de-duplication check: [1](#0-0) 

`storeSession` is invoked from normal, unprivileged authentication flows — specifically the token-exchange strategy re-issues and re-stores the offline/online session whenever the current session is missing or near expiry: [2](#0-1) 

Because offline (and often online) session ids are deterministically derived from the shop (e.g. `offline_<shop>`), repeated re-authentication/token-exchange cycles for the *same* shop/session id will call `storeSession` → `addShopIds` repeatedly with the identical id, and since `addShopIds` never checks for an existing entry, the same id is appended to the `shop:<shop>` KV array on every cycle. Unlike the SQL-backed adapters, which use `ON CONFLICT`/`ON DUPLICATE KEY UPDATE` upserts keyed by session id and therefore only ever store one row per id, the KV adapter's separate shop-index array has no equivalent uniqueness constraint.

### Impact Explanation
Over the natural lifetime of an app, an ordinary merchant/user's session expiring and being refreshed (a routine, unprivileged, automatic flow driven purely by App Bridge/OAuth session-token exchange) will continuously grow the `shop:<shop>` array with duplicate ids. This has two direct consequences:
1. `findSessionsByShop()` iterates every id in that (unbounded, duplicate-laden) array and calls `loadSession` for each, so read amplification grows linearly with the number of re-authentication cycles rather than the number of actual distinct sessions: [3](#0-2) 
2. The KV value itself grows unbounded and can eventually hit platform value-size limits (e.g., Cloudflare KV per-value size caps), causing `addShopIds`/`storeSession` to fail and breaking session storage entirely for that shop — a denial-of-service condition on the authentication/session-storage path.

### Likelihood Explanation
This requires no privileged actor or secret leakage — it is triggered purely by the normal behavior of a single merchant/customer using the app over time, since token/session expiry and refresh happen automatically and repeatedly during ordinary use. No malicious input is even strictly required, though an attacker who can force repeated re-authentication (e.g., by invalidating/expiring sessions faster, or replaying token-exchange requests) could accelerate the growth deliberately.

### Recommendation
In `addShopIds` (and symmetrically ensure `removeShopIds` is consistent), de-duplicate before writing back to KV, e.g. store the shop index as a `Set` or filter out ids already present before appending:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```

### Proof of Concept
1. Configure an app to use `KVSessionStorage`.
2. As a normal merchant, let the offline/online session expire and go through the token-exchange re-authentication flow repeatedly (this is normal, automatic behavior in embedded apps as sessions near/at expiry) — each cycle calls `TokenExchangeStrategy.authenticate` → `config.sessionStorage.storeSession(offlineSession)` with the same deterministic session id.
3. Observe that after N re-authentication cycles, the KV value at `shop:<shop>` contains the same session id repeated N times (verifiable by reading the KV namespace directly), with no de-duplication ever applied by `addShopIds`.
4. Repeated growth over time will increase read amplification in `findSessionsByShop` and can eventually exceed the KV value size limit, causing `storeSession`/`addShopIds` writes to fail for that shop.

Note: I could not execute this against a live Cloudflare Workers KV instance to observe the exact point of failure (size limits, exact amplification factor) — this is based on static code analysis of `kv.ts` and the token-exchange call path; a Devin session with a running environment would be needed to empirically confirm the KV size-limit failure threshold.

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
