## Title
Missing Bounds Validation on `offset`/`limit` Pagination Parameters in HTTP Paginated List Servlets - (File: `framework/src/main/java/org/tron/core/services/http/GetPaginatedExchangeListServlet.java`, `GetPaginatedAssetIssueListServlet.java`, `GetPaginatedNowWitnessListServlet.java`)

### Summary
Several unauthenticated HTTP API endpoints that return paginated lists accept `offset` and `limit` parameters directly from the client via `Long.parseLong(request.getParameter(...))` (GET) or protobuf `PaginatedMessage` fields (POST), and pass them straight through to the underlying `Wallet` store-iteration methods without any server-side bounds checking at the servlet layer.

### Finding Description
`GetPaginatedAssetIssueListServlet.doGet`/`doPost` reads `offset` and `limit` and forwards them unchecked to `wallet.getAssetIssueList(offset, limit)`: [1](#0-0) 

`GetPaginatedExchangeListServlet` has the identical pattern, forwarding to `wallet.getPaginatedExchangeList(offset, limit)`: [2](#0-1) 

`GetPaginatedNowWitnessListServlet` likewise forwards raw `offset`/`limit` to `wallet.getPaginatedNowWitnessList(offset, limit)`: [3](#0-2) 

By contrast, the analogous `getPaginatedProposalList` on the `Wallet` class demonstrates that the codebase is aware bounds checking is required, since it explicitly rejects negative values and caps the limit before iterating the store: [4](#0-3) 

This is the same bug class as the reported issue: pagination `page`/`limit` values are accepted from an anonymous client and used directly to compute a range/skip over a data store without validating that `offset >= 0` and that `limit` is bounded to a sane maximum. I was not able to confirm from the indexed code whether `Wallet.getAssetIssueList(long, long)`, `getPaginatedExchangeList(long, long)`, and `getPaginatedNowWitnessList(long, long)` internally replicate the same `offset < 0 || limit < 0` / `MAX` capping guard that `getPaginatedProposalList` has — the index did not return their bodies. This should be verified directly in `framework/src/main/java/org/tron/core/Wallet.java` before treating this as fully confirmed; if those three methods lack an equivalent guard (unlike the proposal list method), the servlets provide a direct, unauthenticated path for a negative-offset or unbounded-limit request to reach a full-node LevelDB/RocksDB range iteration.

### Impact Explanation
If the corresponding `Wallet` methods do not clamp `limit`/`offset`, an anonymous client can:
- Trigger unbounded iteration over the asset-issue, exchange, or witness store by supplying an extremely large `limit`, causing excessive iterator work, GC pressure, and CPU/memory consumption on the full node/API node — a DoS vector.
- Supply a negative `offset` (which is allowed by `Long.parseLong`, unlike the frontend example that computed a negative skip from `page=0`) potentially causing exceptions, empty/incorrect results, or exposing unintended data ranges depending on how the range is constructed downstream in `Wallet`.
This maps to the "DoS via RPC-API" and "unauthenticated bulk data extraction" categories referenced in the report.

### Likelihood Explanation
These are unauthenticated HTTP endpoints reachable by any client capable of sending a GET/POST to the full-node HTTP API, requiring no signed transaction, no special permission, and no prior state. `RateLimiterServlet` is the only defense (rate limiting), not parameter validation. Exploitability is high in likelihood if the underlying `Wallet` methods lack equivalent guards to `getPaginatedProposalList`.

### Recommendation
- Add explicit server-side bounds validation identical to the pattern already used in `getPaginatedProposalList` (`limit < 0 || offset < 0` rejection, and clamping `limit` to a defined `*_COUNT_LIMIT_MAX`) inside `Wallet.getAssetIssueList(long, long)`, `getPaginatedExchangeList(long, long)`, and `getPaginatedNowWitnessList(long, long)`.
- Alternatively/additionally, validate `offset >= 0` and `1 <= limit <= MAX` at the servlet layer (`GetPaginatedAssetIssueListServlet`, `GetPaginatedExchangeListServlet`, `GetPaginatedNowWitnessListServlet`) before calling into `Wallet`, mirroring the checks already present in `GetBlockByLatestNumServlet` (`num > 0 && num < BLOCK_LIMIT_NUM`, see [5](#0-4) ) and `GetBlockByLimitNextServlet`.

### Proof of Concept
```
GET /wallet/getpaginatedexchangelist?offset=-1&limit=999999999
GET /wallet/getpaginatedassetissuelist?offset=-1&limit=999999999
GET /wallet/getpaginatednowwitnesslist?offset=-1&limit=999999999
```
These requests reach `GetPaginatedExchangeListServlet.doGet` / `GetPaginatedAssetIssueListServlet.doGet` / `GetPaginatedNowWitnessListServlet.doGet`, which parse the parameters with `Long.parseLong` and pass them directly to the corresponding `Wallet` pagination method with no visible clamping at the servlet layer, unlike the sibling `GetPaginatedProposalListServlet` path where `Wallet.getPaginatedProposalList` explicitly rejects negative values and caps `limit`.

**Caveat:** Full confirmation requires inspecting the bodies of `Wallet.getAssetIssueList(long, long)`, `Wallet.getPaginatedExchangeList(long, long)`, and `Wallet.getPaginatedNowWitnessList(long, long)` in `framework/src/main/java/org/tron/core/Wallet.java`, which were not retrievable from the current index. If a Devin session is started, these method bodies should be read directly to confirm presence/absence of equivalent bounds checks before treating this as a confirmed, exploitable finding.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedAssetIssueListServlet.java (L20-46)
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

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      String input = params.getParams();
      boolean visible = params.isVisible();
      PaginatedMessage.Builder build = PaginatedMessage.newBuilder();
      JsonFormat.merge(input, build, visible);
      fillResponse(build.getOffset(), build.getLimit(), visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException {
    AssetIssueList reply = wallet.getAssetIssueList(offset, limit);
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedExchangeListServlet.java (L20-44)
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

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      PaginatedMessage.Builder build = PaginatedMessage.newBuilder();
      JsonFormat.merge(params.getParams(), build, params.isVisible());
      fillResponse(build.getOffset(), build.getLimit(), params.isVisible(), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException {
    ExchangeList reply = wallet.getPaginatedExchangeList(offset, limit);
```

**File:** framework/src/main/java/org/tron/core/services/http/GetPaginatedNowWitnessListServlet.java (L21-45)
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

  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      PostParams params = PostParams.getPostParams(request);
      GrpcAPI.PaginatedMessage.Builder build = GrpcAPI.PaginatedMessage.newBuilder();
      JsonFormat.merge(params.getParams(), build, params.isVisible());
      fillResponse(build.getOffset(), build.getLimit(), params.isVisible(), response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }

  private void fillResponse(long offset, long limit, boolean visible, HttpServletResponse response)
      throws IOException, MaintenanceUnavailableException {
    GrpcAPI.WitnessList reply = wallet.getPaginatedNowWitnessList(offset, limit);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3275-3289)
```java
  public ProposalList getPaginatedProposalList(long offset, long limit) {

    if (limit < 0 || offset < 0) {
      return null;
    }

    long latestProposalNum = chainBaseManager.getDynamicPropertiesStore()
        .getLatestProposalNum();
    if (latestProposalNum <= offset) {
      return null;
    }
    limit =
        limit > PROPOSAL_COUNT_LIMIT_MAX ? PROPOSAL_COUNT_LIMIT_MAX : limit;
    long end = offset + limit;
    end = end > latestProposalNum ? latestProposalNum : end;
```

**File:** framework/src/main/java/org/tron/core/services/http/GetBlockByLatestNumServlet.java (L41-49)
```java
  private void fillResponse(boolean visible, long num, HttpServletResponse response)
      throws IOException {
    if (num > 0 && num < BLOCK_LIMIT_NUM) {
      BlockList reply = wallet.getBlockByLatestNum(num);
      if (reply != null) {
        response.getWriter().println(Util.printBlockList(reply, visible));
        return;
      }
    }
```
