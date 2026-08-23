### Title
Unbounded serial loop in `RedisSessionStorage.findSessionsByShop` enables session-storage growth DoS - (File: `packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts`)

### Summary
`RedisSessionStorage.findSessionsByShop` iterates the full per-shop session-ID list with a sequential (non-parallel) `await` inside a `for` loop, performing one Redis round trip per stored session ID [1](#0-0) . The per-shop ID list (`idKeysArray`) only shrinks via explicit `deleteSession`/`removeKeyFromShopList` calls and grows by one entry every time a new session (in particular, a new *online* session per logged-in staff/customer user) is stored [2](#0-1) . This is structurally the same bug class as `revokeVotes`'s unbounded `dequeued` loop: a per-tenant array that is appended to indefinitely during normal use and is later walked in full, with cost proportional to its size, inside a code path invoked by ordinary app/session lifecycle events.

### Finding Description
Every `storeSession` call appends the new session id to the shop's Redis list without ever pruning entries as sessions naturally expire (only `deleteSession`, called explicitly, removes an entry) [3](#0-2) . For apps using online tokens, a new session is created for every user login, so the list for an active shop with many staff/customers grows continuously over the app's lifetime, mirroring how `dequeued` in `Governance.sol` grows over time with normal contract usage.

`findSessionsByShop` is consumed by `AppInstallations.includes`/`AppInstallations.delete` in the Express package, which in turn are used by the app-installation check and by the `APP_UNINSTALLED` webhook deletion handler [4](#0-3) [5](#0-4) . Because the loop performs one awaited network call per array entry rather than batching (e.g. `Promise.all` or `MGET`), the time/latency cost of the operation scales linearly with the number of sessions ever stored for that shop, with no cap analogous to Celo's `concurrentProposals`/lifetime-based bound on `dequeued`.

### Impact Explanation
As the per-shop session list grows through ordinary, unprivileged usage (repeated online-token logins), any code path that calls `findSessionsByShop` — notably the `APP_UNINSTALLED` webhook handler that deletes all shop sessions on uninstall — becomes progressively slower, eventually risking handler timeouts/blocking of the webhook processing pipeline for that request. This is a resource-exhaustion / availability degradation of an authentication/session-lifecycle handler, analogous to the reported `revokeVotes` gas-exhaustion issue, though here the constrained resource is request latency/CPU/Redis round-trips rather than gas.

### Likelihood Explanation
Reaching a practically significant list size requires sustained, natural usage over time (many distinct online sessions for one shop) rather than a single crafted request, and no automatic pruning of expired sessions exists in this adapter. This makes exploitation slow/organic rather than instantly triggerable by a single anonymous request, similar to how the original Celo report acknowledged the growth is bounded in practice by protocol-level limits — here there is no equivalent bound in `RedisSessionStorage`.

### Recommendation
Parallelize the per-ID lookups in `findSessionsByShop` (e.g., `Promise.all` or a Redis `MGET`) to avoid linear serial round-trips, and/or actively prune expired/stale session IDs from the shop's tracking list (e.g., during `storeSession`/`loadSession`, or via TTL-aware bookkeeping) so the list cannot grow unbounded over the app's lifetime.

### Proof of Concept
Not applicable as a single-request PoC — this is a growth-over-time condition: repeatedly call `storeSession` for the same shop with distinct online-token sessions (as would occur through normal repeated logins) without corresponding `deleteSession` calls, then invoke `findSessionsByShop`/`AppInstallations.delete` (e.g., via the `APP_UNINSTALLED` webhook) and observe that latency scales linearly with the number of accumulated session IDs, per the loop at [6](#0-5) .

### Citations

**File:** packages/apps/session-storage/shopify-app-session-storage-redis/src/redis.ts (L90-99)
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
