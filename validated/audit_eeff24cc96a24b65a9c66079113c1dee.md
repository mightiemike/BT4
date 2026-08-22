### Title
Unbounded per-request response duplication in `ServletOutputStreamCopy`/`CharResponseWrapper` enables memory-exhaustion DoS via any non-disabled HTTP API - ([File: framework/src/main/java/org/tron/core/services/filter/ServletOutputStreamCopy.java])

### Summary
`HttpApiAccessFilter.doFilter` wraps every HTTP response in a `CharResponseWrapper`, whose `getWriter()`/`getOutputStream()` create a `ServletOutputStreamCopy` that mirrors every byte written to the client into an in-memory `ByteArrayOutputStream`. The declared `MAX_RESPONSE_SIZE = 4096` is only used as the initial capacity hint for the `ByteArrayOutputStream` constructor and is never enforced as an upper bound, so the copy buffer grows without limit as the response grows, independent of `disabledApiList` filtering.

### Finding Description
`HttpApiAccessFilter.doFilter` (framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java, lines 34-44) only rejects requests whose endpoint is in `disabledApiList`; any allowed endpoint gets wrapped: [1](#0-0) 

`CharResponseWrapper.getWriter()`/`getOutputStream()` create a `ServletOutputStreamCopy` for every response that passes through the filter (and separately again in `HttpInterceptor`): [2](#0-1) 

`ServletOutputStreamCopy.write(int b)` writes each byte both to the real client output stream and to an internal `ByteArrayOutputStream copy`, which is only seeded with a 4096-byte initial capacity but auto-grows without any cap check on every write call: [3](#0-2) 

Because `MAX_RESPONSE_SIZE` is used purely as the constructor's initial-capacity argument (`new ByteArrayOutputStream(MAX_RESPONSE_SIZE)`) rather than as an enforced maximum, there is no code path that truncates, rejects, or streams-through without buffering once a response exceeds 4096 bytes — the buffer simply reallocates and keeps growing to match the full response size. Since large read-only HTTP APIs such as `getblockbylimitnext` (backed by `GetBlockByLimitNextServlet`) can return large ranges of full block data, an attacker can trigger multi-megabyte responses, each of which is fully duplicated in heap memory for the lifetime of the request via this mirrored buffer. This is unrelated to and unaffected by `disabledApiList`, since that check only blocks specific configured endpoints and does not throttle or cap response size for allowed endpoints. Concurrent requests each allocate independent `ServletOutputStreamCopy`/`ByteArrayOutputStream` instances, so memory usage scales with `concurrent_requests × response_size`, with no global or per-request cap observed in this code path.

### Impact Explanation
This matches the "DoS via RPC-API" bounty class: an unprivileged HTTP client can force the node to hold multiple large, fully-duplicated response buffers in memory concurrently, risking OOM and node crash/stall, purely by issuing a moderate number of concurrent large-range read requests against any HTTP API not present in `disabledApiList`.

### Likelihood Explanation
Preconditions are minimal: the target API must simply not be in the (empty-by-default) `disabledApiList`; large-range read APIs like `getblockbylimitnext` are enabled and reachable by any client, without authentication, payment, or transaction cost — this is a pure HTTP GET/POST cost to the attacker (network bandwidth only). Repeatability is trivial since each request independently re-triggers the unbounded buffer allocation, and the attack can be parallelized to amplify memory pressure.

### Recommendation
Enforce a real maximum on `ServletOutputStreamCopy`'s internal buffer: throw/abort or drop copying once `copy.size()` exceeds a fixed cap (e.g., stop buffering additional bytes once `MAX_RESPONSE_SIZE` reached, while still forwarding to `outputStream` for the client), or avoid double-buffering entirely by only capturing the size (already tracked via `getStreamByteSize()`) without retaining a full byte copy. Additionally, consider limiting result-set/range sizes for heavy read APIs like `getblockbylimitnext` at the servlet layer and applying per-connection/global concurrent request or memory quotas independent of `disabledApiList`.

### Proof of Concept
```
# Raw HTTP sequence (default fullnode HTTP API, port 8090)
# Attacker issues many concurrent large-range requests:
for i in $(seq 1 100); do
  curl -s -X POST http://<node>:8090/wallet/getblockbylimitnext \
    -d '{"startNum":1,"endNum":5000}' &
done
wait
```
Expected observation (heap profiling / JFR on the node process): for each in-flight request, a `ServletOutputStreamCopy.copy` `ByteArrayOutputStream` grows to match the full serialized block-range response (potentially multi-MB), and total retained size scales linearly with `concurrent_requests × response_size`, with no ceiling enforced anywhere in `ServletOutputStreamCopy`/`CharResponseWrapper`, confirming the described unbounded memory growth.

### Citations

**File:** framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java (L34-44)
```java
        if (isDisabled(endpoint)) {
          resp.setStatus(HttpServletResponse.SC_NOT_FOUND);
          resp.setContentType("application/json; charset=utf-8");
          JSONObject jsonObject = new JSONObject();
          jsonObject.put("Error", "this API is unavailable due to config");
          resp.getWriter().println(jsonObject.toJSONString());
          return;
        }

        CharResponseWrapper responseWrapper = new CharResponseWrapper(resp);
        chain.doFilter(request, responseWrapper);
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

**File:** framework/src/main/java/org/tron/core/services/filter/ServletOutputStreamCopy.java (L9-24)
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
```
