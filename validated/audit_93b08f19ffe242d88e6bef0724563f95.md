### Title
Unbounded growth of shop-session-ID list causes DoS in `KVSessionStorage.findSessionsByShop` / `deleteSessions` used by `AppInstallations` - (File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts)

### Summary
`KVSessionStorage` keeps a per-shop index (`shop:{shop}`) of session IDs used to answer `findSessionsByShop`. This index is only ever appended to and is never deduplicated, so a single merchant repeatedly re-authenticating (OAuth reinstall, online-token refresh, embedded app reload) causes the array to grow without bound. `findSessionsByShop` then loops over the entire array performing one KV `get` per entry, so its cost grows linearly (unbounded) with the number of historical `storeSession` calls for that shop, matching the "unbounded loop over dynamically sized collection" bug class from the report.

### Finding Description
`storeSession` calls `addShopIds`, which reads the current `shop:{shop}` array and blindly appends the new session id, with no check for whether that id (or an equivalent id for the same offline/online session) is already present: [1](#0-0) 

Because `storeSession` is invoked on every OAuth callback / token exchange and on every online-session mint, and this same session key can legitimately be re-stored many times over an app's lifetime (re-installs, session refreshes, multiple staff/online sessions per shop), the `shop:{shop}` id list accumulates duplicate/stale entries indefinitely — there is no compaction path for `storeSession`, only `removeShopIds` on explicit `deleteSession`.

`findSessionsByShop` then iterates this ever-growing array, issuing a KV `get` for every stored id via `Promise.all`: [2](#0-1) 

This function is the one exposed by the generic `SessionStorage` interface and consumed directly by `AppInstallations.includes`/`AppInstallations.delete`: [3](#0-2) 

`AppInstallations.delete` is invoked directly from the `APP_UNINSTALLED` webhook handler with no bound on how large the shop's session id list may have grown: [4](#0-3) 

Since the array is only ever appended by ordinary, unprivileged app usage (a merchant simply loading/reinstalling the app repeatedly triggers `storeSession`→`addShopIds`), a single merchant/customer of the app, with no elevated privileges, can grow their own `shop:{shop}` KV value until `findSessionsByShop` (and the `deleteSessions`/`includes` calls built on it) becomes slow or fails outright when the request-handling runtime's execution/time limits (e.g. Cloudflare Workers CPU-time limits, which is the target runtime for this KV adapter) are exceeded.

### Impact Explanation
This directly threatens auth-adjacent handlers: `AppInstallations.includes` gates whether a shop is considered installed, and `AppInstallations.delete` is the cleanup routine invoked by the `APP_UNINSTALLED` webhook. If the underlying `shop:{shop}` id array grows large enough, `findSessionsByShop` (and the dependent `deleteSessions` call) can time out or become prohibitively slow, causing the uninstall webhook handler to fail to clean up sessions (leaving stale/dangling session records) and degrading or denying availability of any code path that relies on `findSessionsByShop` for that shop — a Denial of Service condition rooted in an unbounded loop, consistent with the accepted "DoS of an auth handler" impact category.

### Likelihood Explanation
The condition arises from completely normal, unprivileged app usage — no attacker input or malicious payload is required, only repeated `storeSession` calls for the same shop over time (reinstalls, online-session refreshes, multiple staff logins), all of which are legitimate flows any merchant can trigger. Because there is no cap, no deduplication, and no periodic compaction of the `shop:{shop}` list, the array size is monotonically non-decreasing for any shop that reinstalls or refreshes tokens repeatedly, making this a realistic, low-effort-to-trigger condition over the lifetime of an installation.

### Recommendation
- In `addShopIds`, deduplicate before writing (e.g. use a `Set` of ids, or check `includes` before appending) so re-storing the same session id does not grow the list.
- Consider storing shop session membership as a set-like structure, or pruning ids from the shop index whenever the underlying session key is found to be missing/expired during `findSessionsByShop`.
- Cap the number of tracked ids per shop or expire/garbage-collect stale ids so `findSessionsByShop`/`deleteSessions` cost stays bounded regardless of how many times a shop has authenticated over its lifetime.

### Proof of Concept
1. Configure an app to use `KVSessionStorage`.
2. As a normal merchant, repeatedly complete the OAuth flow (or refresh online tokens) for the same shop N times, e.g. by reinstalling the app or repeatedly loading the embedded app so new online sessions are minted — each call to `storeSession` appends to `shop:{shop}` via `addShopIds` (`kv.ts` lines 75-79) without deduplication.
3. Observe that the KV value at key `shop:{shop}` grows linearly with N and is never compacted.
4. Call `findSessionsByShop(shop)` (e.g., via `AppInstallations.includes`/`delete`, reachable from the `APP_UNINSTALLED` webhook handler) and observe latency growing linearly with N, since it performs one `namespace.get` per tracked id (`kv.ts` lines 56-69) — for sufficiently large N this exceeds the platform's execution-time limits, causing the auth-installation-check/cleanup handler to fail.

### Citations

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
