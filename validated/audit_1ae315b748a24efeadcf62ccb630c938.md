### Title
Unguarded `assert` Allows `totalEnergyWeight`/`totalNetWeight` to Reach Zero, Causing `ArithmeticException` Crash or Infinite Resource Grant - ([File: chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java])

### Summary
`EnergyProcessor.calculateGlobalEnergyLimit` relies on a Java `assert` statement — which is compiled out and does nothing at runtime unless the node is started with `-ea` — to guard against a zero or negative `totalEnergyWeight` denominator. When users unfreeze all their energy-frozen balance, `totalEnergyWeight` (a value stored in `DynamicPropertiesStore` and adjustable purely by unprivileged unfreeze transactions) can be driven down to `0`. Depending on the `allowNewReward` chain-parameter state, the code either divides by zero via `BigInteger` (throwing `ArithmeticException` and crashing the request thread) or divides by zero as a `double` (silently producing near-infinite energy limits for callers), directly paralleling the "zero config values crash or corrupt the process" bug class described in the report.

### Finding Description
`EnergyProcessor.calculateGlobalEnergyLimit` reads `totalEnergyWeight` from the dynamic store and only special-cases it when `allowNewReward()` is true: [1](#0-0) 

```java
public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    ...
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
}
```

The `assert` is a no-op unless assertions are explicitly enabled on the JVM (`-ea`), which is not the default for production nodes. As a result, whenever `allowNewReward()` is `false` (or, more subtly, any state where the `if` condition doesn't trigger `return 0`) and `totalEnergyWeight` reaches `0` or a negative value, execution falls through to the arithmetic:

- **Hardened path** (`hardenCalculation()` true, i.e. `allowHardenResourceCalculation`): `calculateGlobalLimitV1` performs `BigInteger.divide(totalEnergyWeight)`, which throws `ArithmeticException: BigInteger divide by zero` — analogous to the Rust `chunks_exact(0)` panic in the report. [2](#0-1) 
- **Non-hardened path**: `(double) totalEnergyLimit / totalEnergyWeight` evaluates to `Infinity` (IEEE-754 semantics; no exception), and `energyWeight * Infinity` cast to `long` produces `Long.MAX_VALUE` — silently granting an attacker-controlled account an unbounded energy limit, an accounting-integrity failure.

`totalEnergyWeight` is adjusted purely by ordinary, unprivileged `UnfreezeBalanceContract`/`UnfreezeBalanceV2Contract` transactions via `addTotalEnergyWeight`, invoked from `UnfreezeBalanceActuator.execute`: [3](#0-2) 

```java
long weight = dynamicStore.allowNewReward() ? decrease : -unfreezeBalance / TRX_PRECISION;
switch (unfreezeBalanceContract.getResource()) {
  ...
  case ENERGY:
    dynamicStore.addTotalEnergyWeight(weight);
    break;
```

No validation actuator-side prevents `totalEnergyWeight` from being decremented to `0` (e.g., if the last remaining frozen-for-energy accounts on a private/consortium chain, or a chain where `allowNewReward` has not yet been activated, all unfreeze). The identically-structured `BandwidthProcessor.calculateGlobalNetLimit` was already hardened with an explicit `if (totalNetWeight == 0) return 0;` check in addition to the `allowNewReward` guard, showing the codebase recognizes this hazard elsewhere, but `EnergyProcessor.calculateGlobalEnergyLimit` was left relying solely on the disabled `assert`.

`calculateGlobalEnergyLimit` is reached from the unprivileged `useEnergy` path executed on every energy-consuming transaction: [4](#0-3) 

### Impact Explanation
- **Deterministic crash / DoS**: With `allowHardenResourceCalculation` enabled (the intended, forward direction for chains) and `totalEnergyWeight == 0`, any subsequent transaction from any account that still calls the legacy (non-V2 / non-`supportUnfreezeDelay`) `calculateGlobalEnergyLimit` path throws an uncaught `ArithmeticException`, aborting transaction processing on that path — a request/consensus-affecting halt condition, analogous to the reported `chunks_exact(0)` panic.
- **Accounting corruption / underpriced resource issuance**: On the non-hardened path, the same zero-weight condition yields an effectively unlimited energy allowance for the caller (`Long.MAX_VALUE`), letting a user consume energy without properly-priced burning — a concrete underpriced-public-work / resource-accounting violation.
- Both effects are reachable without any privileged role: ordinary account holders performing standard `UnfreezeBalanceContract` operations can drive the shared `totalEnergyWeight` counter to zero.

### Likelihood Explanation
Reaching `totalEnergyWeight == 0` requires collectively unfreezing all energy-frozen TRX network-wide, which is difficult on Mainnet with existing large stake, but is realistic on:
- Private/consortium/test networks (common java-tron deployment mode) with few frozen accounts,
- Any network where the `ALLOW_NEW_REWARD` proposal has not been activated (older/forked networks), since the `allowNewReward()` guard is the only thing standing between this state and the fall-through `assert`.
Given `assert` is compiled out by default, this is a genuine, non-theoretical latent bug whenever the guard condition doesn't apply.

### Recommendation
- Replace the `assert totalEnergyWeight > 0;` with a real runtime check (`if (totalEnergyWeight <= 0) { return 0; }`), independent of `allowNewReward()`, mirroring the pattern already used in `BandwidthProcessor.calculateGlobalNetLimit`/`calculateGlobalNetLimitV2`.
- Add the same explicit `totalEnergyWeight <= 0` guard before both the hardened (`BigInteger`) and legacy (`double`) divisions in `calculateGlobalEnergyLimit`.
- Add unit tests exercising `totalEnergyWeight == 0` and negative values under both `hardenCalculation()` on/off and both `allowNewReward()` states, similar to the existing `CalculateGlobalLimitHardenTest`.
- Audit all other call sites relying on assertions for safety-critical invariants, since Java assertions are disabled by default in production JVMs.

### Proof of Concept
1. Deploy/configure a java-tron node where `ALLOW_NEW_REWARD` has not been activated (`allowNewReward()` returns `false`) and `allowHardenResourceCalculation` is enabled.
2. Have the (small) set of accounts with `FrozenBalanceForEnergy` submit `UnfreezeBalanceContract` transactions until the cumulative `addTotalEnergyWeight` calls drive `DynamicPropertiesStore.getTotalEnergyWeight()` to `0`.
3. Submit any smart-contract transaction from an account with a non-zero `AllFrozenBalanceForEnergy` that calls `EnergyProcessor.useEnergy` → `calculateGlobalEnergyLimit`.
4. Observe: `calculateGlobalLimitV1` → `BigInteger.divide(BigInteger.ZERO)` throws `ArithmeticException`, aborting the transaction execution path; alternatively, with `allowHardenResourceCalculation` disabled, observe the returned energy limit become `Long.MAX_VALUE`/unbounded due to floating-point division by zero.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L102-106)
```java
  public boolean useEnergy(AccountCapsule accountCapsule, long energy, long now) {

    long energyUsage = accountCapsule.getEnergyUsage();
    long latestConsumeTime = accountCapsule.getAccountResource().getLatestConsumeTimeForEnergy();
    long energyLimit = calculateGlobalEnergyLimit(accountCapsule);
```

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L145-166)
```java
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForEnergy();
    if (dynamicPropertiesStore.supportUnfreezeDelay()) {
      return calculateGlobalEnergyLimitV2(frozeBalance);
    }
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }

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
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L350-357)
```java
  protected long calculateGlobalLimitV1(long frozeBalance,
      long totalLimit, long totalWeight) {
    long weight = frozeBalance / TRX_PRECISION;
    return BigInteger.valueOf(weight)
        .multiply(BigInteger.valueOf(totalLimit))
        .divide(BigInteger.valueOf(totalWeight))
        .longValueExact();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L243-256)
```java
    long weight = dynamicStore.allowNewReward() ? decrease : -unfreezeBalance / TRX_PRECISION;
    switch (unfreezeBalanceContract.getResource()) {
      case BANDWIDTH:
        dynamicStore
            .addTotalNetWeight(weight);
        break;
      case ENERGY:
        dynamicStore
            .addTotalEnergyWeight(weight);
        break;
      case TRON_POWER:
        dynamicStore
            .addTotalTronPowerWeight(weight);
        break;
```
