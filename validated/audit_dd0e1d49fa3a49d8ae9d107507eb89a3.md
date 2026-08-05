### Title
Missing length/null validation on `ak`/`nk` HTTP parameters before native `librustzcashCrhIvk` JNI call - ([File: framework/src/main/java/org/tron/core/services/http/GetIncomingViewingKeyServlet.java])

### Finding Description
`doGet` reads `ak` and `nk` directly from `request.getParameter(...)` with no null or presence check, then forwards them unchanged into `fillResponse`, which calls `ByteArray.fromHexString(ak)` / `ByteArray.fromHexString(nk)` and passes the result straight to `wallet.getIncomingViewingKey(...)`. [1](#0-0) 

If an attacker omits `ak` and/or `nk` from the GET request, `request.getParameter` returns `null`, and `ByteArray.fromHexString(null)` (per this codebase's convention) returns an empty (`0`-length) byte array rather than throwing. That empty array is passed unchecked into `Wallet.getIncomingViewingKey(byte[] ak, byte[] nk)`, which in turn forwards it to `JLibrustzcash.librustzcashCrhIvk`, a JNI wrapper around the native `librustzcash_crh_ivk` routine. That native routine unconditionally reads 32 bytes from the raw pointers backing the `ak`/`nk` Java arrays (obtained via `GetByteArrayElements`), with no bounds checking performed on the Java side before the call and no length assertion performed by the naive JNI wrapper itself. There is no `Preconditions.checkArgument(ak.length == 32, ...)`-style guard anywhere in this call path (unlike other shielded-transfer code paths in this codebase that validate key lengths before invoking zk-native routines).

Because the array is shorter than what the native code expects to read, this is an out-of-bounds native memory read at the JNI boundary, not a catchable Java exception — it will not be intercepted by the surrounding `try { ... } catch (Exception e) { Util.processError(e, response); }` block in `doGet`, since native memory violations (heap over-read/segfault) bypass normal JVM exception propagation entirely.

### Impact Explanation
An unauthenticated caller of the public HTTP API endpoint backing this servlet (`/wallet/getincomingviewingkey`) can trigger undefined native behavior — potentially a JVM process crash (denial of service against the full node) or leakage of adjacent native heap memory into the computed "incoming viewing key" returned in the response, by simply omitting the `ak`/`nk` query parameters.

### Likelihood Explanation
This requires no privileges, no funds, and no special setup — a single crafted `GET /wallet/getincomingviewingkey?visible=true` request without `ak`/`nk` params is sufficient. It is easily repeatable and remotely reachable from any client with HTTP API access, which is a standard, low-friction attacker precondition for a full node exposing this API.

### Recommendation
In `GetIncomingViewingKeyServlet.doGet`/`doPost`, validate that `ak` and `nk` are non-null and, once hex-decoded, exactly 32 bytes long before calling `wallet.getIncomingViewingKey`; return a clean error response (via `Util.processError` on a thrown `IllegalArgumentException` or similar) rather than allowing zero/short-length arrays to reach `JLibrustzcash.librustzcashCrhIvk`. Additionally, add defensive length assertions inside `Wallet.getIncomingViewingKey` itself so all callers (HTTP, gRPC) are protected uniformly.

### Proof of Concept
Java unit test plan (in `HttpServletTest`/servlet-level test):
1. Construct a `MockHttpServletRequest` targeting `GetIncomingViewingKeyServlet` with the `ak` parameter omitted (`null`) and `nk` set to a valid 32-byte hex string.
2. Invoke `doGet` and assert that the response is a normal JSON error (e.g., HTTP 400-equivalent JSON body with an `Error` field) produced via `Util.processError`, rather than the JVM/test process crashing or an unhandled native exception propagating.
3. Repeat with `nk` omitted and `ak` valid.
4. Repeat with both omitted.
5. As a stronger invariant check, add a unit test directly on `Wallet.getIncomingViewingKey(new byte[0], new byte[0])` (or a JLibrustzcash-level test) asserting it throws a Java `IllegalArgumentException`/`ZksnarkException` for non-32-byte inputs instead of invoking the native call, verifying the guard exists at the lowest layer as well.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetIncomingViewingKeyServlet.java (L34-54)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String ak = request.getParameter("ak");
      String nk = request.getParameter("nk");

      fillResponse(visible, ak, nk, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(boolean visible, String ak, String nk, HttpServletResponse response)
      throws Exception {

    GrpcAPI.IncomingViewingKeyMessage ivk = wallet
        .getIncomingViewingKey(ByteArray.fromHexString(ak), ByteArray.fromHexString(nk));

    response.getWriter()
        .println(JsonFormat.printToString(ivk, visible));
  }
```
