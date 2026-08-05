### Title
Per-IP QPS rate limiter can be bypassed via unlimited distinct source IPs, allowing unbounded aggregate throughput on costly public HTTP endpoints - ([File: framework/src/main/java/org/tron/core/services/ratelimiter/strategy/IPQpsStrategy.java])

### Summary
`IPQpsStrategy` enforces rate limits keyed solely by remote IP address, and each first-seen IP is granted a brand-new Guava `RateLimiter` via `loadLimiter`/`newRateLimiter`. An attacker who can present many distinct source addresses (e.g., a single host bound to a large IPv6 prefix) can create a fresh, fully-charged limiter bucket per request by rotating IPs, defeating the aggregate `qps=N` cap intended to bound cost against the endpoint.

### Finding Description
`IPQPSRateLimiterAdapter.tryAcquire` derives the key exclusively from `RuntimeData(req).getRemoteAddr()` [1](#0-0)  and delegates to `IPQpsStrategy.tryAcquire(ip)` [2](#0-1) . `loadLimiter` uses `Cache<String, RateLimiter>.get(ip, this::newRateLimiter)`, which atomically creates and inserts a brand-new `RateLimiter.create(qps)` for any key not yet present [3](#0-2) . Each newly created Guava `RateLimiter` starts with its bucket full (it permits an immediate burst), so a first `tryAcquire()` from any brand-new IP key always succeeds regardless of how many other IPs are simultaneously being throttled.

Because the cache key is the bare socket-level remote address obtained from `HttpServletRequest.getRemoteAddr()` [4](#0-3) , the limiter's security model implicitly assumes each IP is a scarce, hard-to-multiply resource. That assumption does not hold for attackers with IPv6 connectivity: a single host bound to a routed /64 (or smaller) prefix can trivially bind an essentially unbounded number of distinct source addresses and issue one request per address. Each such request is treated as "first contact" from a new IP, gets its own fresh limiter, and is granted immediate accept. Since there is no global/aggregate cap across all per-IP buckets (only `maximumSize(10000)` with a 600s expiry on the cache, which only bounds memory, not throughput), the attacker's total accepted request rate scales with the number of distinct source addresses used, not with the configured `qps=N`.

### Impact Explanation
Any HTTP servlet protected only by `IPQPSRateLimiterAdapter` (configured via `paramString qps=N`) can be driven at effectively unbounded aggregate request rate by an IPv6-capable attacker, even though the endpoint's cost model assumes per-source-IP throttling to `N` requests/sec. For "costly" endpoints (e.g., ones triggering expensive computation, DB/state scans, or CPU-heavy work per call), this enables a CPU/state-iteration denial-of-service by rotating source addresses, effectively nullifying the rate limiter's intended protection.

### Likelihood Explanation
Feasibility is high wherever the deployment environment or the attacker's network path exposes real (or NAT'd 1:1) IPv6 addresses to the servlet container, since IPv6 prefix delegation of /64 or larger to end hosts is common with cloud/ISP allocations, making generation of thousands of distinct source IPs a config change on the attacker's side. The severity is reduced (or nullified) in deployments sitting behind a reverse proxy/load balancer that only exposes a single peer address to the servlet layer, or where IPv6 access is filtered/NAT'd to a single IP; the repo does not implement any proxy-aware fallback (e.g., X-Forwarded-For based) that would make this worse, since `RuntimeData` reads only the raw socket peer address. This is a structural limitation of pure per-IP rate limiting rather than a logic bug in the cache/limiter code itself, but it directly matches the exploit path in the question and is fully supported by the code.

### Recommendation
Layer a global/aggregate rate limiter (e.g., `GlobalPreemptibleAdapter`/`GlobalRateLimiter`, already present in the same package) in front of or alongside `IPQPSRateLimiterAdapter` for costly endpoints so total throughput is bounded independent of source IP cardinality. Additionally consider: capping the total number of concurrently tracked IP buckets more aggressively relative to expected legitimate traffic, applying IP-prefix-based bucketing (e.g., collapse IPv6 into /64 or /56 blocks) so a single attacker-controlled prefix maps to one bucket, and/or combining per-IP limiting with per-endpoint/global ceilings.

### Proof of Concept
```java
// Pseudo test extending framework/src/test/java/org/tron/core/services/ratelimiter/adaptor/AdaptorTest.java style
@Test
public void testManyDistinctIpsBypassAggregateQps() throws Exception {
  IPQPSRateLimiterAdapter adapter = new IPQPSRateLimiterAdapter("qps=1");
  int distinctIpCount = 20000;
  int accepted = 0;
  for (int i = 0; i < distinctIpCount; i++) {
    String ip = "2001:db8:" + Integer.toHexString(i / 65536) + "::" + Integer.toHexString(i);
    RuntimeData data = mockRuntimeDataWithIp(ip); // wraps a mocked HttpServletRequest.getRemoteAddr()
    if (adapter.tryAcquire(data)) {
      accepted++;
    }
  }
  // With true qps=1 aggregate enforcement, accepted should be bounded (~1).
  // Expected (bug) result: accepted scales linearly with distinctIpCount (~20000),
  // demonstrating the per-IP-limiter bypass.
  Assert.assertTrue("Aggregate throughput not bounded by qps despite many distinct IPs",
      accepted > distinctIpCount / 2);
}
```
This test issues one request per unique simulated source IP against a `qps=1` configured adapter and asserts that nearly all requests are accepted (linear scaling with IP count) rather than being capped near the configured `qps`, confirming the bypass.

### Citations

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/adapter/IPQPSRateLimiterAdapter.java (L14-17)
```java
  @Override
  public boolean tryAcquire(RuntimeData data) {
    return strategy.tryAcquire(data.getRemoteAddr());
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/IPQpsStrategy.java (L24-27)
```java
  public boolean tryAcquire(String ip) {
    RateLimiter limiter = loadLimiter(ip);
    return limiter != null && limiter.tryAcquire();
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/IPQpsStrategy.java (L38-52)
```java
  private RateLimiter loadLimiter(String ip) {
    try {
      // cache.get is atomic: only one loader executes per key under concurrent requests,
      // preventing multiple RateLimiter instances from being created for the same IP.
      return ipLimiter.get(ip, this::newRateLimiter);
    } catch (Exception e) {
      logger.warn("Failed to load IP rate limiter for {}, denying request: {}",
          ip, e.getMessage());
      return null;
    }
  }

  private RateLimiter newRateLimiter() {
    return RateLimiter.create((Double) mapParams.get(STRATEGY_PARAM_IPQPS).value);
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/RuntimeData.java (L15-18)
```java
  public RuntimeData(Object o) {
    if (o instanceof HttpServletRequest) {
      address = ((HttpServletRequest) o).getRemoteAddr();
    } else if (o instanceof ServerCall) {
```
