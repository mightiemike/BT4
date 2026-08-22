### Title
Unbounded default QPS (1000/s) for `GetPaginatedAssetIssueListServlet` provides no effective DoS protection against its expensive full-store sort - ([File: framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java])

### Summary
`GetPaginatedAssetIssueListServlet` has no entry in `rate.limiter.http`, so `RateLimiterServlet.addRateContainer` falls back to `DefaultBaseQqsAdapter` constructed with `QpsStrategy.DEFAULT_QPS_PARAM`. That default resolves to `qps=1000` (from `rate.limiter.global.api.qps` default in `RateLimiterConfig.GlobalConfig.ApiConfig`), a limit high enough that it does not meaningfully throttle an endpoint whose handler performs a full-store sort/paginate operation per request.

### Finding Description
When a servlet extending `RateLimiterServlet` has no matching entry in `Args.getInstance().getRateLimiterInitialization().getHttpMap()`, `addRateContainer` defaults `cName` to `DEFAULT_ADAPTER_NAME` (`DefaultBaseQqsAdapter`) and `params` to `QpsStrategy.DEFAULT_QPS_PARAM`: [1](#0-0) 

`QpsStrategy.DEFAULT_QPS_PARAM` is computed from `Args.getInstance().getRateLimiterGlobalApiQps()`: [2](#0-1) 

This value is backed by `RateLimiterConfig.GlobalConfig.ApiConfig.qps`, which defaults to `1000`: [3](#0-2) 

`GetPaginatedAssetIssueListServlet` has no override in the http rate limiter list (confirmed by search — no match for this servlet name in `reference.conf`/`config.conf` `rate.limiter.http` entries), so every unauthenticated request to this endpoint is throttled only by the generic 1000 QPS `DefaultBaseQqsAdapter`, and — since `GlobalRateLimiter` is a shared, cross-endpoint budget — by whatever headroom remains from other traffic. A limit of 1000 requests/sec per single expensive endpoint is not a meaningful throttle for an endpoint whose per-request cost is dominated by a full-store sort (as established by the referenced question 1 analysis of this servlet's query path), since even a small fraction of that allowed rate can keep sort/CPU-bound work saturating available threads continuously.

### Impact Explanation
This falls under **DoS via RPC-API**: an unprivileged client can send GET requests to `GetPaginatedAssetIssueListServlet` at a rate well within the default 1000 QPS ceiling and still drive sustained CPU/heap usage from the underlying full-store sort, without the rate limiter intervening to protect node availability. This is a resource-exhaustion/service-degradation risk for the FullNode's HTTP API rather than a fund-loss or state-corruption issue.

### Likelihood Explanation
- No special privileges are required — this is a plain HTTP GET on a default (non-overridden) node configuration; the "default" nature of the config is exactly what's exploited, not a non-default setting.
- Cost to the attacker is essentially free (no on-chain fee, no signed transaction needed — it's an HTTP read endpoint).
- Reproducibility is straightforward: any number of concurrent HTTP clients can issue requests at a rate under the 1000 QPS/endpoint ceiling and under the 50000 QPS global ceiling (`RateLimiterConfig.GlobalConfig.qps` default) and under the per-IP 10000 QPS ceiling, none of which are tuned to the actual cost of a full-store sort operation.
- The main uncertainty is the precise CPU cost per call of the sort in `GetPaginatedAssetIssueListServlet`'s handler — this analysis relies on the "full-store sort" cost characterization from the referenced question 1, which was not independently re-verified in this pass (index did not surface the full method body for the sort logic in this session). The rate-limiter defaulting behavior itself, however, is confirmed directly from code.

### Recommendation
Add an explicit `rate.limiter.http` entry for `GetPaginatedAssetIssueListServlet` (and its `OnSolidity`/`OnPBFT` variants) using a strategy with a QPS ceiling calibrated to the actual cost of a full asset-issue-store sort (e.g., low single-digit QPS via `QpsRateLimiterAdapter` or `IPQPSRateLimiterAdapter`), or bound the per-request cost directly (cap `offset`/`limit`, cache sorted results, or avoid re-sorting the full store on each call) so that the default 1000 QPS ceiling is not the only defense for CPU-expensive endpoints.

### Proof of Concept
```java
// Verifies that GetPaginatedAssetIssueListServlet has no override and thus
// resolves to DefaultBaseQqsAdapter with the generic 1000 QPS default,
// which is not endpoint-cost-aware.
@Test
public void testGetPaginatedAssetIssueListServletUsesUnthrottledDefault() {
  Args.setParam(new String[0], Constant.TEST_CONF);
  RateLimiterInitialization.HttpRateLimiterItem item = Args.getInstance()
      .getRateLimiterInitialization().getHttpMap()
      .get("GetPaginatedAssetIssueListServlet");
  assertNull(item); // no per-endpoint override exists

  // Confirms the fallback adapter/QPS used in production
  assertEquals(RateLimiterServlet.DEFAULT_ADAPTER_NAME, "DefaultBaseQqsAdapter");
  assertTrue(QpsStrategy.DEFAULT_QPS_PARAM.equals("qps=1000")); // default global.api.qps
}
```
A request-level PoC: run N concurrent HTTP clients issuing
`GET /walletsolidity/getpaginatedassetissuelist?offset=0&limit=<large>` at ~900 req/s (under the 1000 QPS default and under global/IP ceilings) against a default-configured node, and observe CPU saturation/latency degradation on the sort path before the rate limiter begins rejecting requests (`acquireResource == false`).

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L59-80)
```java
  @PostConstruct
  private void addRateContainer() {
    final String name = getClass().getSimpleName();
    RateLimiterInitialization.HttpRateLimiterItem item = Args.getInstance()
        .getRateLimiterInitialization().getHttpMap().get(name);

    String cName;
    String params;
    if (item == null) {
      cName = DEFAULT_ADAPTER_NAME;
      params = QpsStrategy.DEFAULT_QPS_PARAM;
    } else {
      cName = item.getStrategy();
      params = item.getParams();
    }

    try {
      container.add(KEY_PREFIX_HTTP, name, buildAdapter(cName, params, name));
    } catch (Exception e) {
      throw rateLimiterInitError(cName, params, name, e);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/strategy/QpsStrategy.java (L10-19)
```java
public class QpsStrategy extends Strategy {
  public static final String STRATEGY_PARAM_QPS = "qps";
  public static final int DEFAULT_QPS = Args.getInstance().getRateLimiterGlobalApiQps();
  public static final String DEFAULT_QPS_PARAM = "qps=" + DEFAULT_QPS;
  private RateLimiter rateLimiter;

  public QpsStrategy(String paramString) {
    super(paramString);
    rateLimiter = RateLimiter.create((Double) mapParams.get(STRATEGY_PARAM_QPS).value);
  }
```

**File:** common/src/main/java/org/tron/core/config/args/RateLimiterConfig.java (L26-44)
```java
  @Getter
  @Setter
  public static class GlobalConfig {
    private int qps = 50000;
    private IpConfig ip = new IpConfig();
    private ApiConfig api = new ApiConfig();

    @Getter
    @Setter
    public static class IpConfig {
      private int qps = 10000;
    }

    @Getter
    @Setter
    public static class ApiConfig {
      private int qps = 1000;
    }
  }
```
