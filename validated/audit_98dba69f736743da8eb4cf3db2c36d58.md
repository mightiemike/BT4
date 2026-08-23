### Title
Unbounded duplicate growth of the per-shop session-ID index in `KVSessionStorage` can permanently break session storage (auth/token-exchange DoS) for a shop - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`)

### Summary
`KVSessionStorage.addShopIds()` appends a session id to the shop's session-index array on every `storeSession()` call **without checking whether the id is already present**. Because `storeSession()` is invoked on every OAuth callback, token-exchange, and session-refresh cycle — actions any authenticated (or repeatedly re-authenticating) merchant/customer triggers simply by using the embedded app — the same session id is pushed into the shop's index array over and over. The array is never deduplicated, trimmed, or capped, so it grows without bound until the serialized JSON value exceeds the underlying KV provider's per-value size limit, at which point `namespace.put()` starts throwing for that shop's index key. This permanently breaks `storeSession`, `deleteSession`, and `findSessionsByShop` for the affected shop, denying authentication/token-exchange for that merchant until an operator manually purges the corrupted KV entry.

### Finding Description
`storeSession()` always calls the private helper `addShopIds`: [1](#0-0) 

```
public async storeSession(session: Session): Promise<boolean> {
  await this.namespace.put(
    session.id,
    JSON.stringify(session.toPropertyArray(true)),
  );
  await this.addShopIds(session.shop, [session.id]);
  return true;
}
```

`addShopIds` unconditionally concatenates the new id onto the existing list, with no membership check: [2](#0-1) 

```
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  await this.namespace.put(key, JSON.stringify([...shopIds, ...ids]));
}
```

Compare this to the sibling `RedisSessionStorage` implementation, which explicitly checks for an existing entry (`idKeysArray.includes(idKey)`) before pushing, avoiding duplicate accumulation: [3](#0-2) 

The KV adapter lacks this guard entirely — an invariant ("an id is added to the index at most once") is silently assumed but never enforced, exactly the same class of bug as the referenced report: a bookkeeping operation (`add`) is performed unconditionally while the corresponding safeguard (checking whether the addition is redundant/would break the invariant) is missing, and the resulting corrupted state later causes routine operations (`storeSession`/`deleteSession`, i.e. the equivalent of "transfer"/"burn") to fail.

`storeSession()` for the *same offline session id* is re-invoked on nearly every authenticated request path that doesn't have a fresh, active session — e.g. the React Router/Remix token-exchange strategy calls `config.sessionStorage.storeSession(offlineSession)` whenever the existing session is inactive or close to expiry: [4](#0-3) 

Because offline session IDs are deterministic (`offline_${shop}`), and online session IDs are deterministic per user (`${shop}_${userId}`), each such refresh re-adds the *same* id string to the shop's KV index list rather than replacing an existing entry.

### Impact Explanation
Since `addShopIds` never deduplicates, the JSON array stored under key `shop:${shop}` grows by one entry every time `storeSession` runs for that shop (which happens routinely — e.g., short‑lived online access tokens, app reloads, webhook-triggered offline session refresh, or an attacker simply repeatedly hitting the app's authenticated entrypoint to force many token-exchange/refresh cycles). Once the serialized array exceeds the KV provider's maximum value size (e.g., Cloudflare Workers KV's 25 MiB limit), `namespace.put(key, ...)` will throw for that shop going forward. Since `addShopIds`/`removeShopIds` are awaited without any try/catch in `storeSession`/`deleteSession`, this failure propagates and blocks storing or deleting *any* session for that shop — effectively locking the merchant out of the app until an operator manually intervenes and clears/rebuilds the corrupted KV key. This is a genuine, unprivileged-actor-triggerable denial of service against the authentication/token-exchange handler, directly analogous to the referenced report's self-inflicted, unrecoverable-without-intervention lockout caused by a missing invariant check on a repeated bookkeeping operation.

### Likelihood Explanation
No special privileges are required — any merchant or app user whose interactions naturally trigger repeated `storeSession` calls for the same session id (frequent for online, short-lived access tokens, or via automated/scripted repeated app loads or token-exchange calls) will cause continuous, unbounded growth of the shop's KV index entry. An attacker with any legitimate embedded-app or token-exchange access to a shop can accelerate this deliberately by rapidly repeating authentication requests. This requires no secrets, no MITM, and no dependency vulnerability — only use of `@shopify/shopify-app-session-storage-kv` as shipped.

### Recommendation
In `addShopIds` (and analogously ensure `removeShopIds` handles absence gracefully), check whether the id is already present before appending, mirroring the Redis adapter's `includes` guard:

```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = new Set(shopIds);
  for (const id of ids) merged.add(id);
  await this.namespace.put(key, JSON.stringify([...merged]));
}
```
Additionally, consider bounding the maximum index size and/or storing the index as a proper set-like structure, and wrapping `addShopIds`/`removeShopIds` failures so a KV write error does not prevent `storeSession`/`deleteSession` from completing for the primary session record.

### Proof of Concept
1. Configure an embedded app using `KVSessionStorage` with `useOnlineTokens: true` (or rely on the offline-session refresh path).
2. As any authenticated merchant/user, repeatedly trigger the token-exchange/session-refresh path (e.g., reload the embedded app or call the authenticated endpoint) so that `storeSession` is invoked many times for the same session id (`offline_${shop}` or `${shop}_${userId}`).
3. Observe that the `shop:${shop}` KV entry's JSON array grows by one duplicate entry per call — confirmed by inspecting `addShopIds`, which has no dedup check, unlike `RedisSessionStorage.addKeyToShopList` at [3](#0-2) .
4. Continue automated repetition until the array's serialized size approaches the KV provider's maximum value size; subsequent `namespace.put(key, ...)` calls in `addShopIds`/`removeShopIds` begin throwing, causing `storeSession`/`deleteSession` to fail for that shop and blocking further authentication/token exchange until manual remediation.

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
