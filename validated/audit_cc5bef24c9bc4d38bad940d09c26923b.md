### Title
Underpriced public CPU work via triggerConstantContract/estimateEnergy read-only VM execution path - ([File: actuator/src/main/java/org/tron/core/actuator/VMActuator.java])

### Summary
`triggerConstantContract`/`estimateEnergy` (JSON-RPC/gRPC/HTTP) execute `VM.play` through `VMActuator` with `isConstantCall=true`, which never deducts TRX/energy from the caller and is bounded only by wall-clock (`checkCPUTimeLimit`) rather than by economic cost. This lets any unprivileged caller repeatedly submit bytecode that maximizes CPU work up to the constant-call CPU deadline, at zero on-chain cost per call, since no fee/energy is burned on this ET_PRE_TYPE read-only path.

### Finding Description
`Wallet.callConstantContract` builds a `VMActuator(true)` and calls `vmActuator.validate(context); vmActuator.execute(context);` [1](#0-0) . Inside `VMActuator.call()`/`create()`, when `isConstantCall` is true, `energyLimit = maxEnergyLimit` (a config value, default 100,000,000, or bounded by `feeLimit`), and the per-tx CPU deadline is computed via `calculateCpuLimitInUs(isConstantCall, maxCpuTimeOfOneTx, ratio, constantCallTimeoutMs)` [2](#0-1) [3](#0-2) . Execution then runs `VM.play(program, ...)` which loops opcodes, checking `program.checkCPUTimeLimit(opName)` after each spend, throwing `OutOfTimeException` only once the wall-clock deadline is exceeded [4](#0-3) [5](#0-4) .

Critically, in `VMActuator.execute()`, when `isConstantCall` is true, the result (including exceptions) is simply captured into `context` and returned — no `rootRepository.commit()`, no balance/energy deduction, no fee settlement occurs [6](#0-5) . This is intentional/documented behavior for the read-only estimate/query path, not a bug in isolation — Tron's design has always made constant calls free of settlement. Rate limiting exists only at the generic HTTP/RPC layer (`RateLimiterServlet`, `GlobalRateLimiter`, per-servlet QPS strategies) which defaults to `qps=1000` unless an operator explicitly configures a stricter per-endpoint limit for `TriggerConstantContractServlet` or `estimateEnergy` [7](#0-6) [8](#0-7) . No per-request accounting exists tying constant-call CPU time to any charged resource.

The `estimateEnergy` binary-search loop compounds this: it repeatedly retries `cleanContextAndTriggerConstantContract` up to `estimateEnergyMaxRetry` (default 3) times on `OutOfTimeException`, and executes O(log(maxFeeLimit)) VM calls per single estimateEnergy request [9](#0-8) , multiplying CPU cost per client-initiated request without any additional fee.

This matches known, documented behavior (see the extensive comments around `constantCallTimeoutMs` referencing issue #6266) — the constant-call CPU deadline is operator-configurable exactly because of this concern, and the default falls back to sharing the block-processing deadline (`maxCpuTimeOfOneTx * maxTimeRatio`), which can be substantially larger than a single opcode-execution slice, giving each free call a nontrivial CPU budget.

### Impact Explanation
An attacker with no TRX balance and no signed/broadcast transaction requirement (constant calls do not require a valid signature or fee payment since they never commit or check balance sufficiency against the true energy price) can drive sustained CPU load on any full node exposing `triggerconstantcontract`/`estimateenergy`/`eth_call`/`eth_estimateGas`. Repeated worst-case bytecode (deeply nested CALL/DELEGATECALL/CALLCODE within depth limits, tight loops) run up to the per-call CPU deadline (which may be tens to hundreds of milliseconds depending on `maxCpuTimeOfOneTx`/`maxTimeRatio`/`constantCallTimeoutMs`) at effectively zero cost, degrading node responsiveness/availability for legitimate users — a resource-exhaustion / denial-of-service class impact scoped to public RPC/HTTP nodes.

### Likelihood Explanation
High feasibility for any node that exposes `supportConstant=true` (required for `triggerconstantcontract`) with default/generous rate limits, since the default rate-limit strategy is `qps=1000` per servlet unless explicitly hardened, and `GlobalRateLimiter`/per-IP limiters are opt-in configuration, not hard defaults. No signature, balance, or resource requirement gates the caller. This is fully reachable from a plain HTTP POST to `/wallet/triggerconstantcontract` or the gRPC `Wallet/TriggerConstantContract`/`Wallet/EstimateEnergy` methods with attacker-supplied bytecode/contract address, requiring only that a target contract with worst-case bytecode exists (which the attacker can itself deploy, subject only to normal contract-creation fees for the one-time deploy, not per-call).

### Recommendation
- Enforce a stricter default (non-1000 qps) rate limit specifically for `TriggerConstantContractServlet`, `TriggerConstantContractServlet`/JSON-RPC `eth_call`/`eth_estimateGas`, and the `EstimateEnergy` RPC methods, and document operators should configure `IPQPSRateLimiterAdapter` for these by default rather than relying on the generic fallback.
- Consider adding a lightweight cost/complexity pre-check (e.g., static bytecode size/loop heuristics) or a strict, low `constantCallTimeoutMs` default rather than 0 (which falls back to the potentially large block-processing deadline).
- Track and cap per-IP cumulative constant-call CPU time server-side (a CPU-time based token bucket) independent of QPS, since QPS alone doesn't bound total CPU consumed per request when execution time itself is the attack surface.

### Proof of Concept
Integration test plan (`framework/src/test` style, similar to `WalletTest.testTriggerConstant`):
1. Deploy (once) a contract whose fallback/queried function performs deeply nested `DELEGATECALL`/`CALLCODE` recursion up to the max call-depth limit combined with tight bytecode loops designed to maximize opcode dispatch and energy-cost computation overhead per unit wall-clock time (worst-case CPU, e.g. `JUMPDEST`/`JUMP` loops interleaved with `CALLCODE` to self).
2. In a loop (e.g., 1000 iterations), invoke `wallet.triggerConstantContract(contract, trxCap, trxExtBuilder, retBuilder)` directly (or via `RpcApiService`/`TriggerConstantContractServlet`) with `feeLimit=0` and an account with zero TRX balance.
3. Assert: (a) each call succeeds or throws `Program.OutOfTimeException` without ever throwing a balance/fee/energy-insufficiency exception (confirming zero-cost execution), (b) `AccountCapsule` balance/energy of the caller account is unchanged across all iterations (`assertEquals(0, account.getBalance())`), (c) measure wall-clock/CPU time consumed by the node thread across the loop (e.g., via `ThreadMXBean.getThreadCpuTime`) and assert it approaches `iterations * constant-call CPU deadline`, demonstrating high CPU cost with `0` TRX spent — the disparity being the vulnerability signal.

### Citations

**File:** framework/src/main/java/org/tron/core/Wallet.java (L2999-3072)
```java
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
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L3159-3163)
```java
    VMActuator vmActuator = new VMActuator(true);

    try {
      vmActuator.validate(context);
      vmActuator.execute(context);
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L225-232)
```java
        if (isConstantCall) {
          if (result.getException() != null) {
            result.setRuntimeError(result.getException().getMessage());
            result.rejectInternalTransactions();
          }
          context.setProgramResult(result);
          return;
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L516-529)
```java
      long energyLimit;
      if (isConstantCall) {
        energyLimit = maxEnergyLimit;
      } else {
        AccountCapsule creator = rootRepository
            .getAccount(deployedContract.getInstance().getOriginAddress().toByteArray());
        energyLimit = getTotalEnergyLimit(creator, caller, contract, feeLimit, callValue);
      }

      long thisTxCPULimitInUs = calculateCpuLimitInUs(isConstantCall,
          rootRepository.getDynamicPropertiesStore().getMaxCpuTimeOfOneTx(),
          getCpuLimitInUsRatio(), CommonParameter.getInstance().getConstantCallTimeoutMs());
      long vmStartInUs = System.nanoTime() / VMConstant.ONE_THOUSAND;
      long vmShouldEndInUs = vmStartInUs + thisTxCPULimitInUs;
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

**File:** actuator/src/main/java/org/tron/core/vm/VM.java (L86-90)
```java
          /* check if cpu time out */
          program.checkCPUTimeLimit(opName);

          /* exec op action */
          op.execute(program);
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1241-1258)
```java
  public void checkCPUTimeLimit(String opName) {

    if (CommonParameter.getInstance().isDebug()) {
      return;
    }
    if (CommonParameter.getInstance().isSolidityNode()) {
      return;
    }
    long vmNowInUs = System.nanoTime() / 1000;
    if (vmNowInUs > getVmShouldEndInUs()) {
      logger.info(
          "minTimeRatio: {}, maxTimeRatio: {}, vm should end time in us: {}, "
              + "vm now time in us: {}, vm start time in us: {}",
          CommonParameter.getInstance().getMinTimeRatio(),
          CommonParameter.getInstance().getMaxTimeRatio(),
          getVmShouldEndInUs(), vmNowInUs, getVmStartInUs());
      throw Exception.notEnoughTime(opName);
    }
```

**File:** framework/src/main/java/org/tron/core/services/http/RateLimiterServlet.java (L59-80)
```java
  @PostConstruct
  private void addRateContainer() {
    final String name = getClass().getSimpleName();
    RateLimiterInitialization.HttpRateLimiterItem item = Args.getInstance()
        .getRateLimiterInitialization().getHttpMap().get(name);

    String cName;
    String params;
    if (item == null) {
      cName = DEFAULT_ADAPTER_NAME;
      params = QpsStrategy.DEFAULT_QPS_PARAM;
    } else {
      cName = item.getStrategy();
      params = item.getParams();
    }

    try {
      container.add(KEY_PREFIX_HTTP, name, buildAdapter(cName, params, name));
    } catch (Exception e) {
      throw rateLimiterInitError(cName, params, name, e);
    }
  }
```

**File:** common/src/main/resources/reference.conf (L446-483)
```text
  # Disabled API list (works for http, rpc and pbft, not jsonrpc). Case insensitive.
  disabledApi = [
    # "getaccount",
    # "getnowblock2"
  ]
}

## Rate limiter config
rate.limiter = {
  # Each HTTP servlet and gRPC method can have its own rate-limit strategy.
  # Three API rate-limit strategies are available:
  #   GlobalPreemptibleAdapter – limits maximum concurrent requests globally.
  #                              paramString = "permit=N" (N = max concurrent calls)
  #   QpsRateLimiterAdapter    – limits average QPS across all callers.
  #                              paramString = "qps=N" (N may be a decimal)
  #   IPQPSRateLimiterAdapter  – limits average QPS per source IP.
  #                              paramString = "qps=N" (N may be a decimal)
  # If no strategy is configured for an endpoint, QpsRateLimiterAdapter with
  # qps=1000 is applied automatically.

  # Per-servlet HTTP rate limits. component is the servlet class simple name.
  http = [
    # {
    #   component = "GetNowBlockServlet",
    #   strategy = "GlobalPreemptibleAdapter",
    #   paramString = "permit=1"
    # },
    # {
    #   component = "GetAccountServlet",
    #   strategy = "IPQPSRateLimiterAdapter",
    #   paramString = "qps=1"
    # },
    # {
    #   component = "ListWitnessesServlet",
    #   strategy = "QpsRateLimiterAdapter",
    #   paramString = "qps=1"
    # }
  ]
```
