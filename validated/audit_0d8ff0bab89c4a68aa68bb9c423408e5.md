### Title
Silent `long` overflow in bandwidth/energy usage-window accounting corrupts resource metering - (File: `chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java`)

### Summary
The Astaria `afterPayment` slope overflow (`unchecked { slope += ... }`) is a case where an accounting value that accumulates attacker-influenced inputs (loan amount × interest rate) is added with unchecked arithmetic and silently wraps, corrupting protocol-wide state (`totalAssets`, `slope`). The closest reachable analog in java-tron is the legacy (non-"hardened") resource-usage window math in `ResourceProcessor`/`RepositoryImpl`, which multiplies attacker-influenced usage/balance values by a fixed precision constant using raw `long` arithmetic with no overflow guard, unless the committee-gated `AllowHardenResourceCalculation` parameter has been activated.

### Finding Description
`ResourceProcessor.increase()` computes average usage and a new sliding-window size using plain `long` multiplication/division when `hardenCalculation()` is false: [1](#0-0) 

The same unguarded pattern exists in the merge/window-size helpers: [2](#0-1) [3](#0-2) 

An identical legacy path exists in the TVM-side repository implementation used by native `freeze`/`delegateresource` TVM precompiles: [4](#0-3) 

These computations multiply `usage`/`lastUsage` (resource-usage counters, denominated in "sun"-scale bandwidth/energy points influenced by staking/delegation amounts) by `precision` (1,000,000) and by `windowSize`, then divide. Because `precision` is a large multiplier, the intermediate product `usage * precision` (or `lastUsage * lastWindowSize + usage * windowSize`) can exceed `Long.MAX_VALUE` well before the underlying staked-TRX values approach the total token supply, since the multiplier itself adds ~6 orders of magnitude of headroom consumption. When this happens on the legacy path, the `long` silently wraps (two's-complement overflow), producing an incorrect (potentially negative) `averageUsage`/`newWindowSize`/`newUsage`, exactly mirroring how Astaria's `slope += uint48(...)` silently wraps and corrupts `totalAssets`.

The codebase itself acknowledges this exact bug class: a parallel "hardened" implementation using `BigInteger` and `Math.addExact`/`multiplyExact` was added specifically to fix it, gated behind the `hardenCalculation()`/`hardenResourceCalculation()` flags, and dedicated regression tests were written that explicitly rely on the *unhardened* path silently overflowing: [5](#0-4) 

This confirms the raw-`long` code path is reachable and produces silent overflow whenever the hardening committee parameter is not enabled, which is the same "unchecked block that should be removed" pattern flagged in the Astaria report.

### Impact Explanation
A silent overflow in `getUsage`/`getNewWindowSize`/`increase` corrupts an account's bandwidth/energy usage accounting (`NetUsage`, `EnergyUsage`, `WindowSize`). Depending on the sign of the wrapped value, this can:
- Make usage appear negative or anomalously small, letting an account consume resources beyond its legitimate allotment (free/duplicated bandwidth or energy — a resource-accounting corruption / effectively a DoS vector against the network's adaptive resource limiting), or
- Corrupt the delegation window size used by `DelegateResourceActuator`/`UnDelegateResourceActuator` and their TVM-precompile equivalents (`DelegateResourceProcessor`, `UnDelegateResourceProcessor`), leading to incorrect resource redistribution between owner and receiver accounts network-wide.

This is reachable from ordinary, unprivileged, broadcast transactions (`FreezeBalanceV2Contract`, `DelegateResourceContract`, `UnDelegateResourceContract`) and from TVM contract calls exercising the native freeze/delegate/undelegate precompiles, satisfying the "resource and reward accounting" / "actuator state transitions" analog categories.

### Likelihood Explanation
Exploitability depends on whether the account's usage/window values can reach the magnitude required for `usage * precision` (precision = 1,000,000) to overflow a 63-bit signed long. This requires usage values on the order of ~9.2×10^12 units before multiplication, which is far above realistic bandwidth/energy point counts for a single account under normal network parameters, but the codebase's own test suite (`testIncreaseOverflowSilentWithoutHardening`, `testIncreaseOverflowDetectedWithHardening`) demonstrates the wraparound is real and was serious enough to warrant a parallel hardened implementation plus a governance-gated rollout — i.e., the java-tron team itself already treats this as a confirmed defect class, only remediated conditionally (behind `AllowHardenResourceCalculation`/`AllowHardenExchangeCalculation` committee parameters). I was unable to fully verify from the available index whether these hardening parameters default to enabled or disabled on production/mainnet, so the current real-world exposure (i.e., whether the vulnerable legacy path is actually active) is uncertain and should be confirmed by inspecting `DynamicPropertiesStore` initialization defaults and mainnet proposal history.

### Recommendation
- Retire the legacy raw-`long` arithmetic branches in `ResourceProcessor.increase/getUsage/getNewWindowSize` and `RepositoryImpl.increase/getUsage` (and the analogous `ExchangeProcessor` legacy path) entirely rather than keeping them behind an optional committee toggle; make the `BigInteger`/`*Exact` hardened path the only implementation.
- If a transition period is required, activate `AllowHardenResourceCalculation` and `AllowHardenExchangeCalculation` by default (value 1) rather than opt-in, since the unguarded path is a corruption/DoS risk once triggered.
- Add saturating or exception-raising bounds checks around all `usage * precision` / `balance * windowSize` style computations in resource- and exchange-accounting code paths, consistent with the mitigation recommended for the original Astaria `slope` overflow (remove `unchecked`, add explicit overflow checks).

### Proof of Concept
Concrete overflow trigger is already codified in the repository's own regression test, demonstrating the silent-wrap condition on the legacy (non-hardened) path: [5](#0-4) 
and the corresponding hardened-path exception confirming the same inputs overflow a `long`: [6](#0-5) 
Reaching this state on-chain requires an account (via repeated `FreezeBalanceV2`/`DelegateResource` transactions or TVM freeze/delegate precompile calls) to accumulate a `lastUsage`/`usage` value whose product with `precision` (1,000,000) exceeds `Long.MAX_VALUE`, at which point `ResourceProcessor.increase()` (non-hardened branch) silently returns a wrapped, incorrect usage value that is persisted into the account's `AccountCapsule` bandwidth/energy fields.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L102-105)
```java
    } else {
      averageLastUsage = divideCeil(lastUsage * this.precision, oldWindowSize);
      averageUsage = divideCeil(usage * this.precision, this.windowSize);
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L262-270)
```java
  private long getNewWindowSize(long lastUsage, long lastWindowSize, long usage,
      long windowSize, long newUsage) {
    if (hardenCalculation()) {
      BigInteger bi = BigInteger.valueOf(lastUsage).multiply(BigInteger.valueOf(lastWindowSize))
          .add(BigInteger.valueOf(usage).multiply(BigInteger.valueOf(windowSize)));
      return bi.divide(BigInteger.valueOf(newUsage)).longValueExact();
    }
    return (lastUsage * lastWindowSize + usage * windowSize) / newUsage;
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L285-300)
```java
  private long getUsage(long usage, long windowSize) {
    if (hardenCalculation()) {
      return BigInteger.valueOf(usage).multiply(BigInteger.valueOf(windowSize))
          .divide(BigInteger.valueOf(precision)).longValueExact();
    }
    return usage * windowSize / precision;
  }

  private long getUsage(long oldUsage, long oldWindowSize, long newUsage, long newWindowSize) {
    if (hardenCalculation()) {
      BigInteger bi = BigInteger.valueOf(oldUsage).multiply(BigInteger.valueOf(oldWindowSize))
          .add(BigInteger.valueOf(newUsage).multiply(BigInteger.valueOf(newWindowSize)));
      return bi.divide(BigInteger.valueOf(precision)).longValueExact();
    }
    return (oldUsage * oldWindowSize + newUsage * newWindowSize) / precision;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L911-924)
```java
  private long increase(long lastUsage, long usage, long lastTime, long now, long windowSize) {
    long averageLastUsage;
    long averageUsage;
    if (hardenResourceCalculation()) {
      BigInteger biPrecision = BigInteger.valueOf(precision);
      BigInteger biWindowSize = BigInteger.valueOf(windowSize);
      averageLastUsage = divideCeilExact(
          BigInteger.valueOf(lastUsage).multiply(biPrecision), biWindowSize);
      averageUsage = divideCeilExact(
          BigInteger.valueOf(usage).multiply(biPrecision), biWindowSize);
    } else {
      averageLastUsage = divideCeil(lastUsage * precision, windowSize);
      averageUsage = divideCeil(usage * precision, windowSize);
    }
```

**File:** framework/src/test/java/org/tron/core/db/ResourceProcessorHardenTest.java (L106-118)
```java
  @Test
  public void testIncreaseOverflowDetectedWithHardening() {
    long lastUsage = Long.MAX_VALUE / 10; // ~9.2e17
    long usage = 1L;
    long lastTime = 9990L;
    long now = 9995L;
    long windowSize = 28800L;

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(1);

    Assert.assertThrows(ArithmeticException.class,
        () -> processor.increase(lastUsage, usage, lastTime, now, windowSize));
  }
```

**File:** framework/src/test/java/org/tron/core/db/ResourceProcessorHardenTest.java (L120-130)
```java
  @Test
  public void testIncreaseOverflowSilentWithoutHardening() {
    long lastUsage = Long.MAX_VALUE / 10;
    long usage = 1L;
    long lastTime = 9990L;
    long now = 9995L;
    long windowSize = 28800L;

    dbManager.getDynamicPropertiesStore().saveAllowHardenResourceCalculation(0);
    processor.increase(lastUsage, usage, lastTime, now, windowSize);
  }
```
