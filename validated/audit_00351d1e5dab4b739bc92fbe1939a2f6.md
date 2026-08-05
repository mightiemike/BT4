### Title
Critical resource-accounting invariant guarded only by Java `assert`, which is a no-op unless the JVM is started with `-ea` - ([File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java])

### Summary
The external report flags Solidity code that guards state that "can be false" (rate/balance derived from parameters) with `assert()` instead of `require()`. `assert()` is meant only for invariants that can *never* be false; using it for externally-influenced conditions is a "wrong guard function" bug because failure semantics differ from `require()` and the check can be effectively unreliable. The same anti-pattern class is present in java-tron's own resource-accounting code, but in Java the consequences are worse: Java's `assert` statement is stripped of runtime effect by default (JVM assertions are disabled unless the process is started with `-ea`), so unless java-tron's launch scripts explicitly enable assertions, these `assert` statements are complete no-ops in production, not merely "expensive" checks.

### Finding Description
In `EnergyProcessor.calculateGlobalEnergyLimit`, the invariant that `totalEnergyWeight > 0` before it is used as a divisor is enforced only via an `assert`: [1](#0-0) 

```java
long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
  return 0;
} else {
  assert totalEnergyWeight > 0;
}
...
long energyWeight = frozeBalance / TRX_PRECISION;
return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```

Note the explicit `<= 0` short-circuit only applies when `allowNewReward()` is true; when that flag is false, a non-positive `totalEnergyWeight` falls through to the `assert`, which — because JVM assertions are disabled by default — performs no check at all in a standard production deployment. Execution then proceeds either to `calculateGlobalLimitV1` (a `BigInteger` divide by `totalEnergyWeight`, which throws `ArithmeticException`/`ArithmeticException` for divide-by-zero) or to the legacy double-arithmetic path, which silently produces `Infinity`/`NaN` cast to `long` instead of failing safely.

A structurally identical issue exists in `ResourceProcessor.increase`, where the ordering invariant `now > lastTime` is likewise enforced only by `assert`: [2](#0-1) 

This is exactly the class of bug the external report describes: relying on an "assert-style" guard for a condition that is actually derived from mutable/external state (dynamic properties, account freeze/unfreeze activity) rather than a true tautology, instead of an unconditional, always-enforced check (the `require()` analog would be an explicit `if (...) throw ...` or `Preconditions.checkState(...)`, which java-tron uses pervasively elsewhere, e.g. in actuator `validate()` methods).

### Impact Explanation
`calculateGlobalEnergyLimit` is invoked from `useEnergy`, which is on the hot path for every energy-consuming TVM transaction (smart-contract calls) triggered by unprivileged users. If `totalEnergyWeight` ever reaches a non-positive value while `allowNewReward()` is disabled (a reachable state depending on chain configuration/history of freezes), the no-op `assert` fails to short-circuit and the code falls into a divide-by-zero (`ArithmeticException`, uncaught here, which would propagate up through block/transaction processing) or an unguarded floating-point path yielding a corrupted energy limit. Either outcome is a state-processing/availability issue: an uncaught exception during transaction execution can halt block processing on affected nodes, and a corrupted energy-limit computation would cause divergent resource accounting between nodes running with vs. without JVM assertions enabled — a consensus-relevant divergence risk.

### Likelihood Explanation
Likelihood depends on `totalEnergyWeight` (aggregate frozen-TRX-for-energy weight) reaching zero or negative while `allowNewReward()` is off — a state that is plausible over the chain's long operational history (e.g., widespread unfreezing) and is exactly the scenario the code's own `if` branch anticipates and tries to handle for the `allowNewReward()`-enabled case, but not for the disabled case. The `assert` gives a false sense that this is covered.

### Recommendation
Replace the `assert` guards in `EnergyProcessor.calculateGlobalEnergyLimit` and `ResourceProcessor.increase` with explicit, unconditionally-enforced checks (e.g., return a safe default such as `0` for non-positive `totalEnergyWeight`, or throw a proper checked/runtime exception that is guaranteed to execute regardless of JVM assertion settings), consistent with the validation style used throughout the actuator layer (`throw new ContractValidateException(...)`, `Preconditions.checkArgument(...)`).

### Proof of Concept
Not applicable as executable PoC within this review — the flaw is demonstrated statically: with default JVM settings (assertions disabled, the standard java-tron deployment mode), `assert totalEnergyWeight > 0;` at [3](#0-2)  never executes, so any code path reaching this line with `totalEnergyWeight <= 0` and `allowNewReward() == false` proceeds directly into the subsequent division, which will throw or produce corrupted output.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L154-165)
```java
    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
    long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
    if (dynamicPropertiesStore.allowNewReward() && totalEnergyWeight <= 0) {
      return 0;
    } else {
      assert totalEnergyWeight > 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV1(frozeBalance, totalEnergyLimit, totalEnergyWeight);
    }
    long energyWeight = frozeBalance / TRX_PRECISION;
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L65-75)
```java
    if (lastTime != now) {
      assert now > lastTime;
      if (lastTime + windowSize > now) {
        long delta = now - lastTime;
        double decay = (windowSize - delta) / (double) windowSize;
        averageLastUsage = round(averageLastUsage * decay,
            this.disableJavaLangMath());
      } else {
        averageLastUsage = 0;
      }
    }
```
