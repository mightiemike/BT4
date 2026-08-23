## Finding

### Title
Unbounded Duplicate Growth of Per-Shop Session ID Array Causes DoS in KV Session Storage - (File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts)

### Summary
`KVSessionStorage.addShopIds()` appends new session IDs to the `shop:${shop}` KV list on every call to `storeSession()` without checking whether the ID is already present. Because offline session IDs are deterministic (`offline_<shop>`), every OAuth install/reinstall or token refresh for the same shop re-appends the same ID, causing the per-shop index array to grow without bound. This is the same bug class as the reported `getRewardsWeight()` issue — an ever-growing array that is fully iterated/loaded by a downstream function (`findSessionsByShop`), eventually causing that function to fail, time out, or exceed storage size limits, thereby breaking the app's authentication/installation-check flow.

### Finding Description
`storeSession()` unconditionally calls `addShopIds(session.shop, [session.id])` on every session write: [1](#0-0) 

`addShopIds` simply concatenates the new ID(s) onto whatever list already exists, with no deduplication: [2](#0-1) 

Offline session IDs are deterministic per shop (`offline_<shop>`), and are computed by `getOfflineId`: [3](#0-2) 

Every time a merchant re-installs the app, re-authenticates, or the OAuth callback / token-exchange flow re-stores an offline session (which is a normal, unprivileged action a single merchant can trigger repeatedly, e.g. via `shopify.auth.callback` or `performTokenExchange`), the same session ID string is appended again to the shop's list in KV storage, rather than being deduplicated. Compare this to the equivalent Redis adapter, which correctly checks for existing membership before appending: [4](#0-3) 

The unbounded array is later fully materialized and iterated by `findSessionsByShop`, which loads and awaits every ID in the list: [5](#0-4) 

`findSessionsByShop` is a core dependency of the `AppInstallations` helper used to gate re-authentication/installation logic in `shopify-app-express`: [6](#0-5) 

### Impact Explanation
As the per-shop ID array grows unboundedly with duplicate entries, `findSessionsByShop` performs an ever-larger number of KV reads (`Promise.all` over `sessionIds.map(...)`), and the underlying `shop:${shop}` value itself grows toward Cloudflare KV's value size limits. This can cause `findSessionsByShop`/`AppInstallations.includes`/`AppInstallations.delete` to slow down, time out, or fail once the value exceeds storage limits — a denial of service of the app's installation-check and re-authentication path, mirroring the reported issue where an ever-growing array eventually makes the consuming function "always revert" / fail.

### Likelihood Explanation
This does not require any privileged actor — it is triggered purely by normal, expected merchant behavior (installing, reinstalling, or refreshing OAuth/token-exchange sessions for the same shop), which is explicitly a permitted single-merchant actor under the validation rules. No malicious intent is even required; ordinary usage over time (or a merchant/bot repeatedly hitting the OAuth callback) steadily degrades and can eventually break this storage path.

### Recommendation
In `addShopIds` (and the corresponding `removeShopIds`), deduplicate before writing, mirroring the pattern already used in the Redis adapter's `addKeyToShopList`:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```
Additionally, consider capping/pruning stale entries and reconciling the array against actually-existing session keys during `findSessionsByShop`.

### Proof of Concept
1. Configure an app using `@shopify/shopify-app-session-storage-kv` as `sessionStorage`.
2. As a normal merchant, complete OAuth install for `shop1.myshopify.com` — `storeSession` is called with `id = offline_shop1.myshopify.com`, appended to `shop:shop1.myshopify.com` → `["offline_shop1.myshopify.com"]`.
3. Repeat step 2 (re-install/reauthorize, or repeatedly trigger token exchange, which is a normal supported flow) N times.
4. Observe that `shop:shop1.myshopify.com` in KV grows to contain N duplicate copies of the same ID: `["offline_shop1.myshopify.com", "offline_shop1.myshopify.com", ...]`.
5. Call `findSessionsByShop('shop1.myshopify.com')` — it performs N redundant KV `get` calls via `Promise.all`, and as N grows large, this operation slows down and the underlying KV value approaches Cloudflare's per-value size limit, ultimately causing `put`/`get` failures and breaking `AppInstallations.includes`/`delete`, which apps rely on to decide whether to re-run OAuth.

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

**File:** packages/apps/shopify-api/lib/session/session-utils.ts (L22-26)
```typescript
export function getOfflineId(config: ConfigInterface) {
  return (shop: string): string => {
    return `offline_${sanitizeShop(config)(shop, true)}`;
  };
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
