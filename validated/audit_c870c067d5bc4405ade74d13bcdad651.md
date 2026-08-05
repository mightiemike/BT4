### Title
Dynamic-Energy congestion factor can be permanently pinned low by gaming the once-per-cycle checkpoint, underpricing CALL-type opcode execution - ([File: chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java])

### Summary
TRON's TVM "Dynamic Energy" anti-DoS mechanism raises/lowers a per-contract `energyFactor` that multiplies the energy cost of `CALL`/`CALLCODE`/`DELEGATECALL`/`STATICCALL`/`CALLTOKEN` penalties [1](#0-0) . Like the `pokeOracle()`/`computeInternalMedian()` pattern in the external report, the factor is only re-evaluated **once per cycle** via `ContractStateCapsule.catchUpToCycle()`, and any call that lands after the first checkpoint of a new cycle is a no-op until the next cycle boundary [2](#0-1) . This is triggered by any unprivileged caller of the contract, since `updateContextContractFactor()` runs on every CALL-family opcode execution in the TVM [3](#0-2) .

### Finding Description
`updateContextContractFactor()` is invoked from `Program` as part of ordinary contract-call execution (not a privileged/system-only path) whenever `VMConfig.allowDynamicEnergy()` is set, so any transaction sender that calls a contract can trigger the checkpoint [4](#0-3) .

The checkpoint logic in `catchUpToCycle()` mirrors the reported oracle bug's structure exactly: it compares the recorded `updateCycle` to the current cycle, and returns `false` (does nothing) if they are equal — i.e., once the factor has been evaluated in the current cycle, it is frozen for the remainder of that cycle regardless of what happens afterward:
```java
// chainbase/.../ContractStateCapsule.java
long lastCycle = getUpdateCycle();
// Updated within this cycle
if (lastCycle == newCycle) {
  return false;
}
``` [5](#0-4) 

The factor is only ever *raised* based on `energyUsage` accumulated **in the previous cycle**, checked exactly once at the moment the cycle boundary is first observed:
```java
if (getEnergyUsage() > threshold) {
  lastCycle += 1;
  ... setEnergyFactor(min(maxFactor, ...))
}
``` [6](#0-5) 
and `energyUsage` is reset to 0 on every cycle transition (via `reset(newCycle)`/rebuild of `ContractState`) [7](#0-6) .

This produces the exact "lock-in for the whole epoch" primitive described in the report: an attacker who is the first to invoke a heavily-used attack contract right at (or just after) a cycle boundary forces `catchUpToCycle` to run while `energyUsage` for the *prior* cycle is still below `threshold` (e.g., by having spread usage so the checkpoint fires before the threshold is technically breached, or simply because usage accrual is asynchronous per calling transaction and the attacker controls transaction ordering/timing around the boundary). Once that first checkpoint of the new cycle completes, `energyFactor` is fixed for the entire cycle — every subsequent call within that cycle is a no-op per the `lastCycle == newCycle` guard, so no matter how much energy the attacker's contract subsequently consumes in that cycle, the multiplicative penalty applied by `getCalculateCallCost()` in `EnergyCost.java` cannot increase until the next cycle:
```java
if (VMConfig.allowDynamicEnergy()) {
  long factor = program.getContextContractFactor();
  if (factor > DYNAMIC_ENERGY_FACTOR_DECIMAL) {
    long penalty = energyCost * factor / DYNAMIC_ENERGY_FACTOR_DECIMAL - energyCost;
    ...
    energyCost += penalty;
  }
}
``` [1](#0-0) 

### Impact Explanation
This is an "underpriced public work" class impact: the Dynamic Energy feature exists specifically to raise the energy cost of contracts that are heavily abused (e.g., contracts exploited to spam cheap opcodes / repeated calls to consume network resources at low TRX cost). By gaming which transaction is the first to cross the cycle boundary for a given contract, an attacker can suppress the price-adjustment checkpoint for that entire cycle, letting the same contract be called at the un-penalized (or under-penalized) `energyFactor` for the full cycle duration while still generating the same volume of resource-consuming calls that the mechanism was designed to throttle. This degrades the anti-DoS/congestion-pricing guarantee that Dynamic Energy is meant to provide across a full cycle window, rather than merely a single block.

### Likelihood Explanation
The trigger path (`updateContextContractFactor` called from ordinary `CALL`-type opcode execution) is reachable by any unprivileged account issuing a `TriggerSmartContract` transaction; no special permissions are required [4](#0-3) . The `lastCycle == newCycle` short-circuit is unconditional and requires no special preconditions beyond being first to call the contract in the new cycle, which is influenceable by transaction submission timing (analogous to "front-running the epoch boundary" in the source report).

### Recommendation
Do not gate the checkpoint purely on a coarse `updateCycle == currentCycle` boolean that freezes state for an entire cycle after the first observation. Instead, either (a) allow the factor to be recomputed additional times within the same cycle if `energyUsage` crosses the threshold intra-cycle (mirroring the report's suggestion to update continuously as time/usage progresses rather than lock for a whole period), or (b) base the increase decision on a monotonic usage counter sampled deterministically at the maintenance/cycle-change block itself (a privileged, protocol-controlled trigger) rather than lazily on the first user transaction that happens to land in the new cycle.

### Proof of Concept
Conceptual PoC (would need to be validated in a running java-tron testnet with `allowDynamicEnergy` enabled):
1. Deploy a contract that performs many cheap internal `CALL`s to itself/others, driving up `energyUsage` tracked in its `ContractStateCapsule`.
2. Near a cycle boundary (`DynamicPropertiesStore.getCurrentCycleNumber()` transition), submit a transaction that calls the contract with minimal usage so that the very first `catchUpToCycle()` invocation in the new cycle observes `energyUsage` from the prior cycle below `DynamicEnergyThreshold`, leaving `energyFactor` unchanged/low for the new cycle (see `ContractStateCapsuleTest.testCatchUpCycle` for exact threshold/factor arithmetic) [8](#0-7) .
3. Immediately after, flood the same contract with high-volume calls within the same cycle; because `lastCycle == newCycle` short-circuits all further `catchUpToCycle` calls, `energyFactor` cannot be raised again until the next cycle, so the flood is charged at the suppressed factor for the remainder of the cycle.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/EnergyCost.java (L513-523)
```java
    if (VMConfig.allowDynamicEnergy()) {
      long factor = program.getContextContractFactor();
      if (factor > DYNAMIC_ENERGY_FACTOR_DECIMAL) {
        long penalty = energyCost * factor / DYNAMIC_ENERGY_FACTOR_DECIMAL - energyCost;
        if (penalty < 0) {
          penalty = 0;
        }
        program.setCallPenaltyEnergy(penalty);
        energyCost += penalty;
      }
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L89-98)
```java
  public boolean catchUpToCycle(
      long newCycle, long threshold, long increaseFactor, long maxFactor,
      boolean useStrictMath, boolean disableMath
  ) {
    long lastCycle = getUpdateCycle();

    // Updated within this cycle
    if (lastCycle == newCycle) {
      return false;
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L106-120)
```java
    final long precisionFactor = DYNAMIC_ENERGY_FACTOR_DECIMAL;

    // Increase the last cycle
    // fix the threshold = 0 caused incompatible
    if (getEnergyUsage() > threshold) {
      lastCycle += 1;
      double increasePercent = 1 + (double) increaseFactor / precisionFactor;
      this.contractState = ContractState.newBuilder()
          .setUpdateCycle(lastCycle)
          .setEnergyFactor(min(
              maxFactor,
              (long) ((getEnergyFactor() + precisionFactor) * increasePercent) - precisionFactor,
              disableMath))
          .build();
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L149-153)
```java
  public void reset(long latestCycle) {
    this.contractState = ContractState.newBuilder()
        .setUpdateCycle(latestCycle)
        .build();
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2360-2383)
```java
  public long updateContextContractFactor() {
    ContractStateCapsule contractStateCapsule =
        contractState.getContractState(getContextAddress());

    if (contractStateCapsule == null) {
      contractStateCapsule = new ContractStateCapsule(
          contractState.getDynamicPropertiesStore().getCurrentCycleNumber());
      contractState.updateContractState(getContextAddress(), contractStateCapsule);
    } else {
      if (contractStateCapsule.catchUpToCycle(
          contractState.getDynamicPropertiesStore().getCurrentCycleNumber(),
          VMConfig.getDynamicEnergyThreshold(),
          VMConfig.getDynamicEnergyIncreaseFactor(),
          VMConfig.getDynamicEnergyMaxFactor(),
          VMConfig.allowStrictMath(),
          VMConfig.disableJavaLangMath())) {
        contractState.updateContractState(getContextAddress(), contractStateCapsule
        );
      }
    }
    contextContractFactor = contractStateCapsule.getEnergyFactor()
        + Constant.DYNAMIC_ENERGY_FACTOR_DECIMAL;
    return contextContractFactor;
  }
```

**File:** framework/src/test/java/org/tron/core/capsule/ContractStateCapsuleTest.java (L14-31)
```java
  @Test
  public void testCatchUpCycle() {
    ContractStateCapsule capsule = new ContractStateCapsule(
        SmartContractOuterClass.ContractState.newBuilder()
            .setEnergyUsage(1_000_000L)
            .setEnergyFactor(5000L)
            .setUpdateCycle(1000L)
            .build());

    Assert.assertFalse(capsule.catchUpToCycle(1000L, 2_000_000L, 2000L, 10_00L, false, false));
    Assert.assertEquals(1000L, capsule.getUpdateCycle());
    Assert.assertEquals(1_000_000L, capsule.getEnergyUsage());
    Assert.assertEquals(5000L, capsule.getEnergyFactor());

    Assert.assertTrue(capsule.catchUpToCycle(1010L, 900_000L, 1000L, 10_000L, false, false));
    Assert.assertEquals(1010L, capsule.getUpdateCycle());
    Assert.assertEquals(0L, capsule.getEnergyUsage());
    Assert.assertEquals(3137L, capsule.getEnergyFactor());
```
