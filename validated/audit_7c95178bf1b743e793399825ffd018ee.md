### Title
Unbounded, unbounded-deduplication growth of the KV per-shop session-id list causes permanent DoS of `storeSession`/authentication for a shop - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
The `KVSessionStorage` adapter tracks all session IDs belonging to a shop in a single KV value (`shop:{shop}`). Unlike the Redis adapter, it never checks whether an ID is already present before appending, so every `storeSession()` call for the *same* session id keeps growing that list. Because `storeSession` is invoked on every OAuth/token-exchange re-authentication (including for the deterministic, non-changing offline session id `offline_{shop}`), an attacker who repeatedly forces the app's token-exchange path to run can grow this list without bound until the underlying KV value exceeds the store's size limit, causing `put()` to fail and permanently breaking session storage (and therefore authentication) for that shop.

### Finding Description
`KVSessionStorage.storeSession` writes the session and then calls the private `addShopIds` helper to keep an index of session ids per shop: [1](#0-0) 

`addShopIds` simply appends the new id(s) to whatever list already exists, with **no de-duplication check**: [2](#0-1) 

This is a structural regression compared to the Redis adapter, which explicitly guards against duplicate insertion (`if (!idKeysArray.includes(idKey))`) before pushing to the per-shop key list: [3](#0-2) 

`storeSession` is not a rare, privileged operation — it is invoked on the normal, unauthenticated-reachable token-exchange path every time an app request arrives without an active offline session. In `shopify-app-remix`/`shopify-app-react-router`/`shopify-app-express`, whenever a request's session is missing or not "active" (e.g., a short-lived/expired/replayed session token, or one deliberately close to expiry), the offline token is re-exchanged and `storeSession(offlineSession)` is called again with an **identical, deterministic id** (`offline_{shop}`): [4](#0-3) [5](#0-4) 

Each such call appends `offline_{shop}` to the `shop:{shop}` KV list again, growing it unboundedly with repeated re-authentication attempts. A KV backend (e.g., Cloudflare Workers KV) enforces a maximum value size (25 MiB); once the accumulated JSON array of duplicate IDs exceeds that limit, `namespace.put` throws, and every subsequent `storeSession`/`findSessionsByShop`/`deleteSession` call for that shop key fails — permanently breaking authentication (token exchange can no longer persist tokens) for that shop until an operator manually purges the bloated KV key.

This mirrors the reported bug class exactly: a data structure that grows once per "checkpoint" call (here, once per `storeSession` invocation) without deduplication or a bound, reachable by repeatedly triggering a routine, attacker-reachable operation (here, forcing re-authentication/token-exchange), eventually causing an unrecoverable failure (DoS) of a core function (session persistence/authentication) instead of a withdraw.

### Impact Explanation
Once the per-shop KV list is bloated past the backend's value size limit, `storeSession` throws for that shop. Since `storeSession` sits directly in the authentication/token-exchange code path used by every app request, this causes a persistent denial of service of the app's authentication for the affected shop — new offline/online tokens can no longer be stored, and `findSessionsByShop`/session lookups used elsewhere (e.g., webhook/session housekeeping) also fail once the value can no longer be read or written. This matches "DoS of an auth handler" and constitutes a permanent freeze requiring manual remediation (deleting/rewriting the corrupted KV key), analogous to the "griefing" classification in the source report.

### Likelihood Explanation
Exploitation only requires the ability to repeatedly force the app's token-exchange/offline re-authentication flow for a specific shop — something reachable by any party who can send requests carrying a (even validly-signed) session token that the app treats as inactive/expired, or simply by automating many rapid app loads/re-installs for a shop. No admin/privileged access is needed; it is a self-inflicted or attacker-inflicted growth via a routine, frequently-executed code path (`storeSession` on every token exchange), unlike the original report's need for enormous transaction counts against a fixed billion-sized array — here the KV size ceiling (tens of MB) is reachable with far fewer repeated calls because each stored id is a full string appended without bound.

### Recommendation
In `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`, make `addShopIds` idempotent by checking for existing ids before appending (mirroring the Redis adapter's guard), e.g.:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = new Set([...shopIds, ...ids]);
  await this.namespace.put(key, JSON.stringify([...merged]));
}
```
Additionally consider bounding/cleaning the per-shop id list (e.g., pruning ids for sessions that no longer exist) so that repeated `storeSession` calls for the same id can never cause unbounded growth.

### Proof of Concept
1. Configure an app using `@shopify/shopify-app-session-storage-kv` as its `SessionStorage`.
2. Trigger the token-exchange path repeatedly for the same shop such that `session.isActive()` evaluates false each time (e.g., replay/short-lived session tokens, or simply call `config.sessionStorage.storeSession(offlineSession)` directly in a loop with `offlineSession.id === 'offline_{shop}'`), as done in the authenticate flow: [6](#0-5) 
3. Observe that `KVSessionStorage.addShopIds` appends `offline_{shop}` to the `shop:{shop}` KV value on every iteration without dedup: [2](#0-1) 
4. After enough iterations the JSON-serialized array exceeds the KV backend's per-value size limit; `namespace.put` throws, and all further `storeSession` calls for that shop fail, breaking authentication for the shop.

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

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/token-exchange.ts (L54-68)
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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L84-101)
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
```
