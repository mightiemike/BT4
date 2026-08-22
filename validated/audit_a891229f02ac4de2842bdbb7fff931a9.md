### Title
Global RPC rate-limiter quota is consumed by lite-fullnode-filtered methods before rejection, enabling shared-quota DoS - ([File: framework/src/main/java/org/tron/common/application/RpcService.java])

### Summary
`RateLimiterInterceptor` runs before `LiteFnQueryGrpcInterceptor` in the gRPC interceptor chain and unconditionally calls `GlobalRateLimiter.acquirePermit()` for every request, including requests to methods that `LiteFnQueryGrpcInterceptor` is guaranteed to reject with `UNAVAILABLE` on a lite fullnode. Because `GlobalRateLimiter` maintains a single shared `RateLimiter` instance across *all* IPs in addition to a per-IP bucket, an unprivileged attacker can flood a lite-node-filtered method (e.g. `protocol.Wallet/GetMarketOrderListByPair`) to drain that shared global quota, causing legitimate unrelated calls (e.g. `GetAccount`) from other clients to be rejected with `RESOURCE_EXHAUSTED`.

### Finding Description
The interceptor chain is registered in a fixed order: [1](#0-0) 

`RateLimiterInterceptor.interceptCall` always attempts to acquire a permit from `GlobalRateLimiter` regardless of whether the target method is one that a lite node will refuse to serve: [2](#0-1) 

Only after this permit acquisition succeeds does the request reach `LiteFnQueryGrpcInterceptor`, which then unconditionally closes the call with `UNAVAILABLE` for any of the `filterMethods` (including `GetMarketOrderListByPair`, `GetBlockById`, `ScanNoteByIvk`, etc.) when `chainBaseManager.isLiteNode()` is true and `openHistoryQueryWhenLiteFN` is false: [3](#0-2) 

Critically, `GlobalRateLimiter` does not only track a per-IP bucket; it also enforces a single process-wide `RateLimiter` shared by every client and every method: [4](#0-3) 

Since the gRPC method name (`call.getMethodDescriptor().getFullMethodName()`) is already known at the point `RateLimiterInterceptor.interceptCall` executes, the lite-node filter check could be performed first (or the global permit could be skipped for methods that will unconditionally be rejected), but it is not. As a result, every flood request to a filtered endpoint still consumes one token from the shared global `RateLimiter` and one token from the attacker's own per-IP `RateLimiter`, even though the request was destined to be rejected regardless.

### Impact Explanation
This is a DoS-via-RPC-API weakness: an unprivileged, anonymous gRPC client can send a high rate of requests to any of the `filterMethods` (which require no authentication, no signed transaction, and no fee) against a lite fullnode. Because the global `RateLimiter` token bucket is shared across all IPs, this can throttle or deny (`RESOURCE_EXHAUSTED`) legitimate, unrelated RPC calls (e.g., `GetAccount`) issued by *other* clients that share the node's public RPC endpoint — not merely the attacker's own IP quota. This matches the "DoS via RPC-API" bounty class, scoped to lite fullnodes serving public gRPC endpoints with default `openHistoryQueryWhenLiteFN=false`.

### Likelihood Explanation
- Preconditions: target node must run as a lite fullnode with `openHistoryQueryWhenLiteFN=false` (the standard/expected configuration for lite fullnodes, which is a common public-API deployment mode used to reduce disk usage).
- Cost to attacker: zero — no signed transaction, no account, no fee; just raw gRPC calls to a public endpoint.
- Feasibility: trivially repeatable with any gRPC client hammering a listed method such as `GetMarketOrderListByPair`; blocking vs non-blocking acquisition mode (`rate.limiter.apiNonBlocking`) only changes whether the attacker's own thread blocks in `RateLimiter.acquire()`, but the global token is consumed either way before rejection.
- The impact is bounded by the node operator's configured global QPS value, so severity scales inversely with that setting, but the design flaw is present regardless of the exact value.

### Recommendation
Reorder interceptor logic (or perform an early lightweight check) so that methods which `LiteFnQueryGrpcInterceptor` will unconditionally reject are filtered out before `RateLimiterInterceptor` consumes any `GlobalRateLimiter` permit — e.g., move `LiteFnQueryGrpcInterceptor` ahead of `RateLimiterInterceptor` in `RpcService.addInterceptor`, or have `RateLimiterInterceptor` consult the lite-node filter set (`LiteFnQueryGrpcInterceptor.getFilterMethods()`) before calling `GlobalRateLimiter.acquirePermit()`.

### Proof of Concept
```java
// Conceptual JUnit test extending existing RateLimiterInterceptorTest /
// LiteFnQueryGrpcInterceptorTest infrastructure.
@Test
public void testFilteredMethodStillConsumesGlobalQuota() {
  // Arrange: configure node as lite fullnode, openHistoryQueryWhenLiteFN=false,
  // set small GlobalRateLimiter QPS (e.g., 2) for determinism.
  when(chainBaseManager.isLiteNode()).thenReturn(true);
  CommonParameter.getInstance().openHistoryQueryWhenLiteFN = false;

  ServerCall<Object, Object> filteredCall = mockCall("protocol.Wallet/GetMarketOrderListByPair");
  ServerCall<Object, Object> legitCall = mockCall("protocol.Wallet/GetAccount");

  // Act: flood the filtered/rejected-by-design method past global QPS.
  for (int i = 0; i < 5; i++) {
    rateLimiterInterceptor.interceptCall(filteredCall, headers, next);
    liteFnQueryGrpcInterceptor.interceptCall(filteredCall, headers, next); // closes UNAVAILABLE
  }

  // Assert: GlobalRateLimiter permits were consumed by the rejected calls.
  // Then a legitimate call from the same/queued global bucket fails:
  Listener<Object> legitListener =
      rateLimiterInterceptor.interceptCall(legitCall, headers, next);
  verify(legitCall).close(argThat(status ->
      status.getCode() == Status.Code.RESOURCE_EXHAUSTED), any());
}
```
Expected result confirming the flaw: the `GetAccount` call is rejected with `RESOURCE_EXHAUSTED` purely because the global `RateLimiter` (shared, not per-method) was exhausted by flooding `GetMarketOrderListByPair`, a method that itself always ends in `UNAVAILABLE` from `LiteFnQueryGrpcInterceptor`.

### Citations

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

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L109-123)
```java
    RuntimeData runtimeData = new RuntimeData(call);
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);

    if (!acquireResource) {
      // Release the per-endpoint permit when global rejected, to avoid semaphore leak.
      if (rateLimiter instanceof IPreemptibleRateLimiter && perEndpointAcquired) {
        ((IPreemptibleRateLimiter) rateLimiter).release();
      }
      call.close(Status.fromCode(Code.RESOURCE_EXHAUSTED), new Metadata());
      return listener;
    }
```

**File:** framework/src/main/java/org/tron/core/services/filter/LiteFnQueryGrpcInterceptor.java (L79-91)
```java
  @Override
  public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(ServerCall<ReqT, RespT> call,
      Metadata headers, ServerCallHandler<ReqT, RespT> next) {
    if (chainBaseManager.isLiteNode()
            && !CommonParameter.getInstance().openHistoryQueryWhenLiteFN
            && filterMethods.contains(call.getMethodDescriptor().getFullMethodName())) {
      call.close(Status.UNAVAILABLE
              .withDescription("this API is closed because this node is a lite fullnode"), headers);
      return new ServerCall.Listener<ReqT>() {};
    } else {
      return next.startCall(call, headers);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java (L18-51)
```java
  private static Cache<String, RateLimiter> cache = CacheBuilder.newBuilder()
      .maximumSize(10000).expireAfterWrite(1, TimeUnit.HOURS).build();

  private static RateLimiter rateLimiter = RateLimiter.create(QPS);

  public static boolean tryAcquire(RuntimeData runtimeData) {
    String ip = runtimeData.getRemoteAddr();
    if (!Strings.isNullOrEmpty(ip)) {
      RateLimiter r = loadIpLimiter(ip);
      if (r == null || !r.tryAcquire()) {
        return false;
      }
    }
    return rateLimiter.tryAcquire();
  }

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
