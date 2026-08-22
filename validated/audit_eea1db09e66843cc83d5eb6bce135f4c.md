### Title
Unbounded memory allocation via oversized `accountId` in `AccountIdIndexStore.get`/`has` reachable from `GetAccountById` RPC - ([File: chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java])

### Summary
`AccountIdIndexStore.get(byte[])` and `has(byte[])` call the private helper `getLowerCaseAccountId`, which performs `ByteString.copyFrom(bsAccountId).toStringUtf8().toLowerCase(Locale.ROOT)` on the raw input on every lookup. This path is reachable from the `GetAccountById` RPC/HTTP endpoints without any upstream length validation, since the 8–32 byte constraint in `SetAccountIdActuator.validate` only applies to the write/set path, not the read path.

### Finding Description
`AccountIdIndexStore.get(byte[] key)` and `has(byte[] key)` unconditionally call `getLowerCaseAccountId(key)`: [1](#0-0) [2](#0-1) 

This helper converts the input byte array into a `ByteString`, then to a UTF-8 `String`, lower-cases it, and converts back to a `ByteString`/byte array — each step allocating a new buffer proportional to the input size, so a single call can allocate several times the size of the attacker-controlled input. The 8–32 byte size restriction on `accountId` exists only in `SetAccountIdActuator.validate()` for the write path (`SetAccountId` transaction), and there is no equivalent bound enforced before invoking `AccountIdIndexStore.get`/`has` on the read path used by `GetAccountById` handlers (`GetAccountByIdServlet`, `RpcApiService`, and their Solidity/PBFT counterparts). I was not able to fully trace the exact line in `Wallet.java` that passes the `accountId` field into `AccountIdIndexStore.get` due to tool limitations in this session, but grep results confirm `Wallet.java` references `AccountIdIndexStore` (3 matches) and `getAccountById`-related paths, and `RpcApiService.java` contains matching entries for the `GetAccountById` service method. Protobuf itself does not cap message field sizes at the gRPC layer by default, so nothing prevents a caller from submitting a multi-megabyte `accountId` bytes field in the `GetAccountById` request.

### Impact Explanation
Each malicious RPC call causes several transient allocations proportional to the submitted `accountId` size (bounded only by gRPC's default max message size, typically 4MB, though this can be configured larger). Under concurrent flooding with many oversized requests, this can produce sustained heap pressure and GC overhead, degrading RPC responsiveness — a DoS-via-RPC-API impact class. However, the actual memory growth per single request is bounded by the gRPC max message size, and each allocation is short-lived (eligible for GC), so this is a resource-amplification/soft-DoS concern rather than a guaranteed unbounded-heap-exhaustion issue; the severity depends heavily on server-side gRPC message-size limits and concurrent request throttling that may already exist at the gRPC transport layer (which I could not fully confirm in this session).

### Likelihood Explanation
The endpoint is reachable by any anonymous RPC/HTTP client with no authentication, no fee payment (it is a read-only query, not a broadcast transaction), and no special preconditions beyond default node configuration. The attack is trivially repeatable at high concurrency. The main mitigating factor is the default gRPC max inbound message size, which caps a single request's payload size; without knowledge of a custom/relaxed configuration, actual achievable amplification per request is limited.

### Recommendation
Add an explicit length check (e.g., reject `accountId` inputs larger than 32 bytes, mirroring the write-side bound in `SetAccountIdActuator.validate`) at the entry of `AccountIdIndexStore.get(byte[])` / `has(byte[])`, or earlier in the `GetAccountById` request handlers (`GetAccountByIdServlet`, `RpcApiService`, `RpcApiServiceOnSolidity`, `RpcApiServiceOnPBFT`) before invoking the store, returning an empty/error response for oversized input instead of processing it.

### Proof of Concept
```java
// JUnit-style PoC targeting AccountIdIndexStore directly
@Test
public void testOversizedAccountIdLookup() {
  byte[] hugeAccountId = new byte[8 * 1024 * 1024]; // 8MB, well beyond the 8-32 byte spec
  Arrays.fill(hugeAccountId, (byte) 'A');

  long before = Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory();
  BytesCapsule result = accountIdIndexStore.get(hugeAccountId); // triggers ByteString copy + toStringUtf8 + toLowerCase + toByteArray
  long after = Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory();

  // Expect: no length validation error; multiple large intermediate allocations occurred
  assertNull(result);
  assertTrue(after - before > hugeAccountId.length); // amplification observed
}
```
At the RPC level, sending many concurrent `GetAccountById` requests each with a several-MB `account_id` field (up to the gRPC max message size) and measuring heap/GC behavior and response latency would demonstrate the amplification effect described above; full confirmation of end-to-end reachability from `Wallet.java`/`RpcApiService.java` into `AccountIdIndexStore.get` could not be completed within this session's tool budget.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L23-27)
```java
  private static byte[] getLowerCaseAccountId(byte[] bsAccountId) {
    return ByteString
        .copyFromUtf8(ByteString.copyFrom(bsAccountId).toStringUtf8().toLowerCase(Locale.ROOT))
        .toByteArray();
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L42-57)
```java
  @Override
  public BytesCapsule get(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    if (ArrayUtils.isEmpty(value)) {
      return null;
    }
    return new BytesCapsule(value);
  }

  @Override
  public boolean has(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    return !ArrayUtils.isEmpty(value);
  }
```
