### Title
Rate limiter is request-count-based, not CPU-cost-weighted, allowing sustained CPU exhaustion via cheap valid `mint` proof-generation requests to `CreateShieldedContractParametersServlet` - ([File: framework/src/main/java/org/tron/core/services/http/CreateShieldedContractParametersServlet.java])

### Summary
`CreateShieldedContractParametersServlet.doPost` forwards any well-formed `PrivateShieldedTRC20Parameters` (mint/transfer/burn) directly to `Wallet.createShieldedContractParameters`, which calls `ShieldedTRC20ParametersBuilder.build(boolean)` and performs a full zk-SNARK proof (`generateOutputProof`/`generateSpendProof`, `librustzcashSaplingBindingSig`) synchronously on the request thread. The only protection is `RateLimiterServlet`/`GlobalRateLimiter`, which is a pure token-count limiter (QPS or per-IP QPS) unaware of the wildly different CPU cost per request type, so an attacker can hold CPU usage near saturation while nominally staying under the configured request-rate ceiling.

### Finding Description
The HTTP request path is: `CreateShieldedContractParametersServlet.doPost` (line 23) → `Wallet.createShieldedContractParameters` → `ShieldedTRC20ParametersBuilder.build(boolean)` [1](#0-0) . Inside `build`, the `MINT` branch calls `generateOutputProof` which performs the actual zk-SNARK proving (a CPU-bound native computation via `JLibrustzcash`), and this happens before any blockchain-level economic gating (no transaction is broadcast/paid for at this stage — the servlet only *builds parameters*, it does not consume TRX bandwidth/energy) [2](#0-1) .

Before reaching the servlet's handler, `RateLimiterServlet.service` gates the request using either a per-endpoint adapter (`QpsRateLimiterAdapter`/`IPQPSRateLimiterAdapter`/`GlobalPreemptibleAdapter`, or the default `qps=1000` if unconfigured) and then `GlobalRateLimiter.acquirePermit` [3](#0-2) . `GlobalRateLimiter` is a Guava `RateLimiter`-based per-IP and global QPS gate — it counts requests per unit time, with no concept of the CPU work a given request will perform [4](#0-3) . Because it treats every accepted request identically regardless of whether the underlying handler does trivial work (e.g., `getnowblock`) or heavy zk-proof generation (`mint`/`transfer`/`burn`), an attacker who sends valid mint requests at a rate just under the configured QPS (which defaults to a generic API QPS, not tuned per handler cost) can keep the server continuously busy computing zk-SNARK proofs. Distributing requests across many source IPs defeats any per-IP QPS limiting, and even a single global QPS limiter can be saturated because the requests are individually valid and cheap to construct client-side (reusable dummy notes/spending keys), while each is expensive server-side.

This is a genuine no-privilege attack path reachable by any unauthenticated HTTP client — no signature validation, no account permission checks, and no fee/energy is charged at this parameter-building stage (which is separate from actually broadcasting the resulting `TriggerSmartContract` transaction). The servlet doesn't cap concurrent in-flight zk-proof computations (no per-endpoint `GlobalPreemptibleAdapter` semaphore-style concurrency cap is configured for this servlet by default in `reference.conf`) [5](#0-4) .

### Impact Explanation
This matches the "DoS via RPC-API" bounty impact class: sustained CPU saturation on the FullNode process degrades or denies service for all API consumers (RPC/HTTP/JSON-RPC), including block sync-serving threads if the servlet threads exhaust the Tomcat/Jetty thread pool or contend heavily for CPU cores with the proving native library. The impact is scoped to node availability/service degradation, not fund loss or consensus divergence, since the proof-generation itself does not touch chain state until a resulting transaction is broadcast and separately validated/paid for by energy/bandwidth.

### Likelihood Explanation
Feasibility depends heavily on deployment configuration:
- Preconditions require the operator to expose the shielded-transaction HTTP API (`vm.shieldedTRC20Transaction`) publicly and to have not tuned `rate.limiter.http` for `CreateShieldedContractParametersServlet` with a low, CPU-aware `GlobalPreemptibleAdapter` concurrency limit (`permit=N`), instead relying on the default QPS-based limiter.
- Cost to the attacker is low: constructing a `mint` request only requires a reusable dummy spending key/note (client-side, no chain interaction, no fee), and the request/response cycle is direct HTTP.
- Distributing across many source IPs defeats per-IP QPS limiting; a single global QPS limiter can still be exhausted if its configured value is not CPU-aware.
- This is repeatable indefinitely as long as the attacker sustains request throughput below the configured threshold.
- I could not verify from the index whether this specific servlet has non-default rate-limiter configuration in the actual production `config.conf` (only `reference.conf` defaults were available), nor whether `vm.shieldedTRC20Transaction` (mint/transfer/burn parameter building) is enabled by default in this build — these are configuration-dependent, reducing certainty of default-config exploitability. This is a design gap in the rate-limiting model (count-based, not cost-weighted) rather than a memory-safety or logic bug, so it should be treated as a hardening recommendation rather than a confirmed default-exploitable vulnerability.

### Recommendation
- Configure `GlobalPreemptibleAdapter` (concurrency-based, e.g., `permit=2` or similar) instead of a plain QPS strategy for `CreateShieldedContractParametersServlet`/`CreateShieldedContractParametersWithoutAskServlet`/`ScanShieldedTRC20NoteByIvkServlet`-type CPU-heavy endpoints, bounding concurrent zk-proof computations regardless of request rate.
- Add a dedicated CPU-cost-aware limiter (e.g., a bounded thread pool / semaphore sized to available cores) specifically around zk-SNARK proof generation in `ShieldedTRC20ParametersBuilder.build`, independent of the generic HTTP rate limiter.
- Consider requiring these endpoints to be disabled by default or restricted to authenticated/internal callers in production deployments, and document that per-IP QPS alone is insufficient for CPU-bound endpoints.

### Proof of Concept
Request-level PoC (illustrative; exact JSON schema per `PrivateShieldedTRC20Parameters`):
```
POST /wallet/createshieldedcontractparameters HTTP/1.1
Content-Type: application/json

{
  "from_amount": "100",
  "shielded_receives": [{"note": {"value": 100, "payment_address": "<dummy_addr>", "rcm": "<dummy_rcm>"}}],
  "shielded_TRC20_contract_address": "<dummy_addr21_hex>"
}
```
Load-test procedure:
1. Generate N dummy mint payloads reusing the same random spending key/payment address (client-side, no chain cost).
2. Fire requests at `rateLimit - 1` requests/window sustained across many source sockets/IPs to the endpoint.
3. Measure server-side CPU utilization (e.g., `top`/`perf`) attributable to `librustzcash` proving calls versus the configured QPS ceiling.
4. Expected result: CPU utilization approaches saturation (near 100% on proving threads) well before the configured request-count ceiling is reached, demonstrating that `RateLimiterServlet`'s count-based `acquirePermit` (in `framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java`) does not bound CPU-cost-weighted load, only request counts, confirming the "FAITHFUL_METERING" gap for this specific handler.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/CreateShieldedContractParametersServlet.java (L23-38)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      String contract = request.getReader().lines()
          .collect(Collectors.joining(System.lineSeparator()));
      Util.checkBodySize(contract);

      boolean visible = Util.getVisiblePost(contract);
      PrivateShieldedTRC20Parameters.Builder build = PrivateShieldedTRC20Parameters.newBuilder();
      JsonFormat.merge(contract, build, visible);

      ShieldedTRC20Parameters shieldedTRC20Parameters = wallet
          .createShieldedContractParameters(build.build());
      response.getWriter().println(JsonFormat.printToString(shieldedTRC20Parameters, visible));
    } catch (Exception e) {
      Util.processError(e, response);
    }
```

**File:** framework/src/main/java/org/tron/core/zen/ShieldedTRC20ParametersBuilder.java (L270-282)
```java
      switch (shieldedTRC20ParametersType) {
        case MINT:
          ReceiveDescriptionInfo receive = receives.get(0);
          receiveDescription = generateOutputProof(receive, ctx).getInstance();
          builder.addReceiveDescription(receiveDescription);

          mergedBytes = ByteUtil.merge(shieldedTRC20Address,
              ByteArray.fromLong(receive.getNote().getValue()),
              encodeReceiveDescriptionWithoutC(receiveDescription),
              encodeCencCout(receiveDescription));
          value = transparentFromAmount;
          builder.setParameterType("mint");
          break;
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L103-151)
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
    } catch (ServletException | IOException | BadMessageException e) {
      throw e;
    } catch (Exception unexpected) {
      logger.error("Http Api {}, Method:{}. Error：", url, req.getMethod(), unexpected);
    } finally {
      // CRITICAL: this clear pairs with the setInt64AsString call above. Removing it
      // will leak int64_as_string state across requests on reused Tomcat threads,
      // producing intermittent quoted/unquoted output that is very hard to debug.
      JsonFormat.clearInt64AsString();
      // Release whenever the per-endpoint permit was acquired (covers both the normal
      // completion path and the case where GlobalRateLimiter rejected the request).
      if (rateLimiter instanceof IPreemptibleRateLimiter && perEndpointAcquired) {
        ((IPreemptibleRateLimiter) rateLimiter).release();
      }
    }
```

**File:** framework/src/main/java/org/tron/core/services/ratelimiter/GlobalRateLimiter.java (L23-51)
```java
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

**File:** common/src/main/resources/reference.conf (L466-480)
```text
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
```
