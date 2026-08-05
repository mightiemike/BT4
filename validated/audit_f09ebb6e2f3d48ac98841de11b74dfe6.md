### Title
Blocking global/per-IP RateLimiter.acquire() with no timeout enables worker-thread pool exhaustion when apiNonBlocking=false - ([File: GlobalRateLimiter.java])

### Summary
When `rate.limiter.apiNonBlocking=false`, `RateLimiterServlet.service()` and `RateLimiterInterceptor.interceptCall()` call `GlobalRateLimiter.acquirePermit()`, which dispatches to `GlobalRateLimiter.acquire()`. That method calls Guava's `RateLimiter.acquire()` (both per-IP and global instances) with no timeout, parking the calling Jetty/gRPC worker thread for as long as it takes for a token to become available. An attacker who opens many concurrent connections against any low-QPS endpoint can hold a proportional number of server worker threads in a blocked state, exhausting the thread pool and denying service to unrelated clients.

### Finding Description
`GlobalRateLimiter.acquire(RuntimeData)` calls `r.acquire()` on the per-IP `com.google.common.util.concurrent.RateLimiter` and then `rateLimiter.acquire()` on the global limiter [1](#0-0) . Guava's `RateLimiter.acquire()` has no timeout parameter — it blocks the calling thread until a permit is available, sleeping proportional to how far the request rate exceeds the configured QPS.

This method is invoked from both HTTP and gRPC entrypoints via `acquirePermit()`, which selects blocking vs non-blocking mode based on `Args.getInstance().isRateLimiterApiNonBlocking()` [2](#0-1) :
- `RateLimiterServlet.service()` calls `GlobalRateLimiter.acquirePermit(runtimeData)` directly on the Jetty servlet thread inside `service()` [3](#0-2) .
- `RateLimiterInterceptor.interceptCall()` calls the same `acquirePermit` on the gRPC executor thread before `next.startCall()` [4](#0-3) .

Notably, the per-endpoint `GlobalPreemptibleStrategy.acquire()` (a different rate-limiting mechanism used for per-endpoint limits) bounds its blocking to a 2-second timeout via `sp.tryAcquire(DEFAULT_ACQUIRE_TIMEOUT, TimeUnit.SECONDS)` [5](#0-4) , showing that the codebase's authors were aware of the need to bound blocking waits — but `GlobalRateLimiter.acquire()` (used for the global/IP-level limiter layered on top of every endpoint) has no such bound. Because per-endpoint check happens first and the global limiter is checked second (`perEndpointAcquired && GlobalRateLimiter.acquirePermit(...)`), any request that passes the (possibly generous) per-endpoint limiter but exceeds the global/IP QPS budget will block the calling thread indefinitely (bounded only by the eventual token replenishment rate, which under sustained excess traffic can be arbitrarily long).

No auth, accounting, or timeout guard exists on this path to cap the block duration — an unprivileged remote client can simply open many concurrent HTTP/gRPC connections against any `RateLimiterServlet`-backed endpoint (or any gRPC method routed through `RateLimiterInterceptor`) and exceed the configured global/IP QPS to tie up worker threads.

### Impact Explanation
Jetty and gRPC use bounded worker/executor thread pools to service concurrent requests. If an attacker opens more concurrent connections than the pool size and each blocks in `rateLimiter.acquire()`/`r.acquire()`, the pool becomes saturated with blocked threads, and legitimate requests to any endpoint (not just the targeted one) queue or time out — a denial of service that the rate limiter itself causes, rather than mitigates. This is a public-API-reachable amplification of DoS impact when this configuration mode is enabled.

### Likelihood Explanation
Requires `rate.limiter.apiNonBlocking=false` (a supported, non-default-guaranteed configuration flag exposed in `config.conf`/`reference.conf`) [6](#0-5) . Given this precondition, exploitation only requires an unprivileged attacker to send ordinary HTTP/gRPC requests at a rate exceeding the configured global/IP QPS with sufficient concurrency — no special privileges, valid transactions, or protocol tricks are needed, making it straightforward and fully repeatable.

### Recommendation
Bound the blocking wait in `GlobalRateLimiter.acquire()` using Guava's timed `RateLimiter.tryAcquire(timeout, unit)` (for both the per-IP and global limiters) instead of the unbounded `acquire()`, mirroring the bounded pattern already used in `GlobalPreemptibleStrategy.acquire()`. On timeout, reject the request (return `false`) rather than parking the thread indefinitely.

### Proof of Concept
Integration test plan:
1. Configure `rate.limiter.apiNonBlocking=false` and set `rate.limiter.globalQps`/`rate.limiter.globalIpQps` to a low value (e.g., 1 QPS) in test config.
2. Deploy a `RateLimiterServlet` subclass backed by a small, known Jetty thread pool size `N` (e.g., configure Jetty QueuedThreadPool max threads = 10 for the test server).
3. Spawn `N + 5` concurrent client threads all issuing requests to the same low-QPS endpoint from the same or different IPs, exceeding both per-IP and global QPS.
4. Assert that the Jetty thread pool's available/idle thread count drops to 0 (or near 0) while requests are pending, using `QueuedThreadPool.getIdleThreads()`.
5. Concurrently issue a request to an unrelated, otherwise-unthrottled endpoint and assert it times out or is queued beyond an acceptable SLA (e.g., no response within 2 seconds), demonstrating cross-endpoint denial of service caused by thread starvation.
6. Repeat for the gRPC path using `RateLimiterInterceptor` with a bounded gRPC executor (e.g., `Executors.newFixedThreadPool(N)`), asserting the executor's active thread count saturates and unrelated RPCs are delayed/rejected.

### Citations

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java (L34-45)
```java
  public static boolean acquire(RuntimeData runtimeData) {
    String ip = runtimeData.getRemoteAddr();
    if (!Strings.isNullOrEmpty(ip)) {
      RateLimiter r = loadIpLimiter(ip);
      if (r == null) {
        return false;
      }
      r.acquire();
    }
    rateLimiter.acquire();
    return true;
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java (L47-51)
```java
  public static boolean acquirePermit(RuntimeData runtimeData) {
    return Args.getInstance().isRateLimiterApiNonBlocking()
        ? tryAcquire(runtimeData)
        : acquire(runtimeData);
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L103-114)
```java
  @Override
  protected void service(HttpServletRequest req, HttpServletResponse resp)
      throws ServletException, IOException {

    RuntimeData runtimeData = new RuntimeData(req);
    IRateLimiter rateLimiter = container.get(KEY_PREFIX_HTTP, getClass().getSimpleName());

    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L109-114)
```java
    RuntimeData runtimeData = new RuntimeData(call);
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/GlobalPreemptibleStrategy.java (L37-47)
```java
  public boolean acquire() {
    try {
      return sp.tryAcquire(DEFAULT_ACQUIRE_TIMEOUT, TimeUnit.SECONDS);
    } catch (InterruptedException e) {
      // Restore the interrupt flag and reject — caller must not release a permit
      // it never acquired.
      logger.error("acquire permit with error: {}", e.getMessage());
      Thread.currentThread().interrupt();
      return false;
    }
  }
```

**File:** common/src/main/java/org/tron/core/config/args/RateLimiterConfig.java (L1-1)
```java
package org.tron.core.config.args;
```
