This confirms a valid analog. The `APP_UNINSTALLED` webhook (an authenticated but merchant-triggered, HTTP-delivered request) triggers `deleteAppInstallationHandler` → `AppInstallations.delete(shop)`, which relies on `findSessionsByShop` to enumerate all sessions for a shop before deleting them. In the Prisma session storage adapter, this query has a hardcoded `take: 25` limit, so if a shop has accumulated more than 25 stored sessions (e.g., many online per-user sessions from staff accounts), only the 25 most-recently-expiring sessions are returned and deleted — the rest remain in storage indefinitely, with valid access tokens, after the merchant uninstalls the app.

### Title
Hardcoded `take: 25` limit in Prisma `findSessionsByShop` causes incomplete session/token deletion on app uninstall - (File: `packages/apps/session-storage/shopify-app-session-storage-prisma/src/prisma.ts`)

### Summary
The Prisma session storage adapter's `findSessionsByShop` caps results at 25 rows via a hardcoded `take: 25`, with no pagination or iteration to fetch the remainder. This method is the sole mechanism used by `AppInstallations.delete()` to discover which sessions to delete after the merchant uninstalls the app.

### Finding Description
`findSessionsByShop` queries only the first 25 sessions ordered by `expires desc`: [1](#0-0) 

This is directly analogous to the reported bug class: a fixed, non-configurable retrieval limit (1024 delegations vs. 25 sessions) combined with the assumption that a single entity will never exceed it, silently truncating results with no error surfaced.

`AppInstallations.delete()` uses `findSessionsByShop` as the exhaustive list of sessions to remove: [2](#0-1) 

This is invoked from the built-in `APP_UNINSTALLED` webhook handler, reachable via an HTTP webhook delivery from Shopify (triggered by a merchant uninstalling the app), without any code path revisiting sessions beyond the first page: [3](#0-2) [4](#0-3) 

Note that other session storage adapters in the same monorepo (`shopify-app-session-storage-mysql`, `-postgresql`, `-sqlite`, `-mongodb`, `-redis`, `-dynamodb`, `-drizzle-*`, `-kv`) implement `findSessionsByShop` without any such cap, returning the full result set. Only the Prisma adapter applies this arbitrary `take: 25` truncation, confirming it is an implementation defect rather than an intentional interface contract.

### Impact Explanation
For any app using `@shopify/shopify-app-session-storage-prisma` where a shop accumulates more than 25 stored sessions (a realistic scenario for a large merchant with many staff members each holding online sessions, plus the offline session, refreshed/rotated tokens, etc.), uninstalling the app will leave more than 25-minus-N sessions — including valid `accessToken`s — permanently in the database. `AppInstallations.includes()` (used by `ensureInstalled` billing/installation checks) is subject to the same truncation, so it may also produce a false positive/negative "installed" determination purely based on ordering. Retained access tokens after uninstall represent a stale-credential/data-retention security exposure: tokens that should be revoked/purged remain usable in storage indefinitely.

### Likelihood Explanation
Triggering this requires no privileged access — it happens automatically whenever Shopify sends the standard `APP_UNINSTALLED` webhook for any shop that happens to have accumulated more than 25 sessions, which is an ordinary occurrence for larger merchant installs with multiple staff accounts using online tokens.

### Recommendation
Remove the hardcoded `take: 25` limit in `findSessionsByShop` (`packages/apps/session-storage/shopify-app-session-storage-prisma/src/prisma.ts`), or paginate through all matching rows and aggregate the full result set before returning, matching the behavior of the other session storage adapters in this monorepo.

### Proof of Concept
1. Configure an app with `PrismaSessionStorage` and simulate a shop with 30 stored sessions (e.g., 29 online sessions for different staff users plus 1 offline session), all sharing the same `shop` value.
2. Trigger the `APP_UNINSTALLED` webhook for that shop (as Shopify would do on merchant uninstall), which invokes `deleteAppInstallationHandler` → `AppInstallations.delete(shop)`.
3. Observe that `findSessionsByShop` returns only 25 rows (ordered by `expires desc`), so `deleteSessions` is called with only those 25 IDs.
4. Query the `Session` table directly afterward and confirm 5 rows for that shop remain, each still containing a live `accessToken`.

### Citations

**File:** packages/apps/session-storage/shopify-app-session-storage-prisma/src/prisma.ts (L124-133)
```typescript
  public async findSessionsByShop(shop: string): Promise<Session[]> {
    await this.ensureReady();
    const sessions = await this.getSessionTable().findMany({
      where: {shop},
      take: 25,
      orderBy: [{expires: 'desc'}],
    });

    return sessions.map((session) => this.rowToSession(session));
  }
```

**File:** packages/apps/shopify-app-express/src/app-installations.ts (L33-41)
```typescript
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

**File:** packages/apps/shopify-app-express/src/webhooks/index.ts (L44-53)
```typescript
  // Add our custom app uninstalled webhook
  const appInstallations = new AppInstallations(config);

  api.webhooks.addHandlers({
    APP_UNINSTALLED: {
      deliveryMethod: DeliveryMethod.Http,
      callbackUrl: config.webhooks.path,
      callback: deleteAppInstallationHandler(appInstallations, config),
    },
  });
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
