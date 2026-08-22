### Title
Unbounded floating-point `transferUsage` calculation in `UnDelegateResourceActuator`/`UnDelegateResourceProcessor` causes silent negative resource-usage corruption - (File: `actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java`)

### Summary
The reported Suzaku bug is a cascading-remainder calculation whose result is subtracted from a pool value without checking whether the pool can absorb it, causing an underflow. The java-tron `UnDelegateResourceContract` handling path contains the same bug-class: a `transferUsage` amount is derived from an independent, rounding-prone floating point computation, bounded only by `min()` against another independently-derived quantity — never against the actual value it is about to be subtracted from (`receiverCapsule.getNetUsage()` / `getEnergyUsage()`). Because Java `long` arithmetic does not revert on underflow (unlike Solidity ≥0.8), the subtraction silently produces and persists a negative resource-usage value into consensus state.

### Finding Description
In `UnDelegateResourceActuator.execute()`: [1](#0-0) 

`transferUsage` is computed as:
```
transferUsage = (long) (receiverCapsule.getNetUsage()
    * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
transferUsage = min(unDelegateMaxUsage, transferUsage);
```
and then:
```
long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
receiverCapsule.setNetUsage(newNetUsage);
```

`transferUsage` is bounded only against `unDelegateMaxUsage`, a value derived independently from global `totalNetLimit`/`totalNetWeight` (unrelated to the receiver's own accumulated usage). Nothing bounds `transferUsage` to `receiverCapsule.getNetUsage()` itself. Because the computation uses `double` division and multiplication (`(double) unDelegateBalance / receiverCapsule.getAllFrozenBalanceForBandwidth()`), floating point rounding can cause the computed `transferUsage` to exceed the receiver's real `netUsage`, especially when ratios are close to 1 or values are small. When that happens, `receiverCapsule.getNetUsage() - transferUsage` goes negative and is written directly via `setNetUsage()` with no floor check — identical in shape to the reported Solidity bug, except that instead of reverting (DoS), java-tron's plain `long` subtraction silently corrupts the persisted account state.

The identical pattern also exists in the TVM-reachable native contract implementation, meaning it is triggerable both by a normal broadcast transaction and by any smart contract calling the `unDelegateResource` precompiled/native opcode: [2](#0-1) 

The identical energy-side code path exists as well: [3](#0-2) 

The negative usage value is not just cosmetic — it feeds into subsequent resource accounting arithmetic (`increase`/`recover`, window-size math) in `ResourceProcessor`, which multiplies/divides using `lastUsage`: [4](#0-3) 

A negative `lastUsage` skews `averageLastUsage`/`newUsage` calculations in ways the resource-limiting logic was never designed to handle, effectively granting the account artificially inflated available bandwidth/energy in subsequent calculations (e.g. `getV2NetUsage`/`getV2EnergyUsage`/`calcCanDelegatedBandWidthMaxSize` subtract usage from frozen balance and clamp only the final result to zero, not the intermediate negative usage): [5](#0-4) 

### Impact Explanation
This is deterministically reproducible by any account executing a normal, unprivileged `UnDelegateResourceContract` transaction (or any smart contract invoking the equivalent native/TVM opcode), so it is reachable from a plain broadcast transaction. The negative `net_usage`/`energy_usage` value is committed to the account's persisted protobuf state and replicated identically by all full nodes (deterministic floating point arithmetic), so it does not cause consensus divergence between honest nodes, but it corrupts the resource-accounting invariant that usage must be non-negative. This can be leveraged to obtain effectively unlimited/negative bandwidth or energy usage headroom for the affected receiver account in later resource checks, i.e. a resource-accounting corruption / potential free-resource-consumption bug, rather than a hard DoS revert as in the Solidity report.

### Likelihood Explanation
Likelihood increases when:
- The receiver's real usage-to-frozen-balance ratio is close to the un-delegated fraction, so floating point rounding tips `transferUsage` slightly above `netUsage`/`energyUsage`.
- Small `netUsage`/`energyUsage` values combined with imprecise `double` division amplify the relative rounding error.
- An attacker fully controls both the owner and receiver accounts (e.g., self-delegation setups) and can engineer the exact frozen-balance/usage ratios needed to trigger the rounding overshoot, similar to how the original report's PoC manufactured specific numeric ratios to force the underflow.

### Recommendation
Clamp `transferUsage` to `min(transferUsage, receiverCapsule.getNetUsage())` (respectively `getEnergyUsage()`) immediately before the subtraction in both `UnDelegateResourceActuator.execute()` and `UnDelegateResourceProcessor.execute()`, in both the BANDWIDTH and ENERGY branches. Alternatively, compute `newNetUsage = max(0, receiverCapsule.getNetUsage() - transferUsage)` before calling `setNetUsage()`, matching the defensive `max(0, …)` pattern already used elsewhere in `FreezeV2Util` and `TransactionTrace`.

### Proof of Concept
1. Set up an owner account that delegates BANDWIDTH to a receiver account such that `receiverCapsule.getAllFrozenBalanceForBandwidth()` and `unDelegateBalance` produce a ratio whose floating point `(double) unDelegateBalance / allFrozenBalance` rounds up enough that `receiverCapsule.getNetUsage() * ratio` (computed as `double`, then truncated to `long`) exceeds the receiver's true proportional share of `netUsage`.
2. Ensure `unDelegateMaxUsage` (computed from global `totalNetLimit`/`totalNetWeight`) is not the binding minimum, so `transferUsage` retains the inflated, rounding-skewed value.
3. Broadcast `UnDelegateResourceContract` (or trigger the TVM `unDelegateResource` opcode) for that amount.
4. Observe that `newNetUsage = receiverCapsule.getNetUsage() - transferUsage` is negative and gets persisted via `accountStore.put(...)`, corrupting the account's bandwidth-usage accounting for later resource-limit computations.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L79-92)
```java
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalNetLimit()) / dynamicStore.getTotalNetWeight()));
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
          receiverCapsule.setLatestConsumeTime(now);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L102-116)
```java
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * ((double) (dynamicStore.getTotalEnergyCurrentLimit()) / dynamicStore.getTotalEnergyWeight()));
            transferUsage = (long) (receiverCapsule.getEnergyUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForEnergy()));
            transferUsage = min(unDelegateMaxUsage, transferUsage);

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForEnergy(-unDelegateBalance);
          }

          long newEnergyUsage = receiverCapsule.getEnergyUsage() - transferUsage;
          receiverCapsule.setEnergyUsage(newEnergyUsage);
          receiverCapsule.setLatestConsumeTimeForEnergy(now);
          break;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L114-127)
```java
          } else {
            // calculate usage
            long unDelegateMaxUsage = (long) ((double) unDelegateBalance / TRX_PRECISION
                * dynamicStore.getTotalNetLimit() / repo.getTotalNetWeight());
            transferUsage = (long) (receiverCapsule.getNetUsage()
                * ((double) (unDelegateBalance) / receiverCapsule.getAllFrozenBalanceForBandwidth()));
            transferUsage = min(unDelegateMaxUsage, transferUsage, VMConfig.disableJavaLangMath());

            receiverCapsule.addAcquiredDelegatedFrozenV2BalanceForBandwidth(-unDelegateBalance);
          }

          long newNetUsage = receiverCapsule.getNetUsage() - transferUsage;
          receiverCapsule.setNetUsage(newNetUsage);
          receiverCapsule.setLatestConsumeTime(now);
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L86-105)
```java
  public long increase(AccountCapsule accountCapsule, ResourceCode resourceCode,
      long lastUsage, long usage, long lastTime, long now) {
    if (dynamicPropertiesStore.supportAllowCancelAllUnfreezeV2()) {
      return increaseV2(accountCapsule, resourceCode, lastUsage, usage, lastTime, now);
    }
    long oldWindowSize = accountCapsule.getWindowSize(resourceCode);
    long averageLastUsage;
    long averageUsage;
    if (hardenCalculation()) {
      BigInteger biPrecision = BigInteger.valueOf(this.precision);
      averageLastUsage = divideCeilExact(
          BigInteger.valueOf(lastUsage).multiply(biPrecision),
          BigInteger.valueOf(oldWindowSize));
      averageUsage = divideCeilExact(
          BigInteger.valueOf(usage).multiply(biPrecision),
          BigInteger.valueOf(this.windowSize));
    } else {
      averageLastUsage = divideCeil(lastUsage * this.precision, oldWindowSize);
      averageUsage = divideCeil(usage * this.precision, this.windowSize);
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L245-261)
```java
  public static long getV2NetUsage(AccountCapsule ownerCapsule, long netUsage, boolean
      disableJavaLangMath) {
    long v2NetUsage= netUsage
        - ownerCapsule.getFrozenBalance()
        - ownerCapsule.getAcquiredDelegatedFrozenBalanceForBandwidth()
        - ownerCapsule.getAcquiredDelegatedFrozenV2BalanceForBandwidth();
    return max(0, v2NetUsage, disableJavaLangMath);
  }

  public static long getV2EnergyUsage(AccountCapsule ownerCapsule, long energyUsage, boolean
      disableJavaLangMath) {
    long v2EnergyUsage= energyUsage
          - ownerCapsule.getEnergyFrozenBalance()
          - ownerCapsule.getAcquiredDelegatedFrozenBalanceForEnergy()
          - ownerCapsule.getAcquiredDelegatedFrozenV2BalanceForEnergy();
    return max(0, v2EnergyUsage, disableJavaLangMath);
  }
```
