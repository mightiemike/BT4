### Title
Unbounded blocking `GlobalRateLimiter.acquire()` allows single-IP attacker to exhaust gRPC server threads - (File: framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java)

### Summary
`GlobalRateLimiter.acquire()` calls Guava's `RateLimiter.acquire()` on the per-IP limiter with no timeout and no interrupt-handling, unlike the sibling `GlobalPreemptibleStrategy.acquire()` which correctly bounds its wait with `tryAcquire(DEFAULT_ACQUIRE_TIMEOUT, TimeUnit.SECONDS)`. Because Guava's `RateLimiter.acquire()` sleeps uninterruptibly for an amount of time proportional to the caller's position in the smoothed-burst queue, a single unprivileged IP that opens many concurrent gRPC calls can force a proportional number of server threads to block for an attacker-controlled duration.

### Finding Description
`RateLimiterInterceptor.interceptCall()` runs on the gRPC executor thread for every incoming call and, when `rate.limiter.apiNonBlocking=false`, invokes `GlobalRateLimiter.acquirePermit()` → `GlobalRateLimiter.acquire()`: [1](#0-0) 

The per-IP `RateLimiter r` is a Guava `SmoothBursty` limiter created once per attacker IP (`loadIpLimiter`) and reused across all concurrent calls from that IP: [2](#0-1) 

`r.acquire()` is Guava's blocking `RateLimiter.acquire()`, which internally sleeps *uninterruptibly* (`Uninterruptibles.sleepUninterruptibly`) for an amount of time computed from how far the caller's "reservation" is ahead of the limiter's available capacity — i.e., proportional to the number of concurrently outstanding acquisitions against that shared per-IP limiter. Unlike `GlobalPreemptibleStrategy.acquire()`, which bounds its semaphore wait to `DEFAULT_ACQUIRE_TIMEOUT = 2` seconds: [3](#0-2) 

`GlobalRateLimiter.acquire()` has no such bound and no `InterruptedException` handling at all — the method signature doesn't even declare or catch it because Guava's blocking `acquire()` doesn't throw it; it silently absorbs interrupts. This means the thread executing `interceptCall` (the gRPC call dispatch thread) is held hostage for however long the smoothing math dictates, with no cap.

Any unprivileged client can reach this: `interceptCall` fires for every gRPC method before any authentication/authorization check, gated only by the interceptor logic itself: [4](#0-3) 

By opening `C` concurrent gRPC connections/calls from one IP, the attacker forces `C` threads to each call `r.acquire()` on the same shared per-IP `RateLimiter`. Since Guava computes each caller's wait based on queue depth against the configured `IP_QPS`, thread-hold time scales with attacker-controlled `C`, and grows without bound as `C` increases — there is no maximum wait, no timeout, and no early-rejection path in the blocking branch.

### Impact Explanation
An attacker using a single IP and default blocking configuration (`rate.limiter.apiNonBlocking=false`) can tie up an arbitrary number of gRPC server threads for an arbitrary, attacker-controlled duration by simply issuing enough concurrent calls. This starves the gRPC executor of threads needed to service legitimate requests from other clients, degrading or halting the node's public API availability — a real, network-reachable denial-of-service against the RPC service using only unauthenticated, unprivileged input.

### Likelihood Explanation
- Preconditions: node running with `rate.limiter.apiNonBlocking=false` (the blocking mode, as evidenced by the fact `acquire()` is the fallback path in `acquirePermit()`), and the attacker able to open sufficient concurrent connections/streams from one IP — no special privileges, keys, or contract deployment required.
- Feasibility: gRPC allows many concurrent streams per connection/IP; opening hundreds to thousands of concurrent calls is trivial for a single client.
- Repeatability: fully repeatable and controllable — the attacker directly controls `C`, and thus directly controls thread-hold duration.

### Recommendation
Replace the unbounded `r.acquire()` / `rateLimiter.acquire()` calls in `GlobalRateLimiter.acquire()` with bounded waits, e.g. `RateLimiter.tryAcquire(timeout, TimeUnit)`, mirroring the pattern already used in `GlobalPreemptibleStrategy.acquire()`. On timeout, return `false` so `RateLimiterInterceptor` rejects the call with `RESOURCE_EXHAUSTED` instead of blocking the thread indefinitely. Additionally consider capping maximum concurrent in-flight acquisitions per IP independent of the smoothing delay.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/services/ratelimiter/GlobalRateLimiterThreadHoldTest.java
@Test
public void testConcurrentSingleIpBlocksThreadsProportionally() throws Exception {
  // Configure a low IP_QPS (e.g. via Args mock) to make effect measurable.
  RuntimeData sameIpData = mockRuntimeData("1.2.3.4");
  int C = 200; // attacker-controlled concurrency from ONE ip
  ExecutorService pool = Executors.newFixedThreadPool(C);
  CountDownLatch start = new CountDownLatch(1);
  AtomicLong maxHoldNanos = new AtomicLong();

  List<Future<Long>> futures = new ArrayList<>();
  for (int i = 0; i < C; i++) {
    futures.add(pool.submit(() -> {
      start.await();
      long t0 = System.nanoTime();
      GlobalRateLimiter.acquire(sameIpData); // blocks uninterruptibly
      return System.nanoTime() - t0;
    }));
  }
  start.countDown();
  long worstCaseHold = 0;
  for (Future<Long> f : futures) {
    worstCaseHold = Math.max(worstCaseHold, f.get(60, TimeUnit.SECONDS));
  }
  // Assert: with bounded/tryAcquire-based fix, hold time per thread should be capped
  // (e.g. <= DEFAULT_ACQUIRE_TIMEOUT). Before the fix, hold time grows with C / IP_QPS,
  // demonstrating unbounded thread occupancy from a single attacker IP.
  assertTrue("thread hold time should be bounded", worstCaseHold <= TimeUnit.SECONDS.toNanos(2));
}
```
Expected: on the current code, `worstCaseHold` scales with `C / IP_QPS` and is unbounded; after applying a `tryAcquire(timeout)`-based fix, hold time is capped, confirming the vulnerability and the fix.

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

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java (L53-63)
```java
  private static RateLimiter loadIpLimiter(String ip) {
    try {
      // cache.get is atomic: only one loader executes per key under concurrent requests,
      // preventing multiple RateLimiter instances from being created for the same IP.
      return cache.get(ip, () -> RateLimiter.create(IP_QPS));
    } catch (Exception e) {
      logger.warn("Failed to load IP rate limiter for {}, denying request: {}",
          ip, e.getMessage());
      return null;
    }
  }
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

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L104-114)
```java
    IRateLimiter rateLimiter = container
        .get(KEY_PREFIX_RPC, call.getMethodDescriptor().getFullMethodName());

    Listener<ReqT> listener = new ServerCall.Listener<ReqT>() {};

    RuntimeData runtimeData = new RuntimeData(call);
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```
