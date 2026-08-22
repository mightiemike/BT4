### Title
Unbounded CPU amplification via `Wallet#estimateEnergy` binary search — single HTTP POST triggers dozens of near-CPU-deadline VM executions - ([File: framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java])

### Summary
`Wallet.estimateEnergy` performs a binary search over the fee-limit space (`low=0..high=dps.getMaxFeeLimit()`, loop `while (low + TRX_PRECISION < high)`), invoking `cleanContextAndTriggerConstantContract` → `triggerConstantContract` → `callConstantContract` → `VMActuator.validate/execute` → `VM.play` for every iteration. Each of these calls runs a fresh EVM execution against a caller-controlled contract/bytecode with its own independent CPU deadline (`constantCallTimeoutMs`, enforced by `Program.checkCPUTimeLimit`), so a single unauthenticated HTTP POST to `/wallet/estimateenergy` can force the node to perform O(log2(maxFeeLimit)) full VM executions, each allowed to run close to the per-call CPU limit, with essentially no cost to the caller beyond one HTTP request.

### Finding Description
`EstimateEnergyServlet.doPost` ( [1](#0-0) ) accepts an unauthenticated, unsigned `TriggerSmartContract` from the request body and immediately calls `wallet.estimateEnergy(...)`. This method requires only that `vm.estimateEnergy=true` and `vm.supportConstant=true` on the node config, both commonly enabled on public/query nodes.

Inside `Wallet.estimateEnergy` ( [2](#0-1) ), the algorithm:
1. Runs one call at `high = dps.getMaxFeeLimit()`.
2. Possibly runs one call at `twoTimes = low*2` to tighten bounds.
3. Runs a binary search loop `while (low + TRX_PRECISION < high)` calling `cleanContextAndTriggerConstantContract` at `mid` on every iteration, each wrapped in a retry loop that re-attempts on `Program.OutOfTimeException` up to `estimateEnergyMaxRetry` times.
4. Runs one final call at the resolved `high`.

Each of these calls goes through `cleanContextAndTriggerConstantContract` → `triggerConstantContract` → `callConstantContract` ( [3](#0-2) ), which constructs a brand new `VMActuator` and calls `vmActuator.validate(context)` / `vmActuator.execute(context)`, ultimately invoking `VM.play(program, ...)` ( [4](#0-3) ). The per-call CPU budget is computed in `VMActuator.calculateCpuLimitInUs`, and for constant calls it uses `constantCallTimeoutMs` directly, independent of the transaction's energy/fee limit: [5](#0-4) 

Because the contract's actual execution time (wall/CPU time to exhaust operations near `constantCallTimeoutMs`) is essentially fixed and independent of the `feeLimit`/`mid` value being probed in each binary-search step, an attacker can craft a contract that consistently burns close to the full CPU deadline regardless of which energy limit is passed in (e.g., a tight loop that always executes until either energy or CPU runs out). Each such call causes a full VM execution near the per-call CPU deadline, and the binary search invokes this dozens of times (bounded by `log2(dps.getMaxFeeLimit()/TRX_PRECISION)`, typically tens of iterations given default `maxFeeLimit` values, plus retries on `OutOfTimeException`) for a single HTTP request that carries no bandwidth/energy fee since it never goes through `TransactionCapsule.validateSignature`, actuator `validate()` for economic checks, or broadcast/fee deduction — it's a pure read-only constant call path (`isEstimating=true`), and `RateLimiterServlet` only throttles request *rate* (QPS), not the CPU cost *per accepted request*.

Existing protections don't stop this: there's no signature/fee requirement (constant calls don't need to be signed transactions with real fees), `RateLimiterServlet` limits requests per second/IP but does not bound the CPU work done within a single accepted request, and the retry logic on `OutOfTimeException` (`estimateEnergyMaxRetry`) can further multiply the number of near-timeout VM executions per request rather than short-circuiting.

### Impact Explanation
This matches the "DoS via RPC-API" bounty class: CPU exhaustion / node stall triggerable by a single unauthenticated HTTP request against a commonly-enabled public endpoint (`vm.estimateEnergy=true`, `vm.supportConstant=true`). Repeated/concurrent requests of this kind can consume worker threads and CPU disproportionately to the cost paid by the attacker (zero on-chain fee), degrading or stalling the node's ability to serve other RPC/API clients or, if severe enough, impacting local block processing responsiveness.

### Likelihood Explanation
- Preconditions are default-adjacent: `vm.estimateEnergy` and `vm.supportConstant` are typical for full/public nodes offering `/wallet/estimateenergy` or the `eth_estimateGas` JSON-RPC method (`TronJsonRpcImpl.estimateEnergy`, [6](#0-5) ).
- Cost to attacker: one unauthenticated HTTP POST, no signed transaction, no fee, no funded account required — the contract can even be inline deploy bytecode (`triggerSmartContract.getContractAddress().isEmpty()` path in `triggerConstantContract`).
- Repeatable: can be sent repeatedly/concurrently up to whatever QPS/IP rate limit is configured, and each single accepted request still costs O(log2(maxFeeLimit)) × (near-CPU-deadline VM run), amplified further by `estimateEnergyMaxRetry`.
- I was unable to verify the exact default values of `constantCallTimeoutMs`, `dynamic.maxFeeLimit`, and `estimateEnergyMaxRetry` in `reference.conf` within this session (searches for these keys in `.conf` files did not resolve specific line content), so the precise number of iterations/worst-case wall-clock amplification per request could not be quantified exactly from the indexed content; a Devin session with full file access would be needed to confirm these constants for a precise bound.

### Recommendation
Bound the total CPU/wall-clock time budget for the entire `estimateEnergy` call (across all binary-search iterations) rather than only per individual VM invocation — e.g., track cumulative elapsed time across the loop and abort with an error once a global deadline is reached, independent of how many iterations have been performed. Additionally, consider capping the maximum number of binary-search iterations and retries more conservatively, and/or introducing a per-request CPU-time-based rate limiter for `estimateEnergyservlet`/`eth_estimateGas`-class endpoints in addition to the existing QPS-based `RateLimiterServlet`.

### Proof of Concept
```java
// JUnit-style PoC sketch (framework/src/test/java/org/tron/core/WalletTest.java style)
@Test
public void testEstimateEnergyCpuAmplification() {
  Args.getInstance().setEstimateEnergy(true);
  Args.getInstance().setSupportConstant(true);

  // Deploy/point at bytecode that loops until near CPU/energy exhaustion
  // regardless of the feeLimit passed in (e.g. an unconditional JUMPDEST loop
  // consuming ~constantCallTimeoutMs on every invocation).
  TriggerSmartContract contract = buildCpuBurnContract();
  TransactionCapsule trxCap = wallet.createTransactionCapsule(contract,
      ContractType.TriggerSmartContract);

  GrpcAPI.TransactionExtention.Builder trxExtBuilder = GrpcAPI.TransactionExtention.newBuilder();
  GrpcAPI.Return.Builder retBuilder = GrpcAPI.Return.newBuilder();
  GrpcAPI.EstimateEnergyMessage.Builder estimateBuilder = GrpcAPI.EstimateEnergyMessage.newBuilder();

  long singleCallStart = System.nanoTime();
  wallet.triggerConstantContract(contract, trxCap, trxExtBuilder, retBuilder);
  long singleCallCpuNs = System.nanoTime() - singleCallStart;

  long estimateStart = System.nanoTime();
  wallet.estimateEnergy(contract, trxCap, trxExtBuilder, retBuilder, estimateBuilder);
  long estimateCpuNs = System.nanoTime() - estimateStart;

  // Assert: total CPU time for estimateEnergy scales with number of binary
  // search iterations (~log2(maxFeeLimit)) rather than being bounded to a
  // constant multiple of a single call.
  Assert.assertTrue("estimateEnergy CPU cost should be bounded, but is O(log2(maxFeeLimit)) times a single call",
      estimateCpuNs < singleCallCpuNs * 3); // expected to FAIL, demonstrating amplification
}
```
At the HTTP layer, a single request:
```
POST /wallet/estimateenergy
{"owner_address":"...", "contract_address":"", "data":"<bytecode that always burns near-max CPU per invocation>", "call_value":0}
```
Expected observation: server-side wall-clock/CPU time for this single request is a multiple (tens of times) of the time for a single `/wallet/triggerconstantcontract` call using the same bytecode, demonstrating the FAITHFUL_METERING violation — cost to the attacker (one request) is far below the CPU work induced on the server.

### Citations

**File:** framework/src/main/java/org/tron/core/services/http/EstimateEnergyServlet.java (L58-62)
```java
      TransactionCapsule trxCap = wallet.createTransactionCapsule(build.build(),
          Protocol.Transaction.Contract.ContractType.TriggerSmartContract);

      wallet.estimateEnergy(build.build(), trxCap,
          trxExtBuilder, retBuilder, estimateEnergyBuilder);
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2986-3087)
```java
  public Transaction estimateEnergy(TriggerSmartContract triggerSmartContract,
      TransactionCapsule txCap, TransactionExtention.Builder txExtBuilder,
      Return.Builder txRetBuilder, GrpcAPI.EstimateEnergyMessage.Builder estimateBuilder)
      throws ContractValidateException, ContractExeException, HeaderNotFound, VMIllegalException {

    if (!Args.getInstance().estimateEnergy) {
      throw new ContractValidateException("this node does not support estimate energy");
    }

    if (!Args.getInstance().supportConstant) {
      throw new ContractValidateException("this node does not support constant, "
          + "so estimate energy cannot work");
    }
    int retry = Args.getInstance().estimateEnergyMaxRetry;

    DynamicPropertiesStore dps = chainBaseManager.getDynamicPropertiesStore();
    long high = dps.getMaxFeeLimit();

    Transaction transaction;

    while (true) {
      try {
        transaction = cleanContextAndTriggerConstantContract(
            triggerSmartContract, txCap, txExtBuilder, txRetBuilder, high);
        break;
      } catch (Program.OutOfTimeException e) {
        retry--;
        if (retry < 0) {
          throw e;
        }
      }
    }

    // If failed, return directly.
    if (transaction.getRet(0).getRet().equals(code.FAILED)) {
      txRetBuilder.setCode(response_code.CONTRACT_EXE_ERROR);
      estimateBuilder.setResult(txRetBuilder);
      return transaction;
    }

    long low = dps.getEnergyFee() * txExtBuilder.getEnergyUsed();

    long twoTimes = low * 2;
    if (twoTimes < high) {
      while (true) {
        try {
          transaction = cleanContextAndTriggerConstantContract(
              triggerSmartContract, txCap, txExtBuilder, txRetBuilder, twoTimes);

          if (transaction.getRet(0).getRet().equals(code.FAILED)) {
            low = twoTimes;
          } else {
            high = twoTimes;
          }

          break;
        } catch (Program.OutOfTimeException e) {
          retry--;
          if (retry < 0) {
            throw e;
          }
        }
      }
    }

    while (low + TRX_PRECISION < high) {
      long mid = (low + high) / 2;

      while (true) {
        try {
          transaction = cleanContextAndTriggerConstantContract(
              triggerSmartContract, txCap, txExtBuilder, txRetBuilder, mid);
          break;
        } catch (Program.OutOfTimeException e) {
          retry--;
          if (retry < 0) {
            throw e;
          }
        }
      }

      if (transaction.getRet(0).getRet().equals(code.FAILED)) {
        low = mid;
      } else {
        high = mid;
      }
    }

    // Retry the binary search result
    transaction = cleanContextAndTriggerConstantContract(
        triggerSmartContract, txCap, txExtBuilder, txRetBuilder, high);
    // Setting estimating result
    estimateBuilder.setResult(txRetBuilder);
    if (transaction.getRet(0).getRet().equals(code.SUCESS)) {
      txRetBuilder.setResult(true);
      txRetBuilder.setCode(response_code.SUCCESS);
      estimateBuilder.setEnergyRequired((long) ceil((double) high / dps.getEnergyFee(),
          dps.disableJavaLangMath()));
    }

    return transaction;
  }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3139-3168)
```java
  public Transaction callConstantContract(TransactionCapsule trxCap,
      Builder builder, Return.Builder retBuilder, boolean isEstimating)
      throws ContractValidateException, ContractExeException, HeaderNotFound, VMIllegalException {

    if (!Args.getInstance().isSupportConstant()) {
      throw new ContractValidateException("this node does not support constant");
    }

    Block headBlock;
    List<BlockCapsule> blockCapsuleList = chainBaseManager.getBlockStore()
        .getBlockByLatestNum(1);
    if (CollectionUtils.isEmpty(blockCapsuleList)) {
      throw new HeaderNotFound("latest block not found");
    } else {
      headBlock = blockCapsuleList.get(0).getInstance();
    }

    BlockCapsule headBlockCapsule = new BlockCapsule(headBlock);
    TransactionContext context = new TransactionContext(headBlockCapsule, trxCap,
        StoreFactory.getInstance(), true, false);
    VMActuator vmActuator = new VMActuator(true);

    try {
      vmActuator.validate(context);
      vmActuator.execute(context);
    } finally {
      // constant call runs on a pooled RPC worker; drop its thread-local VM config view so it
      // can never leak into a later (block/broadcast) execution on the same thread.
      VMConfig.clearLocalSnapshot();
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L192-192)
```java
        VM.play(program, OperationRegistry.getTable());
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L704-710)
```java
  static long calculateCpuLimitInUs(boolean isConstantCall, long maxCpuTimeOfOneTxMs,
      double cpuLimitInUsRatio, long constantCallTimeoutMs) {
    if (isConstantCall && constantCallTimeoutMs > 0L) {
      return constantCallTimeoutMs * VMConstant.ONE_THOUSAND;
    }
    return (long) (maxCpuTimeOfOneTxMs * VMConstant.ONE_THOUSAND * cpuLimitInUsRatio);
  }
```

**File:** framework/src/main/java/org/tron/core/services/jsonrpc/TronJsonRpcImpl.java (L491-514)
```java
  private void estimateEnergy(byte[] ownerAddressByte, byte[] contractAddressByte,
      long value, byte[] data, TransactionExtention.Builder trxExtBuilder,
      Return.Builder retBuilder, EstimateEnergyMessage.Builder estimateBuilder)
      throws ContractValidateException, ContractExeException, HeaderNotFound, VMIllegalException {

    TriggerSmartContract triggerContract = triggerCallContract(
        ownerAddressByte,
        contractAddressByte,
        value,
        data,
        0,
        null
    );

    TransactionCapsule trxCap = wallet.createTransactionCapsule(triggerContract,
        ContractType.TriggerSmartContract);
    Transaction trx =
        wallet.estimateEnergy(triggerContract, trxCap, trxExtBuilder, retBuilder, estimateBuilder);
    trxExtBuilder.setTransaction(trx);
    trxExtBuilder.setTxid(trxCap.getTransactionId().getByteString());
    trxExtBuilder.setResult(retBuilder);
    retBuilder.setResult(true).setCode(response_code.SUCCESS);
    estimateBuilder.setResult(retBuilder);
  }
```
