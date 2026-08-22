### Title
Unbounded per-request DB prefix scan + sort in `getWithPrefix()` enables RPC-API DoS via `GetDelegatedResourceAccountIndexV2Servlet` - ([File: framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java])

### Summary
`GetDelegatedResourceAccountIndexV2Servlet.doGet`/`doPost` pass an attacker-controlled address directly into `Wallet.getDelegatedResourceAccountIndexV2`, which calls `DelegatedResourceAccountIndexStore.getWithPrefix()`. That method performs two unbounded `prefixQuery` DB scans (one per `V2_FROM_PREFIX`/`V2_TO_PREFIX`) and materializes+sorts the full result set on every single HTTP call, with no cap on the number of delegation records per address. An attacker who first creates many delegation index entries for one address can subsequently issue repeated cheap HTTP GET/POST requests that each trigger an O(n log n) scan/sort, unlike a single fixed-cost read.

### Finding Description
The request flow is: HTTP GET/POST -> `GetDelegatedResourceAccountIndexV2Servlet.doGet`/`doPost` (framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java:26-58) -> `wallet.getDelegatedResourceAccountIndexV2(address)` -> `DelegatedResourceAccountIndexStore.getV2Index` -> `getWithPrefix(V2_FROM_PREFIX, V2_TO_PREFIX, address)` (chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java:114-138).

`getWithPrefix()` does:
```
List<DelegatedResourceAccountIndexCapsule> tmpToList =
    new ArrayList<>(this.prefixQuery(key).values());
tmpToList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
```
for both the `from` and `to` prefixes, with no limit on the number of matched keys [1](#0-0) . Each delegation created via a `DelegateResourceContract` writes one entry under `V2_FROM_PREFIX+from+to` and one under `V2_TO_PREFIX+to+from` via `delegateV2()` [2](#0-1) . An attacker fully controls how many distinct receiver addresses they delegate to from a single owner address, so the number of keys under that owner's `V2_FROM_PREFIX` prefix scales linearly with the number of delegation transactions the attacker is willing to pay for.

The servlet itself only applies `RateLimiterServlet`'s generic per-endpoint rate limiting; it does not check the size of the account's delegation set before invoking the store lookup [3](#0-2) . There is no pagination, cap, or streaming — the full result set is loaded into memory and sorted synchronously on the request thread for every call.

### Impact Explanation
This maps to "DoS via RPC-API": once an attacker has built up a large delegation index for one address (a one-time, self-funded cost paid in TRX bandwidth/energy fees for the delegation transactions), every subsequent read of `GetDelegatedResourceAccountIndexV2` for that address costs the node O(n log n) DB scan+sort work, where n is attacker-chosen and unbounded. Repeated invocation from an anonymous, unprivileged HTTP client can sustain elevated CPU/IO load on any node exposing the HTTP/JSON-RPC API, degrading service for other API consumers. This is a genuine violation of "cheap read, cheap cost" expectations for a read-only RPC endpoint, though it does not lead to consensus divergence, fund loss, or key leakage — its blast radius is limited to the specific node(s) whose HTTP API is exposed and to this and structurally similar prefix-scan endpoints.

### Likelihood Explanation
Feasible for any unprivileged, self-funded attacker: creating n delegation records requires broadcasting n `DelegateResourceContract` transactions (or the VM-level `DelegateResourceProcessor` equivalent) from the attacker's own account, each subject to normal bandwidth/energy fees but no special privilege or minimum-amount floor that would make thousands of small delegations prohibitively expensive. After that one-time setup, the attacker can repeat the GET/POST request indefinitely, each call re-scanning and re-sorting the same large set; the `RateLimiterServlet` limits request rate but not per-request cost, so a sustained but rate-limit-compliant request stream can still keep the store doing expensive work continuously. The attack is fully repeatable and does not depend on any race condition or timing window.

### Recommendation
- Cap and/or paginate `getWithPrefix()`/`prefixQuery` results (e.g., limit max keys scanned per request, return an error or truncated result beyond a threshold).
- Track and persist a per-address delegation count so the store can reject or short-circuit before doing large prefix scans.
- Avoid re-sorting on every read; store index entries pre-sorted (e.g., by using a timestamp-ordered key encoding) so no in-memory sort is needed.
- Add endpoint-specific cost-based rate limiting (not just request-count limiting) for `GetDelegatedResourceAccountIndexV2Servlet` and related JSON-RPC equivalents.

### Proof of Concept
```java
// framework/src/test/java/.../DelegatedResourceAccountIndexStoreDosTest.java
@Test
public void testUnboundedPrefixScanCost() {
  byte[] owner = randomAddress();
  // Setup: attacker-controlled, self-funded creation of 50k delegations
  for (int i = 0; i < 50_000; i++) {
    byte[] receiver = randomAddress();
    delegatedResourceAccountIndexStore.delegateV2(owner, receiver, i + 1L);
  }

  // Benchmark repeated "cheap" read calls as an unprivileged HTTP client would trigger
  long start = System.nanoTime();
  for (int i = 0; i < 100; i++) {
    wallet.getDelegatedResourceAccountIndexV2(ByteString.copyFrom(owner));
  }
  long elapsed = System.nanoTime() - start;

  // Assert: latency scales with n and violates a fixed per-request budget
  // (compare against a baseline with e.g. 100 delegations to show superlinear growth)
  assertTrue("per-call cost should stay within fixed budget but does not",
      elapsed / 100 > FIXED_BUDGET_NANOS);
}
```
Expected result: average per-call latency for the 50k-delegation address grows far beyond the latency observed for a small (e.g. 100-entry) address, demonstrating that `getWithPrefix()`'s cost is attacker-controlled and unbounded per HTTP request, confirmed by direct code inspection of `getWithPrefix()` [1](#0-0)  and the servlet's direct pass-through of attacker-supplied address to that call [4](#0-3) .

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L77-89)
```java
  public void delegateV2(byte[] from, byte[] to, long time) {
    byte[] fromKey = Bytes.concat(V2_FROM_PREFIX, from, to);
    DelegatedResourceAccountIndexCapsule toIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(to));
    toIndexCapsule.setTimestamp(time);
    this.put(fromKey, toIndexCapsule);

    byte[] toKey = Bytes.concat(V2_TO_PREFIX, to, from);
    DelegatedResourceAccountIndexCapsule fromIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(from));
    fromIndexCapsule.setTimestamp(time);
    this.put(toKey, fromIndexCapsule);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegatedResourceAccountIndexStore.java (L118-138)
```java
  private DelegatedResourceAccountIndexCapsule getWithPrefix(byte[] fromPrefix, byte[] toPrefix, byte[] address) {
    DelegatedResourceAccountIndexCapsule tmpIndexCapsule =
        new DelegatedResourceAccountIndexCapsule(ByteString.copyFrom(address));

    byte[] key = Bytes.concat(fromPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpToList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpToList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    List<ByteString> list = tmpToList.stream()
        .map(DelegatedResourceAccountIndexCapsule::getAccount).collect(Collectors.toList());
    tmpIndexCapsule.setAllToAccounts(list);

    key = Bytes.concat(toPrefix, address);
    List<DelegatedResourceAccountIndexCapsule> tmpFromList =
        new ArrayList<>(this.prefixQuery(key).values());
    tmpFromList.sort(Comparator.comparing(DelegatedResourceAccountIndexCapsule::getTimestamp));
    list = tmpFromList.stream().map(DelegatedResourceAccountIndexCapsule::getAccount).collect(
        Collectors.toList());
    tmpIndexCapsule.setAllFromAccounts(list);
    return tmpIndexCapsule;
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L26-37)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String address = request.getParameter(VALUE_FIELD_NAME);
      if (visible) {
        address = Util.getHexAddress(address);
      }
      fillResponse(ByteString.copyFrom(ByteArray.fromHexString(address)), visible, response);
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/GetDelegatedResourceAccountIndexV2Servlet.java (L60-69)
```java
  private void fillResponse(ByteString address, boolean visible, HttpServletResponse response)
      throws IOException {
    DelegatedResourceAccountIndex reply =
        wallet.getDelegatedResourceAccountIndexV2(address);
    if (reply != null) {
      response.getWriter().println(JsonFormat.printToString(reply, visible));
    } else {
      response.getWriter().println("{}");
    }
  }
```
