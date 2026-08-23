### Title
Unbounded duplicate accumulation in `KVSessionStorage.addShopIds()` causes DoS of shop-session lookups - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.storeSession()` unconditionally appends the session id to the per-shop id list on every call without checking for existing entries, unlike the equivalent Redis and file-based implementations. Any low-privilege actor who can repeatedly trigger session storage (e.g., repeated OAuth/token-exchange or online-session refresh calls for the same shop/user) can grow this per-shop array without bound, which is then iterated over in full by `findSessionsByShop()` and downstream consumers such as `AppInstallations.includes`/`delete`.

### Finding Description
`addShopIds` reads the current shop id list and blindly concatenates the new id(s), with no deduplication and no cap: [1](#0-0) 

This is called unconditionally from `storeSession` every time a session is persisted: [2](#0-1) 

Contrast this with `RedisSessionStorage.addKeyToShopList`, which explicitly checks `if (!idKeysArray.includes(idKey))` before pushing, preventing duplicate growth on repeated stores of the same id: [3](#0-2) 

Because online session ids for embedded apps are deterministic per shop+user (`${shop}_${userId}`, built by `getJwtSessionId`), a single authenticated user can cause the app to call `storeSession` repeatedly for the *same* session id (e.g., via repeated session-token exchanges, app reloads, or repeated OAuth callback hits) without ever changing the underlying id: [4](#0-3) [5](#0-4) 

Every such call appends another duplicate entry to the `shop:${shop}` KV array with no bound. `findSessionsByShop()` then loads every id in that array (duplicates included) via `Promise.all`: [6](#0-5) 

`findSessionsByShop` is invoked from operational/auth-adjacent code paths such as `AppInstallations.includes`/`delete` (used to check/clean up shop installation state, e.g. on uninstall or app/uninstalled webhook processing): [7](#0-6) 

This is structurally the same bug class as the Union Finance `vouchers[]` issue: an unprivileged/low-privilege actor can cheaply and repeatedly trigger appends to an array that is later fully iterated by another, more consequential operation, with no bound or dedup check.

### Impact Explanation
Once the per-shop id array grows large, `findSessionsByShop` must sequentially issue one KV `get` per (duplicate) id. On Cloudflare Workers KV, this consumes per-request subrequest/CPU budget; a sufficiently large array can cause `findSessionsByShop` calls to exceed platform limits or worker time limits, causing errors/timeouts. Since `findSessionsByShop`/`deleteSessions` back `AppInstallations.includes`/`delete`, this can break app-uninstall bookkeeping and other flows that rely on enumerating a shop's sessions — a denial of service against legitimate shop-management operations, triggerable by a single normal user/session refresh loop rather than any privileged actor.

### Likelihood Explanation
Likelihood is moderate: it requires the app to be configured with the `KVSessionStorage` adapter (Cloudflare Workers deployments) and requires an actor to repeatedly cause `storeSession` to run for the same shop (e.g., scripting repeated hits to the auth/callback or session-token exchange endpoint, or an app that stores the session on every request). No special privilege beyond normal merchant/customer app usage/API access is needed, and the array itself has no size cap or TTL-driven pruning, so growth is monotonic without corresponding `deleteSession` calls.

### Recommendation
Mirror the Redis adapter's behavior: check for existing membership (or use a Set) before appending in `addShopIds`, and/or cap and periodically prune the per-shop id list, removing ids that no longer resolve via `loadSession`. Consider deduplicating on read in `findSessionsByShop` as a defense-in-depth measure.

### Proof of Concept
1. Deploy an app using `KVSessionStorage` for a shop `test.myshopify.com` with embedded, JWT-based online sessions (`getJwtSessionId` → id `test.myshopify.com_<userId>`).
2. As a normal authenticated user of the app, repeatedly trigger any code path that calls `sessionStorage.storeSession(session)` for that same session id (e.g., replay a captured session-token exchange request, or repeatedly reload the embedded app if it stores the session per request) N times.
3. Inspect the KV key `shop:test.myshopify.com` — it now contains N duplicate copies of the same session id, because `addShopIds` never deduplicates: [1](#0-0) 
4. Call `findSessionsByShop('test.myshopify.com')` (directly, or indirectly via `AppInstallations.includes/delete`) — it issues N KV reads via `Promise.all`, and for large N this exceeds platform subrequest/time budgets, causing the call to fail or time out, denying that shop's session-management/uninstall-processing operations.

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
