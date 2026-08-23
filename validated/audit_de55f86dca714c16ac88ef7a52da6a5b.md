### Title
Missing Content-Security-Policy `frame-ancestors` header allows clickjacking of embedded Shopify apps when the `shop` query parameter is absent or invalid - (File: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/add-response-headers.ts`, `packages/apps/shopify-app-react-router/src/server/authenticate/helpers/add-response-headers.ts`)

### Summary
Both `shopify-app-remix` and `shopify-app-react-router` expose a public helper, `shopify.addDocumentResponseHeaders`, whose documented purpose is to attach the anti-clickjacking `Content-Security-Policy: frame-ancestors ...` header to every HTML response an app returns. The implementation, however, only sets this header for embedded apps when a valid `shop` value is present. When `shop` is missing or fails `sanitizeShop` validation, no CSP header (and no `X-Frame-Options` fallback) is set at all, leaving the response completely unprotected against being framed by an arbitrary origin — the same clickjacking bug class described in the wallet-iframe report.

### Finding Description
`addDocumentResponseHeaders` extracts `shop` from the request's query string and only calls `headers.set('Content-Security-Policy', ...)` inside the `if (isEmbeddedApp) { if (shop) { ... } }` branch. If `shop` is `null`/falsy (missing param, or rejected by `api.utils.sanitizeShop`), the function silently returns without ever calling `headers.set` for CSP: [1](#0-0) 

The identical pattern exists in the React Router package: [2](#0-1) 

This is a regression relative to the sibling `shopify-app-express` package, whose `addCSPHeader` unconditionally sets a header in *every* case (either the shop-scoped `frame-ancestors` value or the restrictive `frame-ancestors 'none'` fallback): [3](#0-2) 

Documentation instructs developers to wire this helper into `entry.server.tsx` so it runs on *every* HTML response in the app, making it the sole anti-clickjacking control for these frameworks: [4](#0-3) 

There is also direct evidence in the test suite that this "no header at all" state is a known, accepted outcome rather than a defensive `'none'` fallback — the regression test for an invalid `shop` explicitly tolerates a `null` CSP header instead of asserting `frame-ancestors 'none'`: [5](#0-4) 

The internal `renderAppBridge` call path (used for the bounce/exit-iframe pages during OAuth/session-token flows) is a concrete reachable case: it computes `shop` via `api.utils.sanitizeShop(...)` from the request URL, which can be `null` for a request with a missing or invalid `shop` param, and forwards that `null` straight into `addDocumentResponseHeaders`: [6](#0-5) 

Any anonymous request to the app's document/bounce endpoints omitting or corrupting the `shop` query param (e.g., `?host=...` only, or `?shop=not-a-shop`) will therefore receive a 200 HTML response with no `Content-Security-Policy` and no `X-Frame-Options` header, since neither package sets `X-Frame-Options` anywhere.

### Impact Explanation
Without any frame-restriction header, the affected document response can be embedded by a malicious third-party page in an `<iframe>`. Because the App Bridge bounce/exit-iframe page executes `window.open(...)` scripts and the general app document renders the merchant-authenticated UI, an attacker can overlay this framed content with deceptive/hidden UI elements to trick a logged-in merchant into clicking through actions inside the real app (e.g., approving redirects, triggering session-token refresh flows, or interacting with app UI) without realizing they are doing so — mirroring the wallet clickjacking scenario where hidden UI tricks a user into signing something unintended.

### Likelihood Explanation
The vulnerable code path is trivially reachable: it only requires visiting the app's own document URL with a missing or invalid `shop` query parameter, which an attacker fully controls when crafting the iframe `src` on their malicious page. No authentication bypass or privileged access is needed to trigger the missing-header condition itself; only a merchant's session/visit is needed to realize impact.

### Recommendation
Change `addDocumentResponseHeaders` in both `shopify-app-remix` and `shopify-app-react-router` to always set a `Content-Security-Policy` header, mirroring `shopify-app-express`'s `addCSPHeader`: when `isEmbeddedApp` is true but `shop` is missing/invalid, fall back to `frame-ancestors 'none'` (or a Shopify-admin-only default) instead of omitting the header entirely. Additionally, consider setting a legacy `X-Frame-Options: DENY` fallback for defense in depth.

### Proof of Concept
1. Deploy an app using `shopify-app-remix` or `shopify-app-react-router` with `shopify.addDocumentResponseHeaders` wired into `entry.server.tsx` as documented.
2. As an anonymous attacker, request the app's document/bounce path without a valid `shop` param, e.g. `GET https://app.example.com/auth/exit-iframe?exitIframe=%2Fmy-path` (no `shop`, or `shop=not-a-shop`).
3. Observe the response has no `Content-Security-Policy` header (confirmed by the test at `packages/apps/shopify-app-react-router/src/server/authenticate/admin/__tests__/doc-request-path.test.ts` lines 48-78, which explicitly allows `csp === null`).
4. Embed this URL in an `<iframe>` on an attacker-controlled page and overlay deceptive UI, achieving clickjacking against any merchant who is lured to the attacker's page while authenticated.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/add-response-headers.ts (L21-43)
```typescript
export function addDocumentResponseHeaders(
  headers: Headers,
  isEmbeddedApp: boolean,
  shop: string | null | undefined,
) {
  if (shop) {
    headers.set(
      'Link',
      '<https://cdn.shopify.com/shopifycloud/app-bridge.js>; rel="preload"; as="script";',
    );
  }

  if (isEmbeddedApp) {
    if (shop) {
      headers.set(
        'Content-Security-Policy',
        `frame-ancestors https://${shop} https://admin.shopify.com https://*.spin.dev https://admin.myshopify.io https://admin.shop.dev;`,
      );
    }
  } else {
    headers.set('Content-Security-Policy', `frame-ancestors 'none';`);
  }
}
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/helpers/add-response-headers.ts (L24-46)
```typescript
export function addDocumentResponseHeaders(
  headers: Headers,
  isEmbeddedApp: boolean,
  shop: string | null | undefined,
) {
  if (shop) {
    headers.set(
      'Link',
      `<${CDN_URL}>; rel="preconnect", <${APP_BRIDGE_URL}>; rel="preload"; as="script", <${POLARIS_URL}>; rel="preload"; as="script"`,
    );
  }

  if (isEmbeddedApp) {
    if (shop) {
      headers.set(
        'Content-Security-Policy',
        `frame-ancestors https://${shop} https://admin.shopify.com https://*.spin.dev https://admin.myshopify.io https://admin.shop.dev;`,
      );
    }
  } else {
    headers.set('Content-Security-Policy', `frame-ancestors 'none';`);
  }
}
```

**File:** packages/apps/shopify-app-express/src/middlewares/csp-headers.ts (L19-31)
```typescript
export function addCSPHeader(api: Shopify, req: Request, res: Response) {
  const shop = api.utils.sanitizeShop(req.query.shop as string);
  if (api.config.isEmbeddedApp && shop) {
    res.setHeader(
      'Content-Security-Policy',
      `frame-ancestors https://${encodeURIComponent(
        shop,
      )} https://admin.shopify.com https://*.spin.dev https://admin.myshopify.io https://admin.shop.dev;`,
    );
  } else {
    res.setHeader('Content-Security-Policy', `frame-ancestors 'none';`);
  }
}
```

**File:** packages/apps/shopify-app-remix/README.md (L141-160)
```markdown
Now that your app is ready to respond to requests, it will also need to add the required `Content-Security-Policy` header directives, as per [our documentation](https://shopify.dev/docs/apps/store/security/iframe-protection).
To do that, this package provides the `shopify.addDocumentResponseHeaders` method.

You should return these headers from any endpoint that renders HTML in your app.
Most likely you'll want to add this to every HTML response by updating the entry.server.tsx file:

```ts
// entry.server.tsx
import shopify from './shopify.server';

export default function handleRequest(
  request: Request,
  responseStatusCode: number,
  responseHeaders: Headers,
  remixContext: EntryContext,
) {
  shopify.addDocumentResponseHeaders(request, responseHeaders);

  /// ..etc
}
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/__tests__/doc-request-path.test.ts (L66-77)
```typescript
      // THEN
      expect(response.status).toBe(200);
      const csp = response.headers.get('Content-Security-Policy');
      // For an embedded app with no valid shop, no shop-specific frame-ancestors
      // entry should be emitted, and certainly not the attacker-controlled value.
      if (csp !== null) {
        expect(csp).not.toContain(evilShop);
      }
      const link = response.headers.get('Link');
      // The Link preconnect header is only set when shop is valid; with an
      // invalid shop it should be absent.
      expect(link).toBeNull();
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/helpers/render-app-bridge.ts (L33-40)
```typescript
  const isEmbeddedApp = config.distribution !== AppDistribution.ShopifyAdmin;
  // Sanitize the shop param before using it in response headers (e.g. CSP
  // frame-ancestors, Link preconnect). An attacker-controlled `?shop=evil.com`
  // must not end up in security-sensitive headers.
  const shop = api.utils.sanitizeShop(
    new URL(request.url).searchParams.get('shop')!,
  );
  addDocumentResponseHeaders(responseHeaders, isEmbeddedApp, shop);
```
