### Title
Unbounded growth of per-shop session-ID list in `KVSessionStorage` causes denial-of-service of session lookup/auth checks - (File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts)

### Summary
The `KVSessionStorage` adapter maintains a per-shop list of session IDs (key `shop:${shop}`) that `storeSession` appends to on every call, without checking whether the ID already exists in the list. This mirrors the reported bug class (`_beforeProviderOp` becoming unbounded and gas-exhausting as pending liquidations accumulate): a piece of state that grows monotonically with normal, repeatable protocol/application activity, is never pruned during the "write" path, and is then iterated in full by a security-relevant "read" path (`findSessionsByShop`), degrading and eventually breaking that operation.

### Finding Description
In `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`:

```ts
public async storeSession(session: Session): Promise<boolean> {
    await this.namespace.put(session.id, JSON.stringify(session.toPropertyArray(true)));
    await this.addShopIds(session.shop, [session.id]);
    return true;
}
...
private async addShopIds(shop: string, ids: string[]) {
    const key = this.getShopSessionIdsKey(shop);
    const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
    await this.namespace.put(key, JSON.stringify([...shopIds, ...ids]));
}
``` [1](#0-0) [2](#0-1) 

Unlike the equivalent Redis adapter, which explicitly de-duplicates before appending:
```ts
if (!idKeysArray.includes(idKey)) {
  idKeysArray.push(idKey);
  await this.client.set(shopKey, JSON.stringify(idKeysArray));
}
``` [3](#0-2) 

`KVSessionStorage.addShopIds` blindly concatenates, so re-storing a session with the same `session.id` (which happens routinely — offline sessions are re-stored on every OAuth re-auth/token exchange with a deterministic `offline_${shop}` ID, e.g. via `token-exchange.ts`'s `storeSession(offlineSession)` call) appends a duplicate entry every time: [4](#0-3) 

The resulting `shop:${shop}` list is read in full by `findSessionsByShop`, which loads every listed session ID:
```ts
public async findSessionsByShop(shop: string): Promise<Session[]> {
    const sessionIds = await this.namespace.get<string[]>(this.getShopSessionIdsKey(shop), {type: 'json'});
    if (!sessionIds) return [];
    return Promise.all(sessionIds.map(async (id) => (await this.loadSession(id))!));
}
``` [5](#0-4) 

`findSessionsByShop`/`deleteSessions` back the `AppInstallations` helper used to determine whether a shop has installed the app and to clean up on uninstall:
```ts
async includes(shopDomain: string): Promise<boolean> {
    const shopSessions = await this.sessionStorage.findSessionsByShop!(shopDomain);
    ...
}
``` [6](#0-5) 

### Impact Explanation
As the duplicate-laden list grows without bound (limited only by the KV value size limit, typically 25 MiB on Cloudflare Workers KV), every subsequent `findSessionsByShop`/uninstall cleanup call for that shop becomes slower and eventually fails once the value exceeds the KV size limit (write/read errors) or the number of parallel `loadSession` fetches triggered by `Promise.all` exhausts worker CPU/subrequest limits. Because nothing in the normal `storeSession` path prunes duplicates, this is a one-way ratchet: once the state is large, the app cannot self-heal, exactly analogous to the referenced `_beforeProviderOp` scenario where the protocol becomes permanently unable to process further liquidations. In shopify-app-express deployments backed by this storage adapter, this can degrade or break the `APP_UNINSTALLED` webhook cleanup handler (`deleteAppInstallationHandler` → `AppInstallations.delete` → `findSessionsByShop`/`deleteSessions`), leaving stale sessions/tokens undeleted and potentially causing failures in future authentication bookkeeping for that shop.

### Likelihood Explanation
Reaching this state does not require exploiting any cryptographic weakness or forging a request — it only requires the offline (or an online) session for a given shop to be re-stored repeatedly, which happens naturally through normal OAuth re-installs/token refresh/token-exchange flows that any merchant or authenticated app user can trigger repeatedly by re-invoking the app's OAuth or token-exchange endpoints. Because the growth is proportional to the number of times `storeSession` is called for the same session ID and there is no cap or de-duplication, an actor with only ordinary (non-privileged) access to a single shop's app instance can force unbounded growth over time.

### Recommendation
Modify `KVSessionStorage.addShopIds` to de-duplicate IDs before writing, matching the pattern used in `RedisSessionStorage.addKeyToShopList`:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```
Additionally, consider capping list size or migrating stale/duplicate lists on read, and add a regression test mirroring the existing `battery-of-tests.ts` suite that repeatedly stores the same session ID and asserts the shop-index list does not grow beyond expected size.

### Proof of Concept
1. Configure a Cloudflare Workers app using `@shopify/shopify-app-session-storage-kv`.
2. As an authenticated merchant/user of the app, repeatedly trigger the app's OAuth/token-exchange flow for the same shop (e.g., reload the embedded app or repeatedly hit `/auth` or the token-exchange endpoint N times), causing `storeSession` to be called N times with the same deterministic offline session ID (`offline_${shop}`).
3. Observe that the KV entry at key `shop:${shop}` grows to contain N duplicate copies of the same session ID (verifiable via `namespace.get('shop:' + shop, 'json')`).
4. Call `findSessionsByShop(shop)` (e.g., via `AppInstallations.includes`/`delete`) and observe increasing latency/resource usage proportional to N, until the KV value size limit is hit and writes/reads to that key begin failing — a persistent, non-recoverable degradation of session lookup for that shop.

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

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L156-162)
```typescript
    if (idKeysArrayString) {
      const idKeysArray = JSON.parse(idKeysArrayString);

      if (!idKeysArray.includes(idKey)) {
        idKeysArray.push(idKey);
        await this.client.set(shopKey, JSON.stringify(idKeysArray));
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

**File:** packages/apps/shopify-app-express/src/app-installations.ts (L22-31)
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
```
