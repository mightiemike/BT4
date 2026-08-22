## Analysis

The external report concerns a client (price-feeder) failing to handle rate-limit responses gracefully, leading to cascading failures. The closest reachable analog in java-tron is on the *server* side: the node's own HTTP/gRPC rate limiter, when triggered by ordinary anonymous traffic, does not reject-fast by default — it blocks the request-handling thread indefinitely, which can exhaust the finite worker-thread pool and produce a denial of service for all API consumers.

### Title
Default blocking rate-limiter mode allows anonymous request floods to exhaust HTTP/gRPC worker threads (DoS) - (File: framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java)

### Summary
`rate.limiter.apiNonBlocking` defaults to `false` [1](#0-0) [2](#0-1) . In this mode, both the global rate limiter and per-endpoint limiters call Guava's blocking `RateLimiter.acquire()` instead of `tryAcquire()` [3](#0-2) , and the same dispatch logic is shared by every HTTP servlet and gRPC method via `IRateLimiter.acquirePermit` [4](#0-3) .

### Finding Description
`RateLimiterServlet.service()` and `RateLimiterInterceptor.interceptCall()` both call `rateLimiter.acquirePermit(runtimeData)` and `GlobalRateLimiter.acquirePermit(runtimeData)` before processing a request [5](#0-4) [6](#0-5) . When `apiNonBlocking` is `false` (the shipped default), `acquirePermit` dispatches to `acquire()`, which calls Guava `RateLimiter.acquire()` with no timeout [7](#0-6) . This call **blocks the calling thread** (the Jetty servlet thread or the gRPC executor thread) until a token becomes available, rather than rejecting or returning an error immediately.

Because HTTP servlet containers (Jetty) and gRPC servers operate with a bounded thread/executor pool, an anonymous client (or small set of clients) sending requests faster than the configured global QPS (`global.qps = 50000`, or the much lower per-endpoint default `global.api.qps = 1000`) causes worker threads to pile up waiting inside `RateLimiter.acquire()`. Once the thread pool is saturated with blocked threads, the node can no longer accept or service *any* other HTTP/gRPC requests — including legitimate ones from witnesses, exchanges, or wallets — producing a full denial-of-service condition. Unlike the price-feeder scenario (a client failing to notice a 429 and getting banned), here the *server itself* has no fast-reject/backoff-with-limit behavior in its default configuration; instead of degrading gracefully it silently accumulates blocked threads.

This is compounded by the fact that this is the **default** configuration shipped in both `reference.conf` and `config.conf` — operators must explicitly opt into `apiNonBlocking = true` to get fail-fast semantics [8](#0-7) .

### Impact Explanation
A sustained flood of requests against any enabled HTTP (`fullNodeHttpEnable`/`solidityEnable`/`PBFTEnable`) or gRPC endpoint from unauthenticated/anonymous clients can exhaust the node's servlet/gRPC worker-thread capacity, since excess requests block threads indefinitely instead of being rejected. This is a node-level denial-of-service reachable from any anonymous network client (no privileged actor or peer-level access required), directly matching the "DoS via RPC-API" acceptance criterion.

### Likelihood Explanation
Likelihood is high: the vulnerable configuration (`apiNonBlocking = false`) is the out-of-the-box default in both `reference.conf` and `config.conf` [9](#0-8) , so any publicly exposed FullNode/SolidityNode/PBFT HTTP or gRPC endpoint is affected without any special operator misconfiguration. No authentication, API key, or special network position is needed — an attacker only needs to send requests above the configured QPS threshold.

### Recommendation
**Short term:** Change the default `apiNonBlocking` to `true` (fail-fast/reject on overload) for publicly exposed endpoints, or bound the blocking wait with a timeout (Guava `RateLimiter.tryAcquire(timeout, unit)`) so worker threads cannot be held indefinitely. Ensure a clear error/log message (analogous to the report's recommendation to "log informative error messages upon reaching rate limits") is returned to the caller instead of silent blocking.

**Long term:** Stress-test the HTTP/gRPC layers under sustained above-threshold load to confirm the servlet/gRPC thread pools cannot be exhausted, and consider decoupling rate-limiting from the request-handling thread pool entirely (e.g., reject at the connection/accept layer).

### Proof of Concept
1. Deploy a FullNode with default `config.conf` (`apiNonBlocking = false`, `global.api.qps = 1000`).
2. From an anonymous client, issue concurrent HTTP requests to any enabled servlet (e.g., `/wallet/getnowblock`) at a rate exceeding the configured per-endpoint/global QPS, with enough concurrent connections to match or exceed the Jetty/gRPC thread pool size.
3. Observe that once the QPS is exceeded, each excess request causes its handling thread to block inside `GlobalRateLimiter.acquire()` / `RateLimiter.acquire()` [7](#0-6)  rather than returning immediately.
4. With enough concurrent connections, all available worker threads become blocked, and legitimate requests (including from witnesses/other services) start timing out or hanging — confirming the DoS condition.

### Citations

**File:** common/src/main/resources/reference.conf (L453-521)
```text
## Rate limiter config
rate.limiter = {
  # Each HTTP servlet and gRPC method can have its own rate-limit strategy.
  # Three API rate-limit strategies are available:
  #   GlobalPreemptibleAdapter – limits maximum concurrent requests globally.
  #                              paramString = "permit=N" (N = max concurrent calls)
  #   QpsRateLimiterAdapter    – limits average QPS across all callers.
  #                              paramString = "qps=N" (N may be a decimal)
  #   IPQPSRateLimiterAdapter  – limits average QPS per source IP.
  #                              paramString = "qps=N" (N may be a decimal)
  # If no strategy is configured for an endpoint, QpsRateLimiterAdapter with
  # qps=1000 is applied automatically.

  # Per-servlet HTTP rate limits. component is the servlet class simple name.
  http = [
    # {
    #   component = "GetNowBlockServlet",
    #   strategy = "GlobalPreemptibleAdapter",
    #   paramString = "permit=1"
    # },
    # {
    #   component = "GetAccountServlet",
    #   strategy = "IPQPSRateLimiterAdapter",
    #   paramString = "qps=1"
    # },
    # {
    #   component = "ListWitnessesServlet",
    #   strategy = "QpsRateLimiterAdapter",
    #   paramString = "qps=1"
    # }
  ]

  # Per-method gRPC rate limits. component is "package.ServiceName/MethodName".
  rpc = [
    # {
    #   component = "protocol.Wallet/GetBlockByLatestNum2",
    #   strategy = "GlobalPreemptibleAdapter",
    #   paramString = "permit=1"
    # },
    # {
    #   component = "protocol.Wallet/GetAccount",
    #   strategy = "IPQPSRateLimiterAdapter",
    #   paramString = "qps=1"
    # },
    # {
    #   component = "protocol.Wallet/ListWitnesses",
    #   strategy = "QpsRateLimiterAdapter",
    #   paramString = "qps=1"
    # }
  ]

  # P2P message rate limits.
  p2p = {
    # QPS ceiling for individual P2P message types received from peers.
    # Values are doubles; fractional QPS is allowed (e.g. 0.5 = one per 2 s).
    syncBlockChain = 3.0  # SyncBlockChain handshake messages
    fetchInvData = 3.0    # FetchInvData (block/tx fetch) messages
    disconnect = 1.0      # Disconnect messages
  }

  # Node-wide QPS ceiling across all HTTP + gRPC requests combined.
  global.qps = 50000
  # Per-source-IP QPS ceiling across all HTTP + gRPC requests from that IP.
  global.ip.qps = 10000
  # Default per-endpoint QPS limit applied to any endpoint with no explicit strategy.
  global.api.qps = 1000
  # true = reject over-limit requests immediately; false = queue and block the caller.
  apiNonBlocking = false
}
```

**File:** framework/src/main/resources/config.conf (L189-195)
```text
  # global qps, default 50000
  global.qps = 50000
  # IP-based global qps, default 10000
  global.ip.qps = 10000
  # If true, API rate limiters reject immediately on overload (non-blocking). Default: false
  apiNonBlocking = false
}
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

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IRateLimiter.java (L12-16)
```java
  default boolean acquirePermit(RuntimeData data) {
    return Args.getInstance().isRateLimiterApiNonBlocking()
        ? tryAcquire(data)
        : acquire(data);
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L110-114)
```java
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RateLimiterInterceptor.java (L110-114)
```java
    // Check per-endpoint first to avoid consuming global IP/QPS quota for requests
    // that would be rejected by the per-endpoint limiter anyway. acquirePermit()
    // chooses blocking or non-blocking semantics based on rate.limiter.apiNonBlocking.
    boolean perEndpointAcquired = rateLimiter == null || rateLimiter.acquirePermit(runtimeData);
    boolean acquireResource = perEndpointAcquired && GlobalRateLimiter.acquirePermit(runtimeData);
```
