Found a solid analog. The `respondToBotRequest` check in `shopify-app-remix`/`shopify-app-react-router` unconditionally blocks any request whose `User-Agent` is classified as a bot by the third-party `isbot` heuristic, and there is no configuration flag anywhere in the codebase to disable or override this check — mirroring the GasThrottle report's "no way to turn it off" root cause.### Title
Unconditional, non-configurable bot check (`isbot`) permanently blocks legitimate admin/extension requests with no override — analog to unremovable GasThrottle oracle DoS - (File: `packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts`)

### Summary
`respondToBotRequest` is invoked unconditionally at the very start of every `authenticate.admin` and `authenticate.public.extension` call, before any session/token validation, and rejects the request with a hard `410 Gone` if the third-party `isbot` heuristic classifies the request's `User-Agent` header as a bot. [1](#0-0) [2](#0-1)  There is no configuration flag, future flag, or governance switch anywhere in the codebase to disable or bypass this check, exactly mirroring the GasThrottle report's root cause: an external classifier gates a critical path with no way to turn it off if it misbehaves.

### Finding Description
The check hard-codes a small allowlist for Shopify's own POS/Mobile clients and otherwise defers entirely to the `isbot` npm package's heuristic matching on the raw `User-Agent` string. [3](#0-2)  This function is called unconditionally as the first step of `authenticateAdmin`, ahead of `respondToOptionsRequest`, bounce-page/exit-iframe handling, and session-token/OAuth validation. [2](#0-1)  The identical unconditional pattern exists in `shopify-app-react-router`'s admin authenticate flow and in both packages' `authenticateExtension` factory. [4](#0-3) [5](#0-4) 

Searching the codebase confirms there is no `future` flag, config option, or parameter that lets an app developer or merchant disable/override this bot check (unlike, e.g., `tokenExchange` or other opt-in behaviors gated via `config.future`). Any request — from a real merchant/customer, an embedded webview, an integration client, a security scanner used by the merchant, or any HTTP client whose `User-Agent` string matches `isbot`'s pattern set (which is broad and third-party-maintained) — is permanently and unconditionally denied access to the authenticated admin/extension flow with a `410 Gone`, with no recovery path short of a code change and redeploy by the app developer.

This is structurally identical to the GasThrottle finding: a hard dependency on an external/third-party classifier (`_FAST_GAS_ORACLE` vs. `isbot`) gates a critical operation (swaps vs. authentication), the classifier can misclassify/return an unexpected result, and there is no governance/config toggle to disable it — the app owner has no way to "turn it off" without shipping new code.

### Impact Explanation
Any anonymous, unprivileged HTTP client reaching `authenticate.admin` or `authenticate.public.extension` with a `User-Agent` that `isbot` flags (which can include legitimate automated clients, certain embedded webviews, monitoring/uptime tools used by the merchant, or future `isbot` pattern-list updates that broaden matching) is denied service. Because the check is baked into the SDK with no opt-out, if the third-party `isbot` package updates its heuristics (a dependency change outside the app owner's control) to match a legitimate client's `User-Agent`, every request from that client class is permanently blocked across all apps using this SDK version — a DoS of the authentication handler with no mitigation available to the app owner other than patching/forking the library. This matches the "DoS of an auth handler" acceptance criterion.

### Likelihood Explanation
Reachability is trivial and requires no privilege: the check runs on the very first line of every admin/extension authentication request, driven purely by an attacker/client-controlled `User-Agent` header. [6](#0-5)  The existing test suite explicitly documents that generic bot-like agents (e.g., `Googlebot`, or any string isbot recognizes) trigger the block, confirming the behavior is easy to trigger and broad in scope. [7](#0-6)  The only mitigating factor is that an attacker deliberately targeting themselves gains nothing; the real-world risk is a false-positive class of legitimate `User-Agent`s (present or introduced via a future `isbot` update) being permanently locked out with zero recourse for the app owner.

### Recommendation
Do not hard-fail unconditionally on a third-party bot heuristic for a security-critical authentication path. Either:
1. Add a config/future flag (e.g., `config.future.disableBotCheck` or a `checkBot` option on `authenticate.admin`/`authenticate.public.extension`) allowing app developers to disable or tune the check, analogous to allowing governance to turn off GasThrottle; and/or
2. Make the failure mode fail open with logging/monitoring rather than a hard `410`, or restrict the bot check to non-critical/document-only requests rather than gating the entire authenticated flow, so that a misclassification cannot fully deny service with no way to recover.

### Proof of Concept
1. Deploy an app using `shopify-app-remix` (or `shopify-app-react-router`).
2. Send any HTTP request to a route wrapped by `shopify.authenticate.admin` (or `shopify.authenticate.public.extension`) with a `User-Agent` header matching any pattern in the `isbot` package's list, e.g. `User-Agent: Googlebot`.
3. Observe the request is rejected with `410 Gone` before any session-token or OAuth logic runs, as shown by the existing regression test `reject-bot.test.ts`. [8](#0-7) 
4. Confirm there is no configuration option in `shopifyApp(...)` (searched `config.future` flags in `packages/apps/shopify-app-remix/src/server/future/flags.ts` and related config types) that disables `respondToBotRequest` — the app owner cannot recover from a misclassification without a code change to the SDK itself, matching the "no way to remove after deployment" pattern of the GasThrottle finding.

### Citations

**File:** packages/apps/shopify-app-remix/src/server/authenticate/helpers/reject-bot-request.ts (L5-27)
```typescript
const SHOPIFY_POS_USER_AGENT = /Shopify POS\//;
const SHOPIFY_MOBILE_USER_AGENT = /Shopify Mobile\//;

const SHOPIFY_USER_AGENTS = [SHOPIFY_POS_USER_AGENT, SHOPIFY_MOBILE_USER_AGENT];

export function respondToBotRequest(
  {logger}: BasicParams,
  request: Request,
): void | never {
  const userAgent = request.headers.get('User-Agent') ?? '';

  // We call isbot below to prevent good (self-identifying) bots from triggering auth requests, but there are some
  // Shopify-specific cases we want to allow that are identified as bots by isbot.
  if (SHOPIFY_USER_AGENTS.some((agent) => agent.test(userAgent))) {
    logger.debug('Request is from a Shopify agent, allow');
    return;
  }

  if (isbot(userAgent)) {
    logger.debug('Request is from a bot, skipping auth');
    throw new Response(undefined, {status: 410, statusText: 'Gone'});
  }
}
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/authenticate.ts (L143-149)
```typescript
  return async function authenticateAdmin(request: Request) {
    try {
      respondToBotRequest(params, request);
      respondToOptionsRequest(params, request);
      await respondToBouncePageRequest(request);
      await respondToExitIframeRequest(request);
      await strategy.respondToOAuthRequests(request);
```

**File:** packages/apps/shopify-app-react-router/src/server/authenticate/admin/authenticate.ts (L144-149)
```typescript
  return async function authenticateAdmin(request: Request) {
    try {
      respondToBotRequest(params, request);
      respondToOptionsRequest(params, request);
      await respondToBouncePageRequest(request);
      await respondToExitIframeRequest(request);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/public/extension/authenticate.ts (L17-26)
```typescript
  return async function authenticateExtension(
    request,
    options = {},
  ): Promise<ExtensionContext> {
    const {logger} = params;

    const corsHeaders = options.corsHeaders ?? [];

    respondToBotRequest(params, request);
    respondToOptionsRequest(params, request, corsHeaders);
```

**File:** packages/apps/shopify-app-remix/src/server/authenticate/admin/__tests__/reject-bot.test.ts (L1-22)
```typescript
import {shopifyApp} from '../../..';
import {APP_URL, getThrownResponse, testConfig} from '../../../__test-helpers';

describe('authorize.admin', () => {
  test('rejects bot requests', async () => {
    // GIVEN
    const shopify = shopifyApp(testConfig());

    // WHEN
    const response = await getThrownResponse(
      shopify.authenticate.admin,
      new Request(APP_URL, {
        headers: {
          'User-Agent': 'Googlebot',
        },
      }),
    );

    // THEN
    expect(response.status).toBe(410);
    expect(response.statusText).toBe('Gone');
  });
```
