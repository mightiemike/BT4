### Title
Blocking rate-limiter `acquire()` runs inside the gRPC executor thread and allows a single unprivileged client to exhaust the shared RPC thread pool - ([File: framework/src/main/java/org/tron/core/services/ratelimiter/adapter/QpsRateLimiterAdapter.java])

### Summary
`QpsRateLimiterAdapter.acquire()` (and the sibling `GlobalRateLimiter.acquire()` / `QpsStrategy.acquire()`) call Guava's blocking `RateLimiter.acquire()`, and this call happens synchronously inside `RateLimiterInterceptor.interceptCall()`, which runs on the same bounded executor thread pool (`rpc-full-executor`, sized by `rpc.thread.num`) that services *every* gRPC method on the node. Because `rate.limiter.apiNonBlocking = false` is the shipped default in `reference.conf`, any client can open more concurrent calls than the pool size against a low-QPS-limited method and permanently occupy every worker thread, starving unrelated RPCs such as `GetAccount` and `BroadcastTransaction`.

### Finding Description
`RateLimiterInterceptor.interceptCall()` invokes `rateLimiter.acquirePermit(runtimeData)` directly on the calling thread before `next.startCall()` is ever reached: [1](#0-0) 

`acquirePermit()` dispatches to `acquire()` instead of `tryAcquire()` whenever `rate.limiter.apiNonBlocking` is `false`: [2](#0-1) 

`QpsRateLimiterAdapter.acquire()` forwards to `QpsStrategy.acquire()`, which calls the blocking Guava `RateLimiter.acquire()` — a call that sleeps the current thread until enough tokens have accumulated: [3](#0-2) [4](#0-3) 

Crucially, `apiNonBlocking = false` is the actual shipped default, not an unusual opt-in hardening setting: [5](#0-4) 

The interceptor (and thus this blocking call) executes on the gRPC server's application executor, which `RpcService.initServerBuilder()` wires as a bounded `newFixedThreadPool(rpc.thread.num)` shared across the entire `Server` (i.e., across all methods of `DatabaseApi`, `WalletApi`, `WalletSolidityApi`, etc., not per-method): [6](#0-5) [7](#0-6) 

Because every gRPC method gets a default `QpsRateLimiterAdapter` with `DEFAULT_QPS` if no explicit override is configured: [8](#0-7) 

any client can pick a low-QPS-configured method (or wait for many concurrent requests to compete for the default global API QPS bucket) and open more concurrent calls than `rpc.thread.num`. Each excess call's task blocks inside `Strategy.acquire()`/`GlobalRateLimiter.acquire()` on the interceptor thread waiting for a token; since the token bucket refill rate is fixed and shared, and N threads are all blocked simultaneously waiting on the same limiter, the last-served thread waits roughly `N/qps` seconds — a duration the attacker fully controls by choosing `N`. While these threads are parked in `RateLimiter.acquire()`, they cannot service any other queued gRPC call, including totally unrelated methods like `GetAccount`/`BroadcastTransaction`, because they share the same fixed pool.

None of the existing checks intercept this: `RpcApiAccessInterceptor`, `LiteFnQueryGrpcInterceptor`, transaction validation, and permission checks are irrelevant here because the block happens before any transaction/business logic runs, purely inside rate-limiter bookkeeping.

### Impact Explanation
This is a DoS via RPC-API: an unprivileged, unauthenticated gRPC client can render the node's gRPC service (Full/Solidity/PBFT — whichever port is targeted) unresponsive to all RPCs for as long as the attacker keeps enough concurrent connections open, because the fixed executor pool backing the entire `Server` gets fully occupied by threads sleeping in blocking rate-limiter `acquire()` calls.

### Likelihood Explanation
Preconditions are simply the shipped default configuration: `rate.limiter.apiNonBlocking = false` (default in `reference.conf`) and a bounded `rpc.thread.num` executor (a standard, commonly-set performance/resource-control tunable, not a hardening opt-in). The attack requires no fees, no signed transactions, no special account — just opening more concurrent gRPC connections/streams than the configured thread-pool size against any rate-limited method (the low-QPS default limiter or an explicit low-`qps` override). This is trivially repeatable and cheap for an anonymous client.

### Recommendation
Do not perform blocking rate-limiting inside the gRPC interceptor thread that also executes application RPC handlers. Either:
1. Default `apiNonBlocking` to `true` (fail-fast) for RPC/HTTP interceptors, or
2. Move blocking `acquire()` waits off of the shared bounded executor (e.g., use a scheduled/dedicated executor with a timeout, or convert the interceptor to schedule the actual call asynchronously after the rate limiter permit is obtained, rather than blocking the pool thread), or
3. Bound the maximum wait time of `acquire()` calls (Guava's `RateLimiter.tryAcquire(long timeout, TimeUnit unit)`) and reject with `RESOURCE_EXHAUSTED` on timeout instead of blocking indefinitely.

### Proof of Concept
```java
// Integration-test sketch (JUnit) analogous to RpcApiServicesTest:
// 1. Configure a small fixed rpc.thread.num, e.g. 4, and rate.limiter.apiNonBlocking = false (default).
// 2. Configure a low-qps rpc rate limiter entry, e.g.
//    rate.limiter.rpc = [{ component = "protocol.Wallet/GetAccount",
//                           strategy = "QpsRateLimiterAdapter", paramString = "qps=1" }]
// 3. Open N (> rpc.thread.num, e.g. 20) concurrent async stubs and call GetAccount
//    simultaneously from a single client process (no valid signature required to reach
//    the interceptor since the block happens before business logic).
// 4. Concurrently issue a call to an unrelated, unthrottled or cheap method (e.g. GetNowBlock)
//    and assert it does NOT complete within a normal timeout while the flood is in-flight.

ExecutorService clientPool = Executors.newFixedThreadPool(20);
CountDownLatch start = new CountDownLatch(1);
for (int i = 0; i < 20; i++) {
  clientPool.submit(() -> {
    start.await();
    blockingStub.getAccount(Account.newBuilder().setAddress(anyAddress).build());
  });
}
start.countDown();

// Meanwhile:
long t0 = System.currentTimeMillis();
databaseBlockingStub.getNowBlock(EmptyMessage.newBuilder().build());
long elapsed = System.currentTimeMillis() - t0;

// Expected (vulnerable) result: elapsed >> normal RTT, or times out,
// because all rpc-full-executor threads are parked in RateLimiter.acquire().
Assert.assertTrue("node should stay responsive to unrelated RPCs", elapsed < 1000);
```

### Citations

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L36-43)
```java
  public void init(Server server) {
    // add default
    for (ServerServiceDefinition service : server.getServices()) {
      for (ServerMethodDefinition<?, ?> method : service.getMethods()) {
        container.add(KEY_PREFIX_RPC, method.getMethodDescriptor().getFullMethodName(),
            new DefaultBaseQqsAdapter(QpsStrategy.DEFAULT_QPS_PARAM));
      }
    }
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

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IRateLimiter.java (L12-16)
```java
  default boolean acquirePermit(RuntimeData data) {
    return Args.getInstance().isRateLimiterApiNonBlocking()
        ? tryAcquire(data)
        : acquire(data);
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/adapter/QpsRateLimiterAdapter.java (L19-22)
```java
  @Override
  public boolean acquire(RuntimeData data) {
    return strategy.acquire();
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/QpsStrategy.java (L33-36)
```java
  public boolean acquire() {
    rateLimiter.acquire();
    return true;
  }
```

**File:** common/src/main/resources/reference.conf (L519-520)
```text
  # true = reject over-limit requests immediately; false = queue and block the caller.
  apiNonBlocking = false
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

**File:** framework/src/main/java/org/tron/common/application/RpcService.java (L123-137)
```java
  protected void addInterceptor(NettyServerBuilder serverBuilder) {
    // add a ratelimiter interceptor
    serverBuilder.intercept(this.rateLimiterInterceptor);

    // add api access interceptor
    serverBuilder.intercept(this.apiAccessInterceptor);

    // add lite fullnode query interceptor
    serverBuilder.intercept(this.liteFnQueryGrpcInterceptor);

    // add prometheus interceptor
    if (Args.getInstance().isMetricsPrometheusEnable()) {
      serverBuilder.intercept(prometheusInterceptor);
    }
  }
```
