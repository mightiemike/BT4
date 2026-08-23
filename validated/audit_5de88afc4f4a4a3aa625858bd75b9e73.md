### Title
Non-atomic read-modify-write race in Redis session storage's shop-index list leaves stale/irremovable session references, degrading `findSessionsByShop` (DoS of installation-check auth path) - ([File: packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts])

### Summary
`RedisSessionStorage` maintains a secondary per-shop index (`idKeysArray`) that lists the Redis keys of all sessions belonging to a shop. This index is updated with a classic "GET whole array → mutate in JS → SET whole array" sequence with no locking, `WATCH/MULTI`, or atomic Redis set/list primitive. When two of these operations race for the same shop (e.g. a session being stored while another session for the same shop is being deleted), the sequence is exactly analogous to the reported bug class: one operation's removal is silently lost because it is based on a stale snapshot of the array, so the deleted session's key remains listed in the shop's index forever — even though the actual session key has already been deleted from Redis. There is no other code path that will ever purge that dangling reference again.

### Finding Description
`addKeyToShopList` and `removeKeyFromShopList` both do:
1. `GET` the shop's JSON-encoded id list.
2. Parse it and mutate it in JS (`push` or `splice`).
3. `SET` the whole array back. [1](#0-0) 

`storeSession` (called on every OAuth/session-token exchange creating an online or offline session) calls `addKeyToShopList`, and `deleteSession` (called by webhook `APP_UNINSTALLED` processing and any app-triggered session cleanup) calls `removeKeyFromShopList`, then unconditionally deletes the underlying session key regardless of whether the index update actually succeeded: [2](#0-1) 

Because there is no transactional guard, if `storeSession` for session A and `deleteSession` for session B (same shop) interleave:
- `deleteSession(B)` reads the array `[A, B]`, splices out `B`, and is about to `SET [A]`.
- Concurrently, `addKeyToShopList` for a new session A′ (or a re-store of A) reads the *original* array `[A, B]`, appends A′, and writes `[A, B, A′]` back **after** the delete's write.
- Session B's Redis key is now deleted (`client.del(id)` still ran in `deleteSession`), but `B`'s reference is still present in the shop's persisted index list, because the delete's array write was overwritten by the racing store's write.

This is structurally identical to the reported bug: the "confirmation of removal" (`client.del(id)`) succeeds and the entity is gone from the authoritative store, but the auxiliary index array is not updated to reflect it (the analogue of `operatorNodesArray` retaining the stale `nodeId` after the validator was already deleted from `_registeredValidators`). Nothing in the codebase can ever clean up that dangling `idKey` again — every future `findSessionsByShop` call: [3](#0-2) 

will iterate the leaked entry, issue a wasted `GET`, get `null`, and `continue` — the entry can never be removed because no code path calls `removeKeyFromShopList` for a key that isn't currently associated with a live `Session` object being deleted through `deleteSession`. Over repeated store/delete churn on the same shop (which is routine — every online session created for a staff/customer request, refreshed tokens, and every `APP_UNINSTALLED`/reinstall cycle touches this array), the array grows without bound.

`findSessionsByShop` is the backbone of `AppInstallations.includes()` and `AppInstallations.delete()` in `shopify-app-express`, which gate whether an incoming (unauthenticated) request is treated as "app installed" and redirected to OAuth, and whether `APP_UNINSTALLED` webhook processing correctly purges a shop's sessions: [4](#0-3) 

### Impact Explanation
Because `findSessionsByShop` is on the hot path of the installation-check middleware that runs for every unauthenticated request to an embedded app (and is also part of `APP_UNINSTALLED` cleanup), an ever-growing, never-shrinking shop index causes:
- Linear-growth in per-request Redis round-trips for that shop's installation check, eventually causing request timeouts/latency blow-up (DoS of the auth/installation-check handler).
- `AppInstallations.delete()` (invoked from the `APP_UNINSTALLED` webhook) leaving orphaned sessions un-deleted from Redis because it operates off the same corrupted index, so old access tokens for a supposedly-uninstalled shop can remain retrievable — a data-hygiene/security regression as well as availability regression.

### Likelihood Explanation
Any app using `RedisSessionStorage` under realistic concurrent load (multiple staff/customers hitting the embedded app simultaneously, or an uninstall webhook racing with a fresh reinstall/session-token exchange for the same shop) will trigger overlapping `storeSession`/`deleteSession` calls for the same shop key. No special privilege is required — ordinary concurrent traffic from legitimate users of the same shop is sufficient to race the two non-atomic read-modify-write sequences.

### Recommendation
Replace the GET-mutate-SET pattern in `addKeyToShopList`/`removeKeyFromShopList` with an atomic Redis operation, e.g. use a Redis `SET` type (`SADD`/`SREM`) instead of a JSON array in a string key, or wrap the read-modify-write in a `WATCH`/`MULTI`/`EXEC` transaction (optimistic locking) so that concurrent updates cannot silently overwrite each other. This removes the possibility of "phantom" leaked session references analogous to the irremovable-node bug described in the report.

### Proof of Concept
```ts
// Pseudocode demonstrating the race in redis.ts
const storage = new RedisSessionStorage(redisUrl);

// Shop has one existing session "B"
await storage.storeSession(sessionB); // idKeysArray = [B]

// Race: concurrently store a new session A and delete B
await Promise.all([
  storage.storeSession(sessionA),   // reads [B], writes [B, A]  (may finish LAST)
  storage.deleteSession(sessionB.id) // reads [B], writes []      (may finish FIRST)
]);

// If storeSession's write lands after deleteSession's write:
// final idKeysArray == [B, A]  -- "B" is a dangling/leaked entry forever,
// even though `client.get(B.id)` now returns null (session B was `del`eted).

// Every subsequent findSessionsByShop(shop) will forever issue a wasted
// GET for B's key with no way to prune it, and the array only grows
// with further store/delete churn on that shop.
```

### Citations

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L90-120)
```typescript
  public async storeSession(session: Session): Promise<boolean> {
    await this.ready;

    await this.client.set(
      session.id,
      JSON.stringify(session.toPropertyArray(true)),
    );
    await this.addKeyToShopList(session);
    return true;
  }

  public async loadSession(id: string): Promise<Session | undefined> {
    await this.ready;

    let rawResult: any = await this.client.get(id);

    if (!rawResult) return undefined;
    rawResult = JSON.parse(rawResult);

    return Session.fromPropertyArray(rawResult, true);
  }

  public async deleteSession(id: string): Promise<boolean> {
    await this.ready;
    const session = await this.loadSession(id);
    if (session) {
      await this.removeKeyFromShopList(session.shop, id);
      await this.client.del(id);
    }
    return true;
  }
```

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L128-145)
```typescript
  public async findSessionsByShop(shop: string): Promise<Session[]> {
    await this.ready;

    const idKeysArrayString = await this.client.get(shop);
    if (!idKeysArrayString) return [];

    const idKeysArray = JSON.parse(idKeysArrayString);
    const results: Session[] = [];
    for (const idKey of idKeysArray) {
      const rawResult = await this.client.get(idKey, false);
      if (!rawResult) continue;

      const session = Session.fromPropertyArray(JSON.parse(rawResult), true);
      results.push(session);
    }

    return results;
  }
```

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L151-182)
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

  private async removeKeyFromShopList(shop: string, id: string) {
    const shopKey = shop;
    const idKey = this.client.generateFullKey(id);
    const idKeysArrayString = await this.client.get(shopKey);

    if (idKeysArrayString) {
      const idKeysArray = JSON.parse(idKeysArrayString);
      const index = idKeysArray.indexOf(idKey);

      if (index > -1) {
        idKeysArray.splice(index, 1);
        await this.client.set(shopKey, JSON.stringify(idKeysArray));
      }
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
