### Title
Unbounded `limit`/`offset` parameters in `GetPaginatedAssetIssueListServlet.doGet` allow oversized response materialization - (File: framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java)

### Summary
`GetPaginatedAssetIssueListServlet.doGet` parses `offset` and `limit` directly from the HTTP request as raw `long` values with no upper bound, then passes them straight into `wallet.getAssetIssueList(offset, limit)` and serializes the entire result to JSON in one response write.

### Finding Description
The servlet reads client-supplied `offset` and `limit` parameters unchecked: [1](#0-0) 
These values are forwarded without any clamping to `fillResponse`, which calls `wallet.getAssetIssueList(offset, limit)` and writes the full serialized result via `JsonFormat.printToString`: [2](#0-1) 
The only protection present at the servlet layer is `RateLimiterServlet`, which throttles request *rate*, not response *size* — there is no explicit `MAX_LIMIT` check on the `limit` parameter before it reaches the wallet/store layer. I was not able to retrieve the body of `Wallet.getAssetIssueList(long, long)` or the underlying `AssetIssueStore` pagination method in this session to confirm whether an internal cap exists at that layer (e.g., a `Stream.limit(limit)` bounded by the actual number of stored asset issues). This is a material gap in verification: if the store-level pagination already bounds iteration to the real number of TRC10 assets that exist on-chain, then the practical "unbounded" claim is weaker, because the result set size is bounded by the total number of asset issues ever created on the network — a quantity that itself costs real TRX fees to grow (each asset issuance is a paid transaction), rather than being freely inflatable per-request by an attacker.

### Impact Explanation
Even in the best case for the attacker, the impact is capped by the current on-chain count of asset issues, not by an attacker-chosen arbitrary limit — meaning any DoS effect scales with legitimate existing chain state and repeated-request CPU/serialization cost, not with new attacker-supplied growth. This is a plausible but bounded-severity "DoS via RPC-API" concern: repeated calls with `limit=Long.MAX_VALUE` force full-list construction and JSON serialization on every request instead of a properly paginated slice, wasting CPU/memory proportional to total asset-issue count and response size, but not unbounded in the sense of a memory-exhaustion primitive independent of real chain data.

### Likelihood Explanation
No privileged access is required — any anonymous HTTP client can send `GET /getpaginatedassetissuelist?offset=0&limit=9223372036854775807`. The request is free (no transaction fee, no bandwidth/energy cost) and repeatable, subject only to `RateLimiterServlet`'s rate limiting. However, exploitability is constrained by: (a) the actual size of the asset-issue dataset on the target chain, which is bounded by real economic cost to create TRC10 assets, and (b) rate limiting already in place at the servlet layer. Without confirmed absence of a store-level cap (which I could not verify in this session), I cannot assert this reaches the "Advanced DoS" bar with high confidence.

### Recommendation
Add explicit server-side clamping of the `limit` parameter (e.g., reject or truncate to a `MAX_PAGE_SIZE` constant such as 1000) and validate `offset >= 0`/`limit > 0` before calling `wallet.getAssetIssueList`, mirroring caps used elsewhere in the codebase (e.g., `GetBlockByLimitNext`-style APIs). Return an explicit error for out-of-range values instead of silently processing them.

### Proof of Concept
```
GET /walletsolidity/getpaginatedassetissuelist?offset=0&limit=9223372036854775807 HTTP/1.1
Host: <node>
```
Expected (current behavior, unverified against store-level implementation): server attempts to materialize and serialize as many asset issues as exist in `assetIssueStore`, with no request-level cap; response time/heap usage should be profiled under this extreme `limit` to confirm actual server-side behavior, since the underlying `Wallet.getAssetIssueList`/store pagination code was not available for direct confirmation in this session.

**Note on confidence**: This finding is reported with reduced confidence because the implementation of `Wallet.getAssetIssueList(long, long)` and the underlying asset-issue store pagination logic could not be retrieved/verified in this session. A full assessment requires inspecting `framework/src/main/java/org/tron/core/Wallet.java` (`getAssetIssueList`) and the corresponding `AssetIssueStore` pagination method to determine whether an implicit cap (bounded by total on-chain asset count) already mitigates the unbounded-`limit` concern.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L20-29)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      long offset = Long.parseLong(request.getParameter("offset"));
      long limit = Long.parseLong(request.getParameter("limit"));
      fillResponse(offset, limit, visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L44-52)
```java
  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException {
    AssetIssueList reply = wallet.getAssetIssueList(offset, limit);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```
