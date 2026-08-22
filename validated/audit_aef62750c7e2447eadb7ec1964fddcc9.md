### Title
Unbounded, unmetered signature-recovery amplification in `TransactionUtil.getTransactionSignWeight` enables RPC/HTTP CPU-exhaustion DoS - ([File: actuator/src/main/java/org/tron/core/utils/TransactionUtil.java])

### Summary
`getTransactionSignWeight` is reachable by any anonymous client via `Wallet.getTransactionSignWeight` (gRPC) and `GetTransactionSignWeightServlet` (HTTP), takes an unsigned/arbitrary `Transaction` payload, and performs a full EC signature-recovery loop (`TransactionCapsule.checkWeight`) over every attached signature with no fee, energy, or bandwidth charge and no request-level rate limiter in the method itself. An attacker can therefore submit repeated requests with the maximum allowed number of garbage signatures to force expensive, unmetered cryptographic work on the node.

### Finding Description
The method first bounds the number of signatures by `chainBaseManager.getDynamicPropertiesStore().getTotalSignNum()` [1](#0-0) , then truncates each signature to `PER_SIGN_LENGTH` [2](#0-1) , loads the owner account, resolves the permission, and — if any signatures are present — calls `TransactionCapsule.checkWeight`, which performs EC signature recovery for each signature to compute the approving addresses' combined weight [3](#0-2) . This is a read-only, query-style RPC (unlike `broadcastTransaction`) so it is not subject to bandwidth/energy accounting, and no per-call cost cap or rate limiter exists inside `getTransactionSignWeight` or in its callers (`RpcApiService`, `FullNodeHttpApiService`, `GetTransactionSignWeightServlet`) based on the code reviewed. The only gate is the signature-count bound tied to `getTotalSignNum()`, which limits — but does not eliminate — the amount of EC recovery work per call; each call still performs up to `getTotalSignNum()` full signature-recovery operations plus an `AccountStore.get(owner)` lookup, entirely funded by network bandwidth/connection cost only, with no economic cost to the caller.

### Impact Explanation
This falls under "DoS via RPC-API": an unauthenticated caller can force the node to spend CPU cycles on EC signature recovery repeatedly and cheaply (bounded per-call by `getTotalSignNum()`, but unbounded in aggregate call volume since there is no rate limiter). At sufficient request rate this can degrade or exhaust CPU resources dedicated to the gRPC/HTTP service, affecting availability for other RPC/API clients on the same full node.

### Likelihood Explanation
Preconditions are minimal — only network access to the gRPC or HTTP wallet endpoint, matching the "unprivileged anonymous RPC/HTTP client" threat model. The per-request cost is bounded by `getTotalSignNum()` (a chain parameter, default value not confirmed in this pass — it is stored/read via `DynamicPropertiesStore.getTotalSignNum()`), which caps the worst case per call but does not prevent an attacker from issuing many concurrent/rapid requests, since the endpoint performs no fee/energy/bandwidth charging and I found no rate limiter specific to this method or servlet. Feasibility depends heavily on the actual value of `getTotalSignNum()` and on whether any node-level/global rate limiting (e.g., HTTP connection throttling, gRPC concurrency limits) exists elsewhere in the stack that were not part of the files reviewed — this could not be fully confirmed within the scope of this investigation.

### Recommendation
Add an explicit rate limiter (e.g., token-bucket per IP or global) for `getTransactionSignWeight` in `RpcApiService`/`FullNodeHttpApiService`/`GetTransactionSignWeightServlet`, and/or reduce the default `TOTAL_SIGN_NUM` bound, and consider caching/short-circuiting duplicate signature-recovery work for identical inputs.

### Proof of Concept
Send repeated `GetTransactionSignWeight` gRPC/HTTP requests, each with a `Transaction` containing `getTotalSignNum()` signatures of `PER_SIGN_LENGTH`-sized random bytes and a valid-looking single contract with an existing owner address; measure server-side CPU/wall-clock time scaling with request rate and signature count, confirming `TransactionCapsule.checkWeight`'s EC-recovery loop executes on every call with no throttling — this part (concrete benchmark numbers and confirmation of absence of any external rate limiter in the deployed node configuration) could not be executed/verified in this environment and would require a running node benchmark to fully substantiate the DoS severity.

### Citations

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L187-197)
```java
  public static Transaction truncateSignatures(Transaction trx) {
    Transaction.Builder builder = trx.toBuilder().clearSignature();
    for (ByteString sig : trx.getSignatureList()) {
      if (sig.size() > PER_SIGN_LENGTH) {
        builder.addSignature(ByteString.copyFrom(sig.substring(0, PER_SIGN_LENGTH).toByteArray()));
      } else {
        builder.addSignature(sig);
      }
    }
    return builder.build();
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L199-208)
```java
  public TransactionSignWeight getTransactionSignWeight(Transaction trx) {
    TransactionSignWeight.Builder tswBuilder = TransactionSignWeight.newBuilder();
    Result.Builder resultBuilder = Result.newBuilder();
    if (trx.getSignatureCount() > chainBaseManager.getDynamicPropertiesStore()
        .getTotalSignNum()) {
      resultBuilder.setCode(Result.response_code.OTHER_ERROR);
      resultBuilder.setMessage("too many signatures");
      tswBuilder.setResult(resultBuilder);
      return tswBuilder.build();
    }
```

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L246-253)
```java
        if (trx.getSignatureCount() > 0) {
          List<ByteString> approveList = new ArrayList<>();
          long currentWeight = TransactionCapsule.checkWeight(permission, trx.getSignatureList(),
              Sha256Hash.hash(CommonParameter.getInstance()
                  .isECKeyCryptoEngine(), trx.getRawData().toByteArray()), approveList);
          tswBuilder.addAllApprovedList(approveList);
          tswBuilder.setCurrentWeight(currentWeight);
        }
```
