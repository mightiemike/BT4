### Title
Dynamic Energy anti-abuse throttle can be evaded by straddling the maintenance-cycle boundary - (File: `chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java`)

### Summary
The TVM's "Dynamic Energy" penalty mechanism (`allowDynamicEnergy`), designed to raise the gas cost of calling contracts that consume unusually high energy, keys its threshold check to a discrete `cycle` number rather than to a continuous usage window. This is structurally the same bug class as the Oracle's "two-day low": a defense meant to require *sustained* bad behavior across a period can instead be triggered/evaded using activity concentrated at the boundary between two adjacent periods.

### Finding Description
`ContractStateCapsule.catchUpToCycle` accumulates a contract's per-cycle energy usage and only evaluates it against `threshold` when the current cycle advances to a new one: [1](#0-0) 

The relevant logic:
- If `lastCycle == newCycle`, no evaluation occurs at all — usage keeps accumulating silently within the current cycle.
- Only at the moment the cycle number changes does the code check `getEnergyUsage() > threshold` and bump the penalty `energyFactor`. [2](#0-1) 

This state is read and updated once per call/transaction via `Program.updateContextContractFactor()`, and the resulting factor is applied as a multiplicative energy penalty on subsequent opcode/CALL costs within `VM.play` and `EnergyCost.getCalculateCallCost`: [3](#0-2) [4](#0-3) 

Because the check happens only at cycle transitions (bucketed by `DynamicPropertiesStore.getCurrentCycleNumber()`, which advances on a fixed schedule tied to the network's maintenance interval), an attacker can split a burst of high-energy calls so that part of it lands in cycle `N` (just before the boundary) and the rest lands in cycle `N+1` (just after the boundary). Each half may stay under `threshold` individually, so `catchUpToCycle` never observes a single cycle whose accumulated usage exceeds the threshold, even though the two bursts are separated by only a brief real-time window straddling the cycle boundary. This mirrors the Oracle bug exactly: a period-based defense is bypassed by keeping each discrete "bucket" just under the limit while concentrating all real activity at the seam between buckets.

### Impact Explanation
If evaded, the dynamic energy factor (`energyFactor`) never rises, so the contract never incurs the intended energy-cost multiplier for repeated high-usage invocation. This defeats a resource-abuse mitigation that TRON introduced specifically to make sustained/expensive contract-call patterns costlier, undermining the intended anti-spam/anti-abuse economic throttle on TVM execution and energy metering.

### Likelihood Explanation
Exploitation requires only ordinary, unprivileged smart-contract calls timed relative to `getCurrentCycleNumber()` transitions — reachable by any user submitting a broadcast transaction/contract call, with no special privileges needed. The main constraint is that the cycle length (tied to the maintenance interval, default 6 hours per `config.conf`'s `maintenanceTimeInterval = 21600000`) is much larger than a single block, so this is a timing-boundary game across a coarse period rather than "just 2 blocks" as in the original Oracle report; nonetheless the underlying design flaw (checking accumulated state only at bucket transitions rather than over a rolling window) is the same root cause.

### Recommendation
Replace the discrete per-cycle threshold check with a rolling/decaying window (similar to the resource-usage averaging already used elsewhere, e.g. `ResourceProcessor.increase`), or evaluate accumulated usage against the threshold continuously (e.g., on every call) instead of only at cycle-number transitions, so that usage concentrated around a cycle boundary cannot escape detection.

### Proof of Concept
Not independently reproducible from static analysis alone; the mechanism was inferred from `ContractStateCapsule.catchUpToCycle` and its call sites. I could not fully confirm the exact update frequency/granularity of `DynamicPropertiesStore.getCurrentCycleNumber()` (i.e., whether it increments once per maintenance cycle or some finer-grained schedule) within the available tool budget — this would need to be verified in the full source (e.g., via `MaintenanceManager`/`IncentiveManager`) before treating this as conclusively exploitable at scale. Given the indexing limits mentioned, a full code review in a Devin session would be needed to confirm cycle-length and construct a concrete PoC transaction sequence.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L89-120)
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

    // Guard judge and uninitialized state
    if (lastCycle > newCycle || lastCycle == 0L) {
      reset(newCycle);
      return true;
    }

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

**File:** actuator/src/main/java/org/tron/core/vm/VM.java (L23-83)
```java
    try {
      long factor = DYNAMIC_ENERGY_FACTOR_DECIMAL;
      long energyUsage = 0L;
      // hoist once per execution: avoids a per-opcode VMConfig.current() thread-local lookup
      final boolean allowDynamicEnergy = VMConfig.allowDynamicEnergy();

      if (allowDynamicEnergy) {
        factor = program.updateContextContractFactor();
      }

      while (!program.isStopped()) {
        if (VMConfig.vmTrace()) {
          program.saveOpTrace();
        }

        try {
          Operation op = jumpTable.get(program.getCurrentOpIntValue());
          if (!op.isEnabled()) {
            throw Program.Exception.invalidOpCode(program.getCurrentOp());
          }
          program.setLastOp((byte) op.getOpcode());

          /* stack underflow/overflow check */
          program.verifyStackSize(op.getRequire());
          program.verifyStackOverflow(op.getRequire(), op.getRet());

          String opName = Op.getNameOf(op.getOpcode());
          /* spend energy before execution */
          long energy = op.getEnergyCost(program);
          if (allowDynamicEnergy) {
            long actualEnergy = energy;
            // CALL Ops have special calculation on energy.
            if (CALL_OPS.contains(op.getOpcode())) {
              actualEnergy = energy
                  - program.getAdjustedCallEnergy().longValueSafe()
                  - program.getCallPenaltyEnergy();
            }
            energyUsage += actualEnergy;

            if (factor > DYNAMIC_ENERGY_FACTOR_DECIMAL) {
              long penalty;

              // CALL Ops have special calculation on energy.
              if (CALL_OPS.contains(op.getOpcode())) {
                penalty = program.getCallPenaltyEnergy();
              } else {
                penalty = energy * factor / DYNAMIC_ENERGY_FACTOR_DECIMAL - energy;
                if (penalty < 0) {
                  penalty = 0;
                }
                energy += penalty;
              }

              program.spendEnergyWithPenalty(energy, penalty, opName);
            } else {
              program.spendEnergy(energy, opName);
            }

          } else {
            program.spendEnergy(energy, opName);
          }
```
