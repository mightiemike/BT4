### Title
Unbounded blocking `GlobalRateLimiter.acquire()` inside gRPC `interceptCall` exhausts the fixed RPC executor pool, causing full RPC-API stall (DoS) - ([File: framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java])

### Summary
When `rate.limiter.apiNonBlocking` is `false` (the default), `RateLimiterInterceptor.interceptCall` synchronously calls `GlobalRateLimiter.acquirePermit` → `GlobalRateLimiter.acquire`, which calls Guava `RateLimiter.acquire()` with no timeout on both the per-IP and global limiters. Because `interceptCall` runs on the node's fixed-size gRPC executor thread pool, an attacker who drives global/IP QPS into throttling can pin all pool threads in indefinite blocking waits, starving all other RPC calls node-wide.

### Finding Description
`RateLimiterInterceptor.interceptCall` is invoked for every incoming gRPC call on the executor configured for the server: [1](#0-0) 

`GlobalRateLimiter.acquirePermit` dispatches to the blocking `acquire()` path when `apiNonBlocking` is false: [2](#0-1) 

`acquire()` calls Guava's `RateLimiter.acquire()`, which blocks the calling thread indefinitely (no timeout) until a token becomes available, for both the per-IP limiter and the global limiter, before `interceptCall` returns and `next.startCall` is invoked.

This handler executes on the executor configured in `RpcService.initServerBuilder`, which is a **fixed-size** thread pool when `rpc.thread` (`getRpcThreadNum()`) is configured (a common production setting): [3](#0-2) 

Because the interceptor call — including the blocking `acquire()` — executes on this same bounded executor, once enough concurrent calls are queued/blocked waiting for a rate-limiter token, all executor threads become occupied in indefinite waits. Any further call (even for unrelated, unthrottled methods) cannot be dispatched because no free executor thread exists to run its `interceptCall`, and no explicit rate limiter timeout or thread-isolation mechanism exists to prevent this cross-contamination. This violates the intended "no unbounded blocking on network threads" invariant, since the interceptor performs blocking I/O-equivalent waits on RPC service threads instead of returning quickly (as `tryAcquire()` non-blocking mode does).

No existing check mitigates this: rate limiter permits/releases logic (lines 116-160 of `RateLimiterInterceptor.java`) only governs *whether* the call proceeds, not *how long* the interceptor itself may block the executor thread before making that decision.

### Impact Explanation
This is a node-wide RPC-API DoS: unrelated, otherwise-permitted gRPC calls (e.g., `GetNowBlock`, `BroadcastTransaction`) stall because no executor thread is available to process them, even though those specific calls would individually pass their own per-endpoint/IP/global limits. This matches the "DoS via RPC-API" bounty impact class — it degrades or halts node RPC service availability for all clients, not just the attacker.

### Likelihood Explanation
- Preconditions: `apiNonBlocking=false` (stated as the default), and an attacker who can open many concurrent gRPC connections/streams to a public node.
- Cost to attacker: none beyond opening TCP/HTTP2 connections and issuing requests — no fee, no signed transaction, no privileged role required.
- Feasibility: gRPC/Netty in java-tron allows many concurrent streams (bounded by `maxConcurrentCallsPerConnection` per connection, but an attacker can open many connections). Once concurrent in-flight calls exceed the fixed RPC executor thread count while the global/IP QPS is exhausted, additional executor threads block on `RateLimiter.acquire()` rather than failing fast.
- Repeatable: the condition persists as long as the attacker keeps enough concurrent connections open exceeding the executor pool size.

### Recommendation
Do not perform blocking waits on the gRPC/HTTP request-handling executor threads. Options:
- Replace the indefinite `RateLimiter.acquire()` with a bounded `tryAcquire(timeout)` even in "blocking" mode, and reject/close the call with `RESOURCE_EXHAUSTED` if the timeout elapses, instead of blocking forever.
- Alternatively, perform the blocking acquire on a separate, dedicated (and itself bounded/monitored) thread pool decoupled from the main RPC dispatch executor, so that rate-limiter contention cannot starve the dispatch of unrelated calls.
- Ensure the "blocking" mode is either removed or clearly documented as unsafe for production default, and consider making non-blocking (`tryAcquire`) the reference default.

### Proof of Concept
```java
// Conceptual JUnit-style PoC (framework/src/test/java/org/tron/core/services/ratelimiter/RateLimiterInterceptorTest.java style)
// 1. Configure a fixed executor with N threads (e.g., N=4) as RpcService does when rpc.thread>0.
// 2. Configure GlobalRateLimiter with a very low global QPS (e.g., 1 QPS) and apiNonBlocking=false.
// 3. Submit N+1 concurrent interceptCall invocations to the fixed executor, each going through
//    RateLimiterInterceptor.interceptCall -> GlobalRateLimiter.acquirePermit -> acquire() (blocking).
// 4. Assert: after N calls occupy all executor threads blocked in RateLimiter.acquire(),
//    the (N+1)th call (e.g., an unrelated cheap RPC like GetNowBlock) cannot be scheduled
//    and does not complete within its expected deadline, demonstrating executor-pool exhaustion.

@Test
public void testBlockingAcquireExhaustsExecutor() throws Exception {
  Args.getInstance().setRateLimiterApiNonBlocking(false); // default
  ExecutorService fixedPool = Executors.newFixedThreadPool(4);
  GlobalRateLimiter.class.getDeclaredField("rateLimiter"); // reflectively set to RateLimiter.create(0.1) (very slow)
  // Submit 5 concurrent tasks each calling interceptCall on fixedPool
  // Observe: 5th "unrelated" call does not complete before test timeout,
  // proving thread starvation via indefinite blocking acquire().
}
```
Expected assertion: the call scheduled beyond the executor's thread count fails to complete before a reasonable deadline (e.g., several seconds), confirming that blocking `RateLimiter.acquire()` on the dispatch executor causes cross-call starvation.

### Citations

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L109-114)
```java
    RuntimeData runtimeData = new RuntimeData(call);
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java (L34-51)
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

  public static boolean acquirePermit(RuntimeData runtimeData) {
    return Args.getInstance().isRateLimiterApiNonBlocking()
        ? tryAcquire(runtimeData)
        : acquire(runtimeData);
  }
```

**File:** framework/src/main/java/org/tron/common/application/RpcService.java (L94-101)
```java
  protected NettyServerBuilder initServerBuilder() {
    NettyServerBuilder serverBuilder = NettyServerBuilder.forPort(this.port);
    CommonParameter parameter = Args.getInstance();
    if (parameter.getRpcThreadNum() > 0) {
      this.executorService = ExecutorServiceManager.newFixedThreadPool(
          this.executorName, parameter.getRpcThreadNum());
      serverBuilder = serverBuilder.executor(this.executorService);
    }
```
