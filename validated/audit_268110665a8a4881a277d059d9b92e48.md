### Title
Unbounded growth of `shop:<shop>` session-id list in `KVSessionStorage` allows an authenticated merchant/user to DoS `findSessionsByShop`/`AppInstallations` - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.storeSession` appends the newly-stored session's id to a per-shop index list (`shop:<shop>`) on every call, with no deduplication and no size bound. Because online session ids for embedded apps are deterministic (`${shop}_${userId}`), a normal re-authentication/session-token refresh from a single merchant user causes the same id to be pushed again and again, growing the list without limit. `findSessionsByShop` then iterates the entire list and issues one KV `get` per id, so this array both risks exceeding Cloudflare KV's per-value size limit and turns any use of `findSessionsByShop` (e.g. `AppInstallations.includes`/`delete`, used by `ensureInstalledOnShop` and the `app/uninstalled` webhook handler) into an unbounded, ever-slower operation, and eventually a failure/DoS.

### Finding Description
`storeSession` unconditionally appends the session id: [1](#0-0) [2](#0-1) 

There is no `includes()` guard before pushing, unlike the Redis adapter which explicitly checks for duplicates before appending: [3](#0-2) 

Online session ids for embedded apps are deterministic per shop+user (`${shop}_${userId}`), generated during every OAuth/token-exchange completion: [4](#0-3) [5](#0-4) 

This means a single authenticated merchant user (or an app user with `associated_user.id`) can repeatedly trigger `storeSession` with the identical session id (e.g. by repeating the standard token-exchange/OAuth re-auth flow that any embedded app performs on session-token refresh), and each call appends a duplicate id to the `shop:<shop>` KV entry with no cap.

`findSessionsByShop` reads that unbounded array and performs a `Promise.all` fan-out of one KV `get` per id: [6](#0-5) 

This is used by `AppInstallations.includes`/`delete`, which back `ensureInstalledOnShop` (an auth-adjacent middleware gating access to the embedded app) and the `app/uninstalled` webhook cleanup handler: [7](#0-6) [8](#0-7) 

As the array grows: (1) `addShopIds`'s `JSON.stringify` payload approaches/exceeds the KV value size limit, causing `storeSession`/`addShopIds` writes to fail for that shop going forward, and (2) `findSessionsByShop` performs an increasingly large number of sequential-per-id KV reads, degrading and eventually failing the `app/uninstalled` webhook handler and the `ensureInstalledOnShop` installation-check path for that shop — a direct structural analog of the reported unbounded-array loop causing DoS/frozen state, but here manifesting as a stuck/failing session-index rather than frozen funds.

### Impact Explanation
A single merchant (unprivileged, non-privileged actor performing normal OAuth/token-exchange flows) can degrade or break session-index operations for their own shop by repeatedly completing authentication, without any admin/privileged action. This can lead to failures in `findSessionsByShop`-dependent flows (install-check middleware, uninstall webhook cleanup), and can permanently corrupt/prevent further writes to that shop's session index once the KV value size limit is hit — a form of DoS of an auth-adjacent handler that matches the accepted analog class ("DoS of an auth handler").

### Likelihood Explanation
Likelihood is moderate-to-high: it requires no special privilege, exploit chain, or leaked secret — only repeatedly going through the app's normal login/session-token exchange for the same shop/user, which is trivially automatable by any merchant or embedded-app user with API access. Only apps using the `shopify-app-session-storage-kv` adapter are affected; other adapters (Redis, DynamoDB, SQL-based) query/delete via a shop-indexed column/query rather than an unbounded array they must dedupe manually.

### Recommendation
- In `KVSessionStorage.addShopIds`, check for existing membership (`if (!shopIds.includes(id))`) before appending, mirroring the Redis adapter's `addKeyToShopList` behavior.
- Consider pruning/removing stale/expired session ids from the shop index during `storeSession`/`findSessionsByShop`, or cap the index size.
- Consider storing the shop→session index as a Set-like structure or moving to per-id keys with a scan-based lookup to avoid growth of a single KV value.

### Proof of Concept
1. Configure an embedded app to use `@shopify/shopify-app-session-storage-kv`.
2. As a normal merchant user, repeatedly complete the OAuth/token-exchange flow (or simulate by directly calling `storeSession` many times with a `Session` whose `id` is fixed to `${shop}_${userId}`).
3. Observe that `namespace.get(this.getShopSessionIdsKey(shop))` grows by one entry per call with duplicate ids (no dedup check), since `addShopIds` in [2](#0-1) 
unconditionally spreads new ids onto the existing array.
4. Continue until the JSON-stringified array approaches the KV value size limit; subsequent `storeSession` calls for that shop begin failing, and `findSessionsByShop` (and therefore `AppInstallations.includes`/`delete`) takes increasingly long or errors out due to the number of `loadSession` calls fanned out per shop.

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

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L16-20)
```typescript
export function getJwtSessionId(config: ConfigInterface) {
  return (shop: string, userId: string): string => {
    return `${sanitizeShop(config)(shop, true)}_${userId}`;
  };
}
```

**File:** packages/apps/shopify-api/lib/auth/oauth/create-session.ts (L33-47)
```typescript
  const getOnlineSessionProperties = (responseBody: OnlineAccessResponse) => {
    const {access_token: _access_token, scope: _scope, ...rest} = responseBody;
    const sessionId = config.isEmbeddedApp
      ? getJwtSessionId(config)(
          shop,
          `${(rest as OnlineAccessInfo).associated_user.id}`,
        )
      : crypto.randomUUID();

    return {
      id: sessionId,
      onlineAccessInfo: rest,
      expires: getSessionExpiration(rest.expires_in),
    };
  };
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
