### Title
Uncaught `StackOverflowError`/`Error` from deeply-nested JSON in `Util.packTransaction` bypasses `catch (Exception e)` in `GetTransactionSignWeightServlet.doPost` - (File: framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java)

### Summary
`GetTransactionSignWeightServlet.doPost` only guards `Util.packTransaction` with `catch (Exception e)`, and the only pre-parsing defense, `Util.checkBodySize`, is invoked in `PostParams.getPostParams` and only checks total request-body byte length, not JSON nesting depth. A small request body with deeply nested JSON in `raw_data.contract[].parameter.value` can trigger a `StackOverflowError` (a `Throwable`/`Error`, not an `Exception`) during recursive JSON-to-protobuf parsing, which is not caught by `catch (Exception e)`.

### Finding Description
The request flow is: `doPost` → `PostParams.getPostParams(request)` → `Util.checkBodySize(input)` → `Util.packTransaction(params.getParams(), params.isVisible())`. [1](#0-0) [2](#0-1) 

`checkBodySize` is applied to the raw string length of the POST body only; it does not analyze or limit JSON structural depth. A payload can be made arbitrarily deeply nested while remaining small in total byte size (e.g., thousands of nested `{"a":` braces), passing the size check trivially. When `Util.packTransaction` subsequently drives `JsonFormat`/protobuf JSON parsing of that structure, deeply recursive descent into nested objects can exhaust the JVM stack and throw `StackOverflowError`.

Since `StackOverflowError` extends `Error`, not `Exception`, it is not caught by the `catch (Exception e)` block in `doPost`: [3](#0-2) 

This confirms the check-order issue described in the question: `checkBodySize` (a length check) runs before the expensive/recursive parsing in `packTransaction`, but it provides no protection against nesting-depth-based stack exhaustion, and the resulting `Error` is not caught by the generic exception handler.

### Impact Explanation
This maps to a Denial-of-Service class issue via the RPC/HTTP API. However, the scope of the impact is limited by JVM/servlet-container semantics: an uncaught `StackOverflowError` propagating out of `doPost` terminates only the executing worker thread's call stack for that single request. Jetty's `HttpChannel`/thread-pool execution (`org.eclipse.jetty.util.thread.QueuedThreadPool`/`Runnable.run()`) generally catches `Throwable` at a level above user servlet code, allowing the connection to be aborted and the thread returned to the pool rather than destabilizing the entire JVM. This is not verifiable further from the indexed code available here (Jetty runtime internals are not part of this repo), so whether it causes true worker-thread death versus a caught, isolated failure could not be confirmed with certainty from the codebase alone.

Assuming worst case (thread death without pool replenishment or resource leak per request), repeated exploitation could degrade the HTTP API's availability under sustained load — a DoS via RPC-API impact. But this requires many repeated requests, and the endpoint already inherits from `RateLimiterServlet`, indicating some rate limiting is applied, which increases the cost/difficulty of sustaining an attack at scale.

### Likelihood Explanation
- Precondition: none beyond unauthenticated HTTP access to the public `/wallet/getsignweight`-style endpoint (or equivalent), which is unprivileged and requires no funded account or signed transaction.
- Cost to attacker: negligible — a single small HTTP POST with deep JSON nesting.
- Repeatability: repeatable, subject to whatever rate limiting `RateLimiterServlet` enforces per IP.
- Feasibility of achieving node-wide instability (vs. isolated per-thread failure) is uncertain without confirming Jetty's request-handling `Throwable` catch behavior in this deployment, which could not be verified from the available files.

### Recommendation
- Add explicit JSON nesting-depth limits (and/or array/element count limits) in `Util.checkBodySize` or a pre-parse validation step, independent of raw byte length.
- Wrap the parsing call in `Util.packTransaction` (and similarly in all other servlets that call it, e.g. `BroadcastServlet`, `GetShieldTransactionHashServlet`, `GetTransactionApprovedListServlet`) with `catch (Throwable t)` instead of / in addition to `catch (Exception e)`, so unexpected `Error`s are converted into a clean HTTP error response rather than propagating uncontrolled.
- Consider using a JSON parser configuration that enforces a maximum nesting depth (many JSON libraries expose such a limit) before handing data to `JsonFormat`.

### Proof of Concept
Request-level PoC (conceptual, to be validated by a background agent with runtime access):
```
POST /wallet/getsignweight HTTP/1.1
Content-Type: application/json
Content-Length: <small>

{"transaction":{"raw_data":{"contract":[{"parameter":{"value":{"a":{"a":{"a": ... (repeat ~50,000 times) ...}}}}}]}}}
```
Expected observation: total body size stays under the `checkBodySize` threshold, so the request passes that check; parsing in `Util.packTransaction` then throws `StackOverflowError`, which is not caught by `catch (Exception e)` in `GetTransactionSignWeightServlet.doPost`, and the response is either an abrupt connection reset (uncaught error propagated to container) instead of the expected JSON error body that `Util.processError` would normally produce for a caught `Exception`.

A JUnit-level PoC would call `Util.packTransaction(deeplyNestedJsonString, false)` directly and assert that a `StackOverflowError` (or similar `Error`) is thrown rather than a `JsonFormat.ParseException`/`Exception`, confirming it would escape the servlet's `catch (Exception e)`. [2](#0-1) [1](#0-0)

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetTransactionSignWeightServlet.java (L24-37)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      Transaction transaction = Util.packTransaction(params.getParams(), params.isVisible());
      TransactionSignWeight reply = transactionUtil.getTransactionSignWeight(transaction);
      if (reply != null) {
        response.getWriter().println(Util.printTransactionSignWeight(reply, params.isVisible()));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/PostParams.java (L24-32)
```java
  public static PostParams getPostParams(HttpServletRequest request) throws Exception {
    String input = request.getReader().lines().collect(Collectors.joining(System.lineSeparator()));
    Util.checkBodySize(input);
    if (APPLICATION_FORM_URLENCODED.getMimeType().equals(request.getContentType())) {
      input = getJsonString(input);
    }
    boolean visible = Util.getVisiblePost(input);
    return new PostParams(input, visible);
  }
```
