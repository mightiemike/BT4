### Title
Unbounded, unde-duplicated growth of per-shop session-ID index in `KVSessionStorage` causes DoS of session lookup during normal/forced token-exchange re-authentication - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.storeSession()` records every stored session ID into a per-shop index array via `addShopIds()`, but unlike the Redis adapter's equivalent method, it never checks whether the ID is already present before appending. Because `storeSession()` is invoked on every offline/online token-exchange refresh cycle (which recreates a session with the *same* deterministic ID for a given shop/user), the shop's session-ID array grows by one duplicate entry on every re-authentication, without bound, purely through normal or attacker-accelerated request activity from a single authenticated merchant.

### Finding Description
`addShopIds` blindly concatenates new IDs onto the existing array read from KV storage: [1](#0-0) 

This is called unconditionally from `storeSession`: [2](#0-1) 

Contrast this with the Redis adapter, which explicitly de-duplicates before pushing to the shop-key list: [3](#0-2) 

`storeSession` is called with a deterministic, shop/user-scoped session ID (`getOfflineId(shop)` for offline tokens, `getJwtSessionId(shop, sub)` for online tokens) every time the request-authentication path performs a token exchange because no active session was found — i.e., whenever the previously stored offline/online token is missing, inactive, or expired: [4](#0-3) [5](#0-4) 

Online access tokens are short-lived (they expire routinely, e.g., roughly daily), so this refresh path executes repeatedly under ordinary app usage. A malicious or careless merchant/user can trivially accelerate this by invalidating their own session (e.g., triggering `invalidateAccessToken` on a 401, or simply issuing many authenticated requests as their current online token nears expiry) to force `storeSession` for the *same* session ID over and over. Because `addShopIds` never de-duplicates, the per-shop array in KV storage (`shop:<shop-domain>` key) accumulates one new entry per refresh, indefinitely.

`findSessionsByShop` then loops over this ever-growing array and issues one KV `get` per entry via `Promise.all`: [6](#0-5) 

This function is a documented, required part of the `SessionStorage` interface and is used directly in app-installation/auth-adjacent logic (e.g., `AppInstallations.includes`/`delete`, used to decide whether a shop needs to redirect back through OAuth): [7](#0-6) 

As the duplicate-laden array grows unbounded, `findSessionsByShop` performs increasingly many redundant KV reads for a single shop. On Cloudflare Workers (the KV runtime this adapter targets), this directly threatens the platform's per-request subrequest/CPU-time limits, resulting in the call failing or timing out — a denial of service of the session-lookup path that backs app-installation/auth-reinstallation checks for that shop.

### Impact Explanation
This is a DoS of a request-authentication-adjacent handler (`findSessionsByShop`, used by `AppInstallations`/auth reinstallation checks) reachable purely by a single authenticated merchant/customer repeatedly exercising the normal, unprivileged token-exchange re-authentication flow — no elevated privileges, secrets, or third-party dependency needed. The unbounded array growth mirrors the reported bug class exactly (permissionless, duplicate-prone appends to an array that is later iterated in a critical path), differing only in the trigger mechanism (session refresh cadence instead of arbitrary token registration).

### Likelihood Explanation
Likelihood is limited by the fact that this only affects the KV session-storage adapter (Cloudflare Workers-targeted, one of several optional storage backends), and the growth rate is bounded by how often a given session ID needs re-exchange (natural online-token expiry, or an attacker deliberately invalidating their own session/token repeatedly). It requires sustained, repeated activity over time rather than a single request, and only impacts the shop performing the requests (not cross-tenant). It's a genuine, low-cost self-DoS/griefing vector for anyone using `KVSessionStorage`, but is slower to manifest than a single-shot spam attack.

### Recommendation
In `KVSessionStorage.addShopIds` (and `removeShopIds` symmetrically), de-duplicate before writing back to the namespace, mirroring the check already present in `RedisSessionStorage.addKeyToShopList`:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```
Additionally consider capping/pruning stale IDs (e.g., during `findSessionsByShop`, drop entries whose `loadSession` returns `undefined`) to bound array size over the lifetime of a shop's installation.

### Proof of Concept
1. Configure an embedded app to use `KVSessionStorage` with `useOnlineTokens: true` and token-exchange authentication (`shopify-app-express`/`shopify-app-remix` strategies).
2. As an installed merchant/user, repeatedly cause the online session to be treated as inactive/expired before its natural expiry — e.g., trigger a 401 from the Admin API to invoke `invalidateAccessToken`, or simply wait out normal token expiry and issue a request — so that `performTokenExchange`/`createTokenExchangeStrategy.authenticate` re-runs `config.sessionStorage.storeSession(onlineSession)` with the same `id: getJwtSessionId(shop, sub)` on every cycle: [8](#0-7) 
3. Each call appends the identical ID again to the `shop:<domain>` KV entry via `addShopIds` without dedup: [1](#0-0) 
4. Repeat automatically (e.g., a script invalidating its own session and re-authenticating every few seconds) to accumulate an arbitrarily large ID array for that shop.
5. Call `findSessionsByShop(shop)` (directly, or via `AppInstallations.includes/delete`) and observe request time/read-count growing linearly with the number of duplicate entries, eventually exceeding Workers KV/subrequest limits and failing: [6](#0-5)

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

**File:** packages/apps/shopify-app-express/src/middlewares/perform-token-exchange.ts (L70-116)
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
