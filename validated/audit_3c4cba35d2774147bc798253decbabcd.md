### Title
Rounding down in dynamic-energy penalty calculation lets callers under-pay the congestion penalty (`EnergyCost.java`, `VM.java`)

### Summary
When the TVM dynamic-energy-price feature is active (`VMConfig.allowDynamicEnergy()`), the extra energy "penalty" charged to a caller invoking a congested/flagged contract is computed with integer division that truncates (rounds down) toward the caller instead of rounding up in favor of the protocol/network. This mirrors the reported `VaultV2.forceDeallocate` bug class: a `mulDiv`-style computation that should always round in favor of the party being protected (here, correct energy metering/throttling) instead rounds in favor of the party being charged (the caller), letting the intended penalty be silently reduced or fully escape to zero for small per-opcode energy costs.

### Finding Description
The dynamic-energy penalty is computed identically in two places using plain integer (floor) division: [1](#0-0) 

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

and, for the general (non-CALL) opcode path, in the main interpreter loop: [2](#0-1) 

```java
if (factor > DYNAMIC_ENERGY_FACTOR_DECIMAL) {
  long penalty;
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
```

Both formulas compute `penalty = energyCost * factor / DECIMAL - energyCost`, a truncating division that always rounds toward zero. This is the exact analog of the report's `assets[i].mulDivDown(...)`: a security/metering control (the extra energy charge meant to throttle usage of a "hot"/congested contract, signaled by `factor > DYNAMIC_ENERGY_FACTOR_DECIMAL`) is computed by rounding down in favor of the party being penalized (the transaction sender) rather than rounding up in favor of the protocol invariant that "extra energy usage on flagged contracts must always be charged."

For any individual opcode with a small base `energyCost` (many TVM opcodes cost as little as 1–3 gas, see `Op`/`EnergyCost` base costs) and a `factor` only slightly above `DYNAMIC_ENERGY_FACTOR_DECIMAL`, `energyCost * factor / DECIMAL` truncates back down to `energyCost`, so `penalty` evaluates to exactly `0`, even though a fractional penalty was mathematically owed. Because the loop in `VM.play` (see `actuator/src/main/java/org/tron/core/vm/VM.java`) recomputes and re-applies this rounding on every single opcode execution, an attacker can structure a contract call to consist of many low-cost opcodes rather than fewer high-cost ones, causing the fractional penalty to be truncated away on every opcode instead of being charged once on an aggregate (higher) cost where it would not round to zero.

### Impact Explanation
This is a resource/energy metering integrity issue reachable from any ordinary `TriggerSmartContract` broadcast transaction once the dynamic energy feature is enabled by the committee (`VMConfig.allowDynamicEnergy()`). The dynamic-energy mechanism exists specifically to increase the cost of interacting with flagged/congested contracts and thereby discourage abuse/spam of hot contracts. Systematic under-charging of the penalty:
- Weakens the throttling/anti-abuse control the mechanism was designed to enforce, allowing cheaper-than-intended repeated calls into flagged contracts.
- Leaks value from network energy accounting (`ProgramResult`/`TransactionTrace` energy-penalty totals, see `setPenalty` in `chainbase/src/main/java/org/tron/core/db/TransactionTrace.java:163-168`), meaning callers pay less TRX in energy fees than the protocol intends, which is directly analogous to the paying-less-than-intended-penalty impact described in the report.
- Enables cheaper-than-designed congestion of high-demand contracts, an economic DoS vector against the intended throttling design.

Given the same rounding bug is duplicated in two call sites (`EnergyCost.getCalculateCallCost` and `VM.play`), the effect compounds over every opcode of a transaction, not just once.

### Likelihood Explanation
Likelihood is dependent on the dynamic-energy feature (`allowDynamicEnergy`) being enabled on the network via committee proposal, which is a normal, documented TVM configuration path (not a privileged/leaked-key attack). Once enabled, any unprivileged account can trigger this by simply calling a contract whose `contextContractFactor` exceeds `DYNAMIC_ENERGY_FACTOR_DECIMAL`; no special permissions, malicious peers, or off-chain conditions are required — only ordinary transaction broadcast.

### Recommendation
Round the penalty up (ceiling division) rather than down, so the protocol never under-collects the intended congestion/energy penalty:
```java
long penalty = -Math.floorDiv(-(energyCost * factor), DYNAMIC_ENERGY_FACTOR_DECIMAL) - energyCost;
```
or equivalently use a ceiling-division helper (the codebase already has `divideCeil`/`divideCeilExact` patterns elsewhere, e.g. `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java:272-283`) consistently in both `EnergyCost.getCalculateCallCost` and `VM.play`, and ensure the two computations stay in sync to avoid future drift.

### Proof of Concept
1. Enable dynamic energy pricing (`allowDynamicEnergy` = true) and have a contract's `contextContractFactor` be set just above `DYNAMIC_ENERGY_FACTOR_DECIMAL` (e.g., `DECIMAL + 1`, out of `DECIMAL = 10000` as typically scaled).
2. Deploy/call a contract composed of many opcodes with small individual `energyCost` (e.g., `PUSH1`, `ADD`, cost ~3 each).
3. For each opcode: `penalty = energyCost * (DECIMAL+1) / DECIMAL - energyCost = floor(energyCost * (DECIMAL+1)/DECIMAL) - energyCost`. For small `energyCost` (e.g., 3) and `DECIMAL=10000`, `energyCost*(DECIMAL+1)/DECIMAL` truncates to `energyCost`, so `penalty = 0` on every single opcode.
4. Compare total energy actually billed (`receipt.getEnergyPenaltyTotal()` via `TransactionTrace.setPenalty`) against the fractional penalty that should have accrued if computed on the aggregate energy usage for the transaction — the actual billed penalty will be smaller (potentially zero), demonstrating the value leak versus the intended congestion-pricing invariant.

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

**File:** actuator/src/main/java/org/tron/core/vm/VM.java (L62-79)
```java
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
```
