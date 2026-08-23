### Title
DoS of the OAuth callback handler when webhook registration (external Shopify GraphQL call) fails - (File: `packages/apps/shopify-app-express/src/auth/auth-callback.ts`)

### Summary
`authCallback()` performs several sequential steps after exchanging the OAuth code, including an outbound GraphQL call to register webhooks. That call is not isolated with its own error handling, so any failure in the external `webhookSubscriptionCreate/Update/Delete` GraphQL request (network error, non-`THROTTLED` GraphQL error, Shopify API outage) causes the entire OAuth callback to fail with a 500 error, even though the merchant's session was already persisted and the flow could otherwise have completed successfully.

### Finding Description
`authCallback()` runs the following steps inside a single `try` block: exchange the OAuth code, store the session, then (for offline sessions) call `registerWebhooks()`, and finally invoke the `afterAuth` hook: [1](#0-0) 

`registerWebhooks()` forwards to `api.webhooks.register({session})`, which performs GraphQL calls per topic: [2](#0-1) 

Inside `shopify-api`'s `register()`, the code loops over configured webhook topics and calls `registerTopic()`/`runMutations()` for each one, and also fetches existing handlers via `getExistingHandlers()` (a paginated GraphQL query loop). None of these calls are wrapped in a `try/catch`; any thrown error (e.g., a GraphQL/network error for one topic) propagates up and aborts the whole registration for every other topic: [3](#0-2) [4](#0-3) 

Because `registerWebhooks()` is called from inside `authCallback()`'s outer `try`, that unhandled error is caught only by the generic `handleCallbackError()`, which — for anything other than `InvalidOAuthError`, `CookieNotFound`, or `BotActivityDetected` — returns a plain 500 response and never calls the `afterAuth` hook nor completes the redirect flow: [5](#0-4) 

This mirrors the SafEth report's pattern: a loop over multiple dependent external sub-operations (derivatives vs. webhook topics) where a single sub-operation's external-call failure aborts the entire higher-level operation (stake/unstake/rebalance vs. the OAuth callback), causing a full failure instead of a partial/degraded success.

### Impact Explanation
When webhook registration fails due to an external dependency issue (Shopify API hiccup, rate limiting outside the explicitly-handled `THROTTLED` case, or any GraphQL error on any single topic), the merchant's OAuth install/callback request fails with a 500 error. This happens even though `config.sessionStorage.storeSession()` already succeeded just before the webhook call, meaning the merchant is left with a stored session but a failed callback response — the `afterAuth` hook (which apps often use for post-install billing checks, additional setup, or the final redirect into the app) never runs, and the user sees an error page instead of completing installation. This is a denial-of-service condition on the auth callback handler that is entirely at the mercy of a downstream dependency (the Shopify Admin GraphQL API), consistent with the "DoS due to external call failure" bug class in the reference report.

### Likelihood Explanation
This is triggerable by any merchant during normal app installation — no privileged access or attacker action is required, only a transient failure/error from Shopify's GraphQL webhook API (network blip, one bad topic response, non-throttled GraphQL error) during the automatic `registerWebhooks` call that happens on every OAuth callback for offline sessions. Because there is no per-topic isolation (`try/catch`) and no isolation between webhook registration and the rest of the callback flow, the likelihood of the whole callback failing scales with the number of configured webhook topics and any transient issues on Shopify's side.

### Recommendation
Wrap the `registerWebhooks()` (and more granularly, each topic's mutation/pagination call inside `shopify-api`'s `register()`) in its own `try/catch` so that a failure for one topic does not abort registration for the rest, and so that a webhook-registration failure does not turn into a hard failure of the entire OAuth callback. On error, log per-topic failures (as is already done for `success: false` results) and continue with `afterAuth`/redirect logic, since sessions are already stored and re-registration can be retried on subsequent logins per the `register()` documentation ("You can safely call this method every time a shop logs in").

### Proof of Concept
1. Configure an app with `@shopify/shopify-app-express` and at least one webhook handler via `shopify.webhooks.addHandlers`.
2. Complete the OAuth flow so `authCallback()` runs; before/at the point `api.webhooks.register({session})` performs its GraphQL request, simulate a transient failure (e.g., mock the GraphQL client to return a non-`THROTTLED` error or throw a network error) — see the existing test pattern in [6](#0-5)  that mocks `shopify.api.webhooks.register`.
3. Observe that the response is a 500 (per `handleCallbackError`'s default branch) even though `config.sessionStorage.storeSession()` already succeeded, and that `afterAuth` is never invoked, confirming the callback handler is denied by the external dependency failure.

### Citations

**File:** packages/apps/shopify-app-express/src/auth/auth-callback.ts (L20-38)
```typescript
}: AuthCallbackParams): Promise<boolean> {
  try {
    const callbackResponse = await api.auth.callback({
      rawRequest: req,
      rawResponse: res,
      expiring: config.future?.expiringOfflineAccessTokens,
    });

    config.logger.debug('Callback is valid, storing session', {
      shop: callbackResponse.session.shop,
      isOnline: callbackResponse.session.isOnline,
    });

    await config.sessionStorage.storeSession(callbackResponse.session);

    // If this is an offline OAuth process, register webhooks
    if (!callbackResponse.session.isOnline) {
      await registerWebhooks(config, api, callbackResponse.session);
    }
```

**File:** packages/apps/shopify-app-express/src/auth/auth-callback.ts (L56-96)
```typescript
    await config.hooks?.afterAuth?.({session: callbackResponse.session});

    config.logger.debug('Completed OAuth callback', {
      shop: callbackResponse.session.shop,
      isOnline: callbackResponse.session.isOnline,
    });

    return true;
  } catch (error) {
    config.logger.error(`Failed to complete OAuth with error: ${error}`);

    await handleCallbackError(req, res, api, config, error);
  }

  return false;
}

async function handleCallbackError(
  req: Request,
  res: Response,
  api: Shopify,
  config: AppConfigInterface,
  error: Error,
) {
  switch (true) {
    case error instanceof InvalidOAuthError:
      res.status(400);
      res.send(error.message);
      break;
    case error instanceof CookieNotFound:
      await redirectToAuth({req, res, api, config});
      break;
    case error instanceof BotActivityDetected:
      res.status(410);
      res.send(error.message);
      break;
    default:
      res.status(500);
      res.send(error.message);
      break;
  }
```

**File:** packages/apps/shopify-app-express/src/helpers/register-webhooks.ts (L5-18)
```typescript
export async function registerWebhooks(
  config: AppConfigInterface,
  api: Shopify,
  session: Session,
): Promise<void> {
  config.logger.debug('Registering webhooks', {shop: session.shop});

  const responsesByTopic = await api.webhooks.register({session});

  for (const topic in responsesByTopic) {
    if (!Object.prototype.hasOwnProperty.call(responsesByTopic, topic)) {
      continue;
    }

```

**File:** packages/apps/shopify-api/lib/webhooks/register.ts (L64-90)
```typescript
    const existingHandlers = await getExistingHandlers(config, session);

    log.debug(
      `Existing topics: [${Object.keys(existingHandlers).join(', ')}]`,
      {shop: session.shop},
    );

    for (const topic in webhookRegistry) {
      if (!Object.prototype.hasOwnProperty.call(webhookRegistry, topic)) {
        continue;
      }

      if (privacyTopics.includes(topic)) {
        continue;
      }

      registerReturn[topic] = await registerTopic({
        config,
        session,
        topic,
        existingHandlers: existingHandlers[topic] || [],
        handlers: getHandlers(webhookRegistry)(topic),
      });

      // Remove this topic from the list of existing handlers so we have a list of leftovers
      delete existingHandlers[topic];
    }
```

**File:** packages/apps/shopify-api/lib/webhooks/register.ts (L123-144)
```typescript
  let hasNextPage: boolean;
  let endCursor: string | null = null;
  do {
    const query = buildCheckQuery(endCursor);

    const response = await client.request<WebhookCheckResponse>(query);

    response.data?.webhookSubscriptions?.edges.forEach(
      (edge: WebhookCheckResponseNode) => {
        const handler = buildHandlerFromNode(edge);

        if (!existingHandlers[edge.node.topic]) {
          existingHandlers[edge.node.topic] = [];
        }

        existingHandlers[edge.node.topic].push(handler);
      },
    );

    endCursor = response.data?.webhookSubscriptions?.pageInfo.endCursor!;
    hasNextPage = response.data?.webhookSubscriptions?.pageInfo.hasNextPage!;
  } while (hasNextPage);
```

**File:** packages/apps/shopify-app-express/src/auth/__tests__/auth.test.ts (L290-294)
```typescript
    jest
      .spyOn(shopify.api.auth, 'callback')
      .mockResolvedValueOnce({session, headers: undefined});
    jest.spyOn(shopify.api.webhooks, 'register').mockResolvedValueOnce({});
  });
```
