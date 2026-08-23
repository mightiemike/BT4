### Title
Denial of Service via Unbounded Shop-Session-ID List Growth in `KVSessionStorage` - ([File: packages/apps/session-storage/shopify-app-session-storage-kv/src/kv.ts])

### Summary
`KVSessionStorage.addShopIds()` appends a session id to the per-shop id list on every `storeSession()` call without checking whether the id is already present. Because OAuth session ids for a given shop/user are deterministic (e.g. re-derived on every successful login/re-auth), a single, unprivileged merchant repeatedly completing the (public) OAuth begin/callback flow can grow this list without bound, mirroring the reported "unrestricted deposit / no existence dedup" bug class that causes storage bloat and eventual DoS of dependent lookups.

### Finding Description
`storeSession()` always calls `addShopIds(session.shop, [session.id])` after writing the session record: [1](#0-0) 

`addShopIds` simply concatenates the new id(s) onto the existing array with no existence check (unlike the analogous `RedisSessionStorage.addKeyToShopList`, which does check `idKeysArray.includes(idKey)` before pushing): [2](#0-1) 

Compare to the Redis implementation, which guards against duplicate insertion: [3](#0-2) 

This list (`shop:<shop>` key) is subsequently read entirely by `findSessionsByShop`, which loads every id in the list, including duplicates, via `loadSession`: [4](#0-3) 

`findSessionsByShop` is a session-storage/lookup primitive that is invoked from unauthenticated/low-privilege request paths, notably `AppInstallations.includes()`/`delete()`, which are used by the `ensureInstalledOnShop`/webhook uninstall middleware on effectively every incoming request and on every `APP_UNINSTALLED` webhook: [5](#0-4) 

Because `storeSession` is called on every successful OAuth callback (`oauth.ts` `callback()` builds and returns a `Session` that app code stores via the configured `SessionStorage`), and the OAuth begin/callback endpoints are public, unauthenticated-by-design entry points guarded only by HMAC/state/nonce validation (not by any rate limit or session cap): [6](#0-5) 

a single merchant/user can legitimately and repeatedly complete the OAuth flow (e.g., automating repeated visits to `/auth` then `/auth/callback` for their own shop) to keep appending the same (deterministic) session id to the shop's id list indefinitely, exactly analogous to the reported pattern of unrestricted appends with no minimum/dedup check leading to unbounded array growth.

### Impact Explanation
As the per-shop id array grows unbounded:
- The JSON-serialized value stored under the `shop:<shop>` KV key grows without limit, eventually approaching/exceeding the underlying KV value size limits, causing `storeSession`/`addShopIds` writes to fail.
- `findSessionsByShop` performs an ever-growing number of sequential `loadSession` KV reads (one per array entry, including duplicates), degrading and eventually breaking `AppInstallations.includes`/`delete`, which are used on the install-check middleware executed on nearly every request and on uninstall-webhook cleanup — a functional DoS of the auth/install-check handler for that shop.
- This is a storage-bloat DoS reachable purely from a single merchant's own repeated (legitimate) authentication actions, with no privileged access required, matching the accepted analog class "DoS of an auth handler" / "session-storage injection/bloat."

### Likelihood Explanation
Moderate-to-high for apps using `@shopify/shopify-app-session-storage-kv`: the OAuth begin/callback endpoints are intentionally public and only need a valid HMAC/state/nonce (which the shop's own owner/staff can always produce for their own shop), so a merchant can trivially script repeated re-authentication to trigger `storeSession` many times in quick succession, each call unconditionally appending to the unbounded list.

### Recommendation
In `addShopIds`, deduplicate before writing (mirroring the Redis adapter's `includes()` guard), e.g.:
```ts
private async addShopIds(shop: string, ids: string[]) {
  const key = this.getShopSessionIdsKey(shop);
  const shopIds = (await this.namespace.get<string[]>(key, 'json')) ?? [];
  const merged = Array.from(new Set([...shopIds, ...ids]));
  await this.namespace.put(key, JSON.stringify(merged));
}
```
Additionally consider capping the number of tracked ids per shop and/or rate-limiting repeated `storeSession` calls for the same session id to prevent storage bloat.

### Proof of Concept
1. Configure an app with `KVSessionStorage`.
2. As the shop owner (unprivileged, single-merchant actor), repeatedly hit `GET /auth?shop=test-shop.myshopify.com` followed by completing the resulting OAuth redirect/callback (each request producing a valid HMAC/state since it's the shop's own legitimate OAuth flow) N times in a loop.
3. Each successful callback calls `storeSession()` → `addShopIds()`, appending the (deterministic, per-user) session id to `shop:test-shop.myshopify.com` without dedup.
4. After sufficient iterations, `namespace.get('shop:test-shop.myshopify.com')` returns an array with N duplicate/near-duplicate entries; `findSessionsByShop` now performs N sequential KV reads, and the KV value approaches/exceeds size limits, causing subsequent `storeSession`/`findSessionsByShop` calls (used by the install-check middleware on every app request) to slow down or fail — DoS.

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

**File:** packages/apps/shopify-api/lib/auth/oauth/oauth.ts (L129-238)
```typescript
export function callback(config: ConfigInterface): OAuthCallback {
  return async function callback<T = AdapterHeaders>({
    expiring,
    ...adapterArgs
  }: CallbackParams): Promise<CallbackResponse<T>> {
    throwIfCustomStoreApp(
      config.isCustomStoreApp,
      'Cannot perform OAuth for private apps',
    );

    const log = logger(config);

    const request = await abstractConvertRequest(adapterArgs);

    const query = new URL(
      request.url,
      `${config.hostScheme}://${config.hostName}`,
    ).searchParams;
    const shop = query.get('shop')!;

    const response = {} as NormalizedResponse;
    let userAgent = request.headers['User-Agent'];
    if (Array.isArray(userAgent)) {
      userAgent = userAgent[0];
    }
    if (isbot(userAgent)) {
      logForBot({request, log, func: 'callback'});
      throw new ShopifyErrors.BotActivityDetected(
        'Invalid OAuth callback initiated by bot',
      );
    }

    log.info('Completing OAuth', {shop});

    const cookies = new Cookies(request, response, {
      keys: [config.apiSecretKey],
      secure: true,
    });

    const stateFromCookie = await cookies.getAndVerify(STATE_COOKIE_NAME);
    cookies.deleteCookie(STATE_COOKIE_NAME);
    if (!stateFromCookie) {
      log.error('Could not find OAuth cookie', {shop});

      throw new ShopifyErrors.CookieNotFound(
        `Cannot complete OAuth process. Could not find an OAuth cookie for shop url: ${shop}`,
      );
    }

    const authQuery: AuthQuery = Object.fromEntries(query.entries());
    if (!(await validQuery({config, query: authQuery, stateFromCookie}))) {
      log.error('Invalid OAuth callback', {shop, stateFromCookie});

      throw new ShopifyErrors.InvalidOAuthError('Invalid OAuth callback.');
    }

    log.debug('OAuth request is valid, requesting access token', {shop});

    const body = {
      client_id: config.apiKey,
      client_secret: config.apiSecretKey,
      code: query.get('code'),
      expiring: expiring ? '1' : '0',
    };

    const cleanShop = sanitizeShop(config)(query.get('shop')!, true)!;

    const postResponse = await fetchRequestFactory(config)(
      `https://${cleanShop}/admin/oauth/access_token`,
      {
        method: 'POST',
        body: JSON.stringify(body),
        headers: {
          'Content-Type': DataType.JSON,
          Accept: DataType.JSON,
        },
      },
    );

    if (!postResponse.ok) {
      throwFailedRequest(await postResponse.json(), false, postResponse);
    }

    const session: Session = createSession({
      accessTokenResponse: await postResponse.json<AccessTokenResponse>(),
      shop: cleanShop,
      state: stateFromCookie,
      config,
    });

    if (!config.isEmbeddedApp) {
      const cookiePath =
        typeof config.cookiePath === 'function'
          ? config.cookiePath(session)
          : (config.cookiePath ?? '/');
      await cookies.setAndSign(SESSION_COOKIE_NAME, session.id, {
        expires: session.expires,
        sameSite: 'lax',
        secure: true,
        path: cookiePath,
      });
    }

    return {
      headers: (await abstractConvertHeaders(
        cookies.response.headers!,
        adapterArgs,
      )) as T,
      session,
    };
```
