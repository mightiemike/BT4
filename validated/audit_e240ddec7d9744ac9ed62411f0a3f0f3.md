### Title
Unbounded per-query cycle loop in `MortgageService.getOldReward` enables wall-clock-scaled RPC DoS - (File: chainbase/src/main/java/org/tron/core/service/MortgageService.java)

### Summary
`MortgageService.queryReward()` and `withdrawReward()` reach `computeReward(beginCycle, endCycle, accountCapsule)`, which — when `allowOldRewardOpt()` is false — falls into `getOldReward()` and iterates `for (long cycle = begin; cycle < end; cycle++) reward += computeReward(cycle, votes);` [1](#0-0)  This loop's cost is linear in `end - begin` (number of maintenance cycles since the account's last withdrawal), and it is fully reachable by an unauthenticated `GET`/`POST` to `GetRewardServlet`, for any address. [2](#0-1) 

### Finding Description
`GetRewardServlet.doGet` takes an arbitrary `address` query parameter (no signature, no auth, no ownership check) and calls `manager.getMortgageService().queryReward(address)` [3](#0-2) . `queryReward` computes `beginCycle`/`endCycle` from `DelegationStore` and, if `beginCycle < endCycle` (`endCycle` set to `currentCycle`), calls `computeReward(beginCycle, endCycle, accountCapsule)` [4](#0-3) . That method, when `beginCycle < newAlgorithmCycle` (i.e., new reward algorithm not yet effective for this account), calls `getOldReward(beginCycle, oldEndCycle, srAddresses)` [5](#0-4) , which — with `allowOldRewardOpt()` false — runs the raw per-cycle loop doing a `delegationStore.getReward`/`getWitnessVote` DB read per vote per cycle [6](#0-5) .

Crucially, `queryReward` never persists or checkpoints the computed reward — it is a pure read call that recomputes the full `[beginCycle, currentCycle)` range from scratch on every invocation as long as the caller does not withdraw (only `withdrawReward`, invoked from `WithdrawBalanceActuator`, advances `beginCycle`). Since `GetRewardServlet` is a plain HTTP GET with no signature or per-account cost, an attacker can:
1. Vote once early (cheap, single signed freeze+vote tx paid for normally), which fixes `beginCycle` far in the past.
2. Never call withdraw, letting `currentCycle` (driven purely by node wall-clock maintenance, ~6h/cycle) grow the `end - begin` gap indefinitely.
3. Repeatedly call `GetRewardServlet`/`GetRewardInfo` for that address, each call recomputing the full linear-cost loop.

The only mitigating control present is `RateLimiterServlet`, which enforces a QPS-style rate limit per endpoint/IP [7](#0-6) , but this limits request *frequency*, not the *cost per request*, and does not cap `end - begin` or reject queries with abnormally large cycle gaps. There is no per-call cap on the cycle range, no check against `TransactionCapsule.validateSignature`, actuator `validate()`, or any address-ownership check in the read path — `GetRewardServlet` is a pure query endpoint, not a transaction, so none of the usual transaction-level economic guards (fees/energy/bandwidth) apply.

### Impact Explanation
Each query costs O(N) work (N = cycles elapsed since `beginCycle`, unbounded and growing purely with wall-clock time), with 2 DB reads (`getReward`, `getWitnessVote`) per vote per cycle. This matches TRON's "DoS via RPC-API" impact class: sustained CPU/DB-read amplification per request that scales with attacker-influenced state (age of an unwithdrawn vote) rather than a fixed bound, enabling degraded node responsiveness for legitimate API/RPC consumers when combined with request flooding within rate-limiter allowances.

### Likelihood Explanation
Preconditions required: `allowOldRewardOpt()` must be false and the account's `beginCycle` must be less than `newRewardCalStartCycle` (old algorithm still active for that range) — this is the default/legacy state on chains that have not activated the optimization proposal, and it is entirely plausible for older, long-unwithdrawn accounts on any deployment that hasn't turned on `allowOldRewardOpt`. Attacker cost is minimal: one ordinary freeze+vote transaction (normal fee), then simply waiting (no further transactions needed) while querying via the free, unauthenticated HTTP/RPC endpoint. The attack is fully repeatable and requires no privileged role — only an ordinary funded account to cast the initial vote, and anyone (even a different unprivileged caller, since `GetRewardServlet` takes an arbitrary address parameter) can trigger the expensive query afterward. The severity scales with the QPS rate limiter's configured allowance and the size of the wallet's unwithdrawn cycle window, both of which are outside the attacker's control per-request but grow automatically over time with zero attacker cost.

### Recommendation
Cap the maximum number of cycles processed per `getOldReward`/`computeReward` invocation (e.g., reject or truncate ranges exceeding a configurable maximum), or force lazy/periodic checkpointing of `beginCycle` (e.g., during `queryReward`, not just `withdrawReward`) so cost cannot grow unbounded with elapsed wall-clock time. Alternatively, always route to `RewardViCalService.getNewRewardAlgorithmReward` (VI-based O(1) computation) once `allowOldRewardOpt` is enabled network-wide, and consider deprecating/rate-limiting old-algorithm computation windows more aggressively at the API layer with a per-address cost-aware limiter rather than a flat QPS limiter.

### Proof of Concept
```java
// JUnit-style PoC sketch against MortgageServiceTest harness
@Test
public void testUnboundedGetOldRewardCost() {
  // setup: allowOldRewardOpt = false, allowChangeDelegation = true
  dynamicPropertiesStore.saveAllowOldRewardOpt(0L);
  dynamicPropertiesStore.saveNewRewardAlgorithmEffectiveCycle(Long.MAX_VALUE);

  byte[] account = randomAddress();
  // simulate one vote at cycle 1
  delegationStore.setBeginCycle(account, 1L);
  delegationStore.setEndCycle(account, 2L);
  AccountCapsule accountCapsule = buildAccountWithVotes(account, srAddress, 1_000_000L);
  accountStore.put(account, accountCapsule);
  seedRewardsAndVotesForCycles(1L, 100_000L, srAddress); // populate getReward/getWitnessVote

  for (long largeCycle : new long[]{10_000L, 100_000L}) {
    dynamicPropertiesStore.saveCurrentCycleNumber(largeCycle);
    long start = System.nanoTime();
    mortgageService.queryReward(account);
    long elapsed = System.nanoTime() - start;
    System.out.println("cycles=" + largeCycle + " elapsed(ns)=" + elapsed);
  }
  // Assert: elapsed time for 100_000 grows ~10x over 10_000 with no attacker cost increase,
  // demonstrating unbounded linear cost per query call.
}
```
Expected result: `queryReward` latency scales linearly with `currentCycle - beginCycle`, confirming unbounded per-call cost purely as a function of elapsed wall-clock cycles, reachable via `GetRewardServlet` with zero additional attacker cost per query beyond the rate limiter's allowed QPS.

### Citations

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L161-168)
```java
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      return reward + accountCapsule.getAllowance();
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
    }
    return reward + accountCapsule.getAllowance();
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L209-213)
```java
    if (beginCycle < newAlgorithmCycle) {
      long oldEndCycle = min(endCycle, newAlgorithmCycle,
          dynamicPropertiesStore.disableJavaLangMath());
      reward = getOldReward(beginCycle, oldEndCycle, srAddresses);
      beginCycle = oldEndCycle;
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L260-269)
```java
  private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
    if (dynamicPropertiesStore.allowOldRewardOpt()) {
      return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
    }
    long reward = 0;
    for (long cycle = begin; cycle < end; cycle++) {
      reward += computeReward(cycle, votes);
    }
    return reward;
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetRewardServlet.java (L20-30)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      long value = 0;
      byte[] address = Util.getAddress(request);
      if (address != null) {
        value = manager.getMortgageService().queryReward(address);
      }
      String out = JsonFormat.isInt64AsString()
          ? "{\"reward\": \"" + value + "\"}"
          : "{\"reward\": " + value + "}";
      response.getWriter().println(out);
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L103-136)
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

    String contextPath = req.getContextPath();
    String url = Strings.isNullOrEmpty(req.getServletPath())
        ? MetricLabels.UNDEFINED : contextPath + req.getServletPath();
    // int64_as_string is honored only on GET requests (URL query). POST is intentionally
    // unsupported because reading the body here would consume request.getReader() and
    // break downstream servlets that read it themselves.
    if ("GET".equalsIgnoreCase(req.getMethod())) {
      JsonFormat.setInt64AsString(Util.getInt64AsString(req));
    }
    try {
      resp.setContentType("application/json; charset=utf-8");

      if (acquireResource) {
        Histogram.Timer requestTimer = Metrics.histogramStartTimer(
            MetricKeys.Histogram.HTTP_SERVICE_LATENCY, url);
        super.service(req, resp);
        Metrics.histogramObserve(requestTimer);
      } else {
        resp.getWriter()
            .println(Util.printErrorMsg(new IllegalAccessException("lack of computing resources")));
      }
```
