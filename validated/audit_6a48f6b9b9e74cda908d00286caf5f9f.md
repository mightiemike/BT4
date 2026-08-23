The analog bug class here—failing to remove/adjust prior accounting state when new state is recorded, causing duplication—maps to the `KVSessionStorage` implementation's shop-session index management.

### Title
Unbounded duplicate session ID accumulation in Cloudflare KV session storage index - ([File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts])

### Summary
`KVSessionStorage.storeSession` calls `addShopIds(session.shop, [session.id])` on every single `storeSession` invocation, without ever checking whether that session ID is already present in the shop's index array before appending it, unlike the analogous Redis implementation.

### Finding Description
`storeSession` is the entry point that both the OAuth callback handler and the token-exchange/online-token-refresh flows use to persist a `Session` (see `packages/apps/shopify-app-express/src/auth/auth-callback.ts` and `packages/apps/shopify-app-remix/src/server/authenticate/admin/strategies/auth-code-flow.ts`, both of which call `config.sessionStorage!.storeSession(session)` on every OAuth callback). In the KV adapter: [1](#0-0) 

`addShopIds` unconditionally concatenates the new id(s) into the existing array with no de-duplication check: [2](#0-1) 

This is the same bug class as the reported `adjustInvestorCountsAfterCountryChange` issue: state is only ever additively updated without first reconciling/removing the previous entry for the same key (`session.id`), so repeated `storeSession` calls for the *same* session id (which happens routinely — e.g. re-running OAuth for the same shop/user, refreshing online tokens, or any flow that calls `storeSession` more than once with an existing session id) keep appending duplicate ids to the `shop:<shop>` index array.

By contrast, the Redis adapter's equivalent function explicitly guards against this: [3](#0-2) 

### Impact Explanation
Because `storeSession` is invoked on every OAuth callback/token refresh for a shop or online user (an action reachable via a normal, unprivileged app-install / re-auth / token-exchange flow, not requiring any elevated privilege), the `shop:<shop>` KV entry grows unbounded with duplicate ids over time. Consequences:
* `findSessionsByShop` returns duplicated entries for the same session id, corrupting session bookkeeping used by consuming apps (e.g. code that iterates "all sessions for a shop" to revoke/rotate tokens may operate on stale/duplicate records).
* The KV value has size limits (Cloudflare KV values are capped), so an attacker or even normal repeated legitimate re-authentication traffic can eventually push the array past storage limits, causing `JSON.stringify`/`put` failures and effectively bricking session storage (and thus the auth handler) for that shop — a storage-exhaustion DoS reachable from repeated, unprivileged OAuth/token-refresh requests.
* `deleteSession`/`removeShopIds` only removes a single matching id string via `filter`, so duplicates for a since-deleted session id may leave residual stale entries if `removeShopIds` runs on a snapshot concurrently with another `addShopIds` write (no locking), compounding the corruption similar to how the original report's missing decrement corrupted investor counters.

### Likelihood Explanation
`storeSession` is called on the hot path of OAuth/token-exchange handling in every framework package (`shopify-app-express`, `shopify-app-remix`), so any shop performing normal re-installation, token refresh, or multiple online-token logins by different staff users will trigger this repeatedly. No privileged access or attacker cooperation beyond normal app usage over time is required to trigger unbounded growth, though a malicious actor could accelerate it by repeatedly initiating OAuth for the same shop/session id.

### Recommendation
Mirror the Redis adapter's de-duplication logic in `addShopIds`: check `if (!shopIds.includes(id))` before appending each id, e.g.:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```

### Proof of Concept
```ts
const kv = new KVSessionStorage(fakeKVNamespace);
const session = new Session({id: 'sess_1', shop: 'shop.myshopify.com', state: 's', isOnline: false});

await kv.storeSession(session); // shop:shop.myshopify.com -> ["sess_1"]
await kv.storeSession(session); // shop:shop.myshopify.com -> ["sess_1", "sess_1"]  <-- duplicate, unbounded on repeat

const sessions = await kv.findSessionsByShop('shop.myshopify.com');
// sessions.length === 2, both referring to the same session id
```

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
