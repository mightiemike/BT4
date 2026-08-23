### Title
Unbounded per-shop session-ID list growth via repeated OAuth completions causes DoS of session-lookup/uninstall auth handlers - (File: `packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts`, `packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts`)

### Summary
Analogous to the ticket-manager bug (an unbounded per-user data structure that becomes expensive/impossible to iterate, causing an out-of-gas/DoS when a downstream function reads it "all at once"), the KV- and Redis-backed session storage implementations maintain an unbounded array of session IDs per shop that is appended to on every session creation, with no cap and no deduplication. This list is fully materialized/iterated by `findSessionsByShop`, which is invoked from unprivileged-reachable auth/webhook code paths (`AppInstallations.includes`/`delete`, used by the app-uninstalled webhook handler and `ensureInstalledOnShop`). A single shop/merchant can grow this list arbitrarily large by repeatedly completing OAuth for non-embedded apps (each completion mints a brand-new random session ID), eventually causing the reading/deleting handler to become extremely slow or to exhaust memory — a self-inflicted but externally-triggerable DoS of the auth-processing path for that shop.

### Finding Description
For non-embedded apps, `createSession` mints a fresh, unique online-session ID with no reuse: [1](#0-0) 

Because `getOnlineSessionProperties` falls back to `crypto.randomUUID()` when the app is not embedded, every completed OAuth flow for the same shop produces a brand-new session ID — nothing keys it to the associated user (unlike the embedded/JWT case which reuses `shop_userId`). There is no limit on how many times a shop can complete OAuth.

When each of these sessions is persisted, the KV session storage unconditionally appends the new ID to a per-shop list without deduplication or any bound: [2](#0-1) 

The Redis storage has the same unbounded-append pattern: [3](#0-2) 

This per-shop ID list is later fully iterated by `findSessionsByShop`, which loads every listed session from the underlying store — via `Promise.all` in the KV implementation and via a sequential `for` loop with an `await` for each entry in the Redis implementation: [4](#0-3) [5](#0-4) 

`findSessionsByShop` is reached from unprivileged/webhook-triggered auth code: the app-uninstall webhook handler calls `AppInstallations.delete(shop)`, which calls `findSessionsByShop` and then `deleteSessions` over every returned ID: [6](#0-5) [7](#0-6) 

This mirrors the root cause of the referenced report: a data structure whose size is controlled entirely by an unprivileged, repeatable action of a single actor (there, buying tickets; here, repeatedly completing OAuth for one's own shop) is later read/iterated in full by a critical handler (there, `ownerOf` inside `propagateWinner`; here, `findSessionsByShop`/`deleteSessions` inside the uninstall webhook handler and any code path calling `AppInstallations.includes`), with no cap enforced by the library.

### Impact Explanation
A merchant (or anyone able to drive the shop through the OAuth flow repeatedly, which requires no elevated privilege — just triggering `/auth` for a non-embedded app configuration) can create thousands of orphaned online sessions for their own shop. When the app later processes the `APP_UNINSTALLED` webhook (or any code that calls `AppInstallations.includes`/`delete`, e.g. reinstall checks), the handler must load and then sequentially delete every one of these sessions. In the Redis adapter this is a strictly sequential per-ID network round trip; in the KV adapter it is an unbounded `Promise.all` fan-out. This can make the webhook/auth handler processing for that shop unacceptably slow or resource-exhausted, effectively DoS-ing that handler for the affected shop (and, depending on deployment, contending for capacity used by the webhook endpoint that serves all shops).

### Likelihood Explanation
Likelihood depends on app configuration: it only manifests for non-embedded apps using online access tokens (or any custom flow generating new session IDs per login) with the KV or Redis session storage adapters, since these are the adapters observed with unbounded, non-deduplicated per-shop ID lists. No admin misconfiguration of array-length-style parameters is required — the number of OAuth completions is limited only by how many times the actor is willing to authenticate, similar to the referenced report where the ticket count was limited only by `type(uint16).max`.

### Recommendation
- Deduplicate and/or cap the per-shop session-ID list in `KVSessionStorage.addShopIds` and `RedisSessionStorage.addKeyToShopList` (e.g., use a set-like structure, or evict/expire old online session IDs when a new one is added for the same user/shop).
- Reuse a stable session ID for online sessions in non-embedded apps (e.g., derived from `shop`+`associated_user.id`) instead of `crypto.randomUUID()`, matching the embedded/JWT behavior, so re-authentication overwrites rather than appends.
- Bound `findSessionsByShop`/`deleteSessions` operations with batching/pagination and enforce a maximum list size with periodic pruning of expired sessions.

### Proof of Concept
1. Configure a non-embedded app instance using `KVSessionStorage` (or `RedisSessionStorage`) as `sessionStorage`.
2. For the same shop, repeatedly drive the OAuth flow to completion with online access tokens (each run calls `createSession`, which returns a new `crypto.randomUUID()` session ID because `config.isEmbeddedApp` is false) — see `getOnlineSessionProperties` in [1](#0-0) .
3. Each completed flow calls `storeSession`, which appends the new ID to the shop's list without dedup, per [8](#0-7)  and [2](#0-1) .
4. After accumulating a large number of session IDs (e.g., tens of thousands), trigger `APP_UNINSTALLED` for the shop, invoking `deleteAppInstallationHandler` → `AppInstallations.delete(shop)` → `findSessionsByShop` (loads every session) → `deleteSessions` (deletes every session), per [9](#0-8) .
5. Observe substantially degraded/failing processing time for the webhook/auth handler proportional to the number of accumulated session IDs.

### Citations

**File:** packages/apps/shopify-api/lib/auth/oauth/create-session.ts (L33-40)
```typescript
  const getOnlineSessionProperties = (responseBody: OnlineAccessResponse) => {
    const {access_token: _access_token, scope: _scope, ...rest} = responseBody;
    const sessionId = config.isEmbeddedApp
      ? getJwtSessionId(config)(
          shop,
          `${(rest as OnlineAccessInfo).associated_user.id}`,
        )
      : crypto.randomUUID();
```

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
