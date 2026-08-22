This is confirmed but with an important qualification: `getblockbylimitnext` itself is already capped at 100 blocks via `BLOCK_LIMIT_NUM = 100` [1](#0-0) , so that specific example is not the strongest instance, but the underlying `ServletOutputStreamCopy`/`CharResponseWrapper` mechanism applies to *every* HTTP API response and has no size cap regardless of which endpoint is used.

### Title
Unbounded in-memory response duplication via `ServletOutputStreamCopy` enables HTTP-API memory-exhaustion DoS - ([File: framework/src/main/java/org/tron/core/services/filter/ServletOutputStreamCopy.java])

### Summary
`CharResponseWrapper` wraps every HTTP servlet response (via `HttpApiAccessFilter` and separately via `HttpInterceptor`) with a `ServletOutputStreamCopy` that mirrors every byte written to the client into an in-memory `ByteArrayOutputStream` with no upper bound on its growth. Any unprivileged client can trigger large read responses (any endpoint returning a large payload, not just `getblockbylimitnext`) and force the node to buffer the full response twice in memory per request, and concurrently across many requests, independent of `disabledApiList`.

### Finding Description
`HttpApiAccessFilter.doFilter` wraps every non-disabled request in a `CharResponseWrapper` before invoking the servlet chain [2](#0-1) . `CharResponseWrapper.getWriter()`/`getOutputStream()` creates a `ServletOutputStreamCopy` around the real output stream [3](#0-2) . `ServletOutputStreamCopy.write(int b)` writes each byte both to the real stream and to an internal `ByteArrayOutputStream copy`, which is only seeded with an initial capacity hint of 4096 bytes (misleadingly named `MAX_RESPONSE_SIZE`, but not actually a cap) and grows without bound as more bytes are written [4](#0-3) . `HttpInterceptor.doFilter` independently wraps the same response in a *second* `CharResponseWrapper`/`ServletOutputStreamCopy` purely to measure byte size for metrics, doubling the buffered memory per request [5](#0-4) . Neither filter enforces any response-size limit before or during buffering; the buffer is only read afterward (`getByteSize()`) for metrics purposes. There is no `disabledApiList`, rate limiter, or response-size check that bounds this per-request memory cost—`disabledApiList` only blocks specific endpoints entirely and does not throttle allowed ones [6](#0-5) . While the example endpoint cited (`getblockbylimitnext`) actually enforces `BLOCK_LIMIT_NUM = 100` blocks per request server-side [1](#0-0) , the underlying filter mechanism has no knowledge of or dependency on such per-endpoint limits—any endpoint (existing or future) that returns a large or attacker-influenced-size payload is subject to the same unbounded duplication, and an attacker can issue many concurrent requests against any allowed, moderately large-response endpoint to multiply memory pressure.

### Impact Explanation
This is a DoS via RPC-API (memory exhaustion / potential OOM or GC pressure leading to node stall) reachable by any unprivileged HTTP client, matching the "DoS via RPC-API" bounty impact class. The severity is bounded by whatever the largest allowed single response is (e.g., capped list/block endpoints), multiplied by concurrent request count, and doubled by the two independent wrapping filters (`HttpApiAccessFilter` + `HttpInterceptor`) rather than being truly unbounded per request for endpoints that already enforce output limits like `getblockbylimitnext`.

### Likelihood Explanation
No privileges, signed transactions, or fees are required—only sending HTTP GET/POST requests against a public FullNode/SolidityNode HTTP API port, which is enabled by default (`fullNodeEnable = true`) per `docs/configuration.md`. The attack is trivially repeatable and scales with attacker-controlled concurrency. However, actual impact severity for the cited endpoint (`getblockbylimitnext`) is limited by its 100-block cap, so the practical exploitability depends on finding/using an endpoint whose response size is large or attacker-controllable (this repo snapshot doesn't show conclusive evidence of a data endpoint without any size cap; that would require further per-servlet review beyond what was inspected).

### Recommendation
Add an explicit, configurable cap on the size of `ServletOutputStreamCopy`'s internal buffer (e.g., stop copying and mark truncated once a max-response-size threshold, similar to `jsonrpc.maxResponseSize`, is exceeded), and consolidate the two redundant wrapping filters (`HttpApiAccessFilter` and `HttpInterceptor`) into a single wrap to avoid double buffering. Additionally consider streaming size accounting (a counter) instead of copying full response bytes purely for metrics purposes.

### Proof of Concept
```
// Conceptual load test (not a full JUnit due to needing a large-response endpoint):
// 1. Pick an HTTP GET/POST endpoint under /wallet/* that is not in disabledApiList
//    and returns a response proportional to attacker-supplied query size.
// 2. Fire N concurrent requests, e.g.:
for (int i = 0; i < 200; i++) {
  executor.submit(() -> httpClient.execute(new HttpGet(largeResponseUrl)));
}
// 3. Sample JVM heap (e.g., via JFR/heap histogram) for ByteArrayOutputStream
//    instances retained by ServletOutputStreamCopy.copy during concurrent execution.
// Expected (if unbounded): heap usage grows roughly linearly with
// (response_size * concurrent_requests * 2) with no plateau/cap,
// as neither ServletOutputStreamCopy nor HttpApiAccessFilter/HttpInterceptor
// impose a maximum buffer size.
```

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetBlockByLimitNextServlet.java (L18-45)
```java
  private static final long BLOCK_LIMIT_NUM = 100;
  @Autowired
  private Wallet wallet;

  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      fillResponse(Util.getVisible(request), Long.parseLong(request.getParameter("startNum")),
          Long.parseLong(request.getParameter("endNum")), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      BlockLimit.Builder build = BlockLimit.newBuilder();
      JsonFormat.merge(params.getParams(), build, params.isVisible());
      fillResponse(params.isVisible(), build.getStartNum(), build.getEndNum(), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(boolean visible, long startNum, long endNum,
      HttpServletResponse response)
      throws IOException {
    if (endNum > 0 && endNum > startNum && endNum - startNum <= BLOCK_LIMIT_NUM) {
```

**File:** framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java (L43-44)
```java
        CharResponseWrapper responseWrapper = new CharResponseWrapper(resp);
        chain.doFilter(request, responseWrapper);
```

**File:** framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java (L60-74)
```java
  private boolean isDisabled(String endpoint) {
    boolean disabled = false;

    try {
      endpoint = URI.create(endpoint).normalize().toString();
      List<String> disabledApiList = CommonParameter.getInstance().getDisabledApiList();
      if (!disabledApiList.isEmpty()) {
        disabled = disabledApiList.contains(endpoint.split("/")[2].toLowerCase(Locale.ROOT));
      }
    } catch (Exception e) {
      logger.warn("check isDisabled except, endpoint={}, {}", endpoint, e.getMessage());
    }

    return disabled;
  }
```

**File:** framework/src/main/java/org/tron/core/services/filter/CharResponseWrapper.java (L21-49)
```java
  @Override
  public ServletOutputStream getOutputStream() throws IOException {
    if (writer != null) {
      throw new IllegalStateException("getWriter() has been called .");
    }

    if (outputStream == null) {
      outputStream = getResponse().getOutputStream();
      streamCopy = new ServletOutputStreamCopy(outputStream);
    }

    return streamCopy;
  }

  @Override
  public PrintWriter getWriter() throws IOException {
    if (outputStream != null) {
      throw new IllegalStateException("getOutputStream() has been called.");
    }

    if (writer == null) {
      streamCopy = new ServletOutputStreamCopy(getResponse().getOutputStream());
      // set auto flash so that copy can be valid
      writer = new PrintWriter(new OutputStreamWriter(streamCopy,
          getResponse().getCharacterEncoding()), true);
    }

    return writer;
  }
```

**File:** framework/src/main/java/org/tron/core/services/filter/ServletOutputStreamCopy.java (L9-28)
```java
class ServletOutputStreamCopy extends ServletOutputStream {

  private OutputStream outputStream;
  private ByteArrayOutputStream copy;
  private int MAX_RESPONSE_SIZE = 4096;

  public ServletOutputStreamCopy(OutputStream outputStream) {
    this.outputStream = outputStream;
    this.copy = new ByteArrayOutputStream(MAX_RESPONSE_SIZE);
  }

  @Override
  public void write(int b) throws IOException {
    outputStream.write(b);
    copy.write(b);
  }

  public int getStreamByteSize() {
    return this.copy.size();
  }
```

**File:** framework/src/main/java/org/tron/core/services/filter/HttpInterceptor.java (L40-45)
```java
      CharResponseWrapper responseWrapper = new CharResponseWrapper(
              (HttpServletResponse) response);
      chain.doFilter(request, responseWrapper);
      HttpServletResponse resp = (HttpServletResponse) response;
      int size = responseWrapper.getByteSize();
      MetricsUtil.meterMark(MetricsKey.NET_API_OUT_TRAFFIC, size);
```
