No vulnerability found for this question.

**Reasoning:** The premise conflates two distinct config flags. `FreezeV2Util.getV2NetUsage`/`getV2EnergyUsage` and `queryAvailableUnfreezeV2Size` are gated only by `VMConfig.disableJavaLangMath()`, which is sourced from `DynamicPropertiesStore.getConsensusLogicOptimization()` [1](#0-0)  — this is a *different* flag from `allowHardenResourceCalculation`, which is loaded separately and not referenced anywhere in `FreezeV2Util.java` [2](#0-1) .

More importantly, `disableJavaLangMath()` does not change any formula — it only selects between `java.lang.Math` and `StrictMath` implementations via `Maths.max`/`Maths.min`/`Maths.addExact` [3](#0-2) . Both `MathWrapper` and `StrictMathWrapper` delegate `max`/`min`/`addExact`/`subtractExact` to `Math.*` and `StrictMath.*` respectively [4](#0-3) [5](#0-4) . For exact integer arithmetic operations like `max`, `min`, and `addExact`, `java.lang.Math` and `StrictMath` are specified to produce bit-identical results on all platforms — the Math/StrictMath divergence only applies to floating-point transcendental functions (e.g. `pow`, `sin`) with platform-dependent rounding, not to integer min/max/exact-arithmetic. Consequently, `getV2NetUsage(account, usage, true)` and `getV2NetUsage(account, usage, false)` are guaranteed to return identical values for any input — there is no "pre-flip vs post-flip formula" divergence to exploit.

Since the query path (`FreezeV2Util.queryDelegatableResource`/`queryAvailableUnfreezeV2Size`, called via `PrecompiledContracts.ResourceUsage`/related precompiles) [6](#0-5)  and the actuator/processor path (`DelegateResourceActuator.validate`, `UnDelegateResourceProcessor`) [7](#0-6)  compute the exact same numeric result regardless of the flag's boolean value at either point in time, the described cross-block stale-query exploit has no computational basis. There is no value-conservation violation possible from this mechanism.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java (L48-48)
```java
        snapshot.disableJavaLangMath = ds.getConsensusLogicOptimization() == 1;
```

**File:** actuator/src/main/java/org/tron/core/vm/config/ConfigLoader.java (L52-52)
```java
        snapshot.allowHardenResourceCalculation = ds.getAllowHardenResourceCalculation() == 1;
```

**File:** common/src/main/java/org/tron/common/math/Maths.java (L52-66)
```java
  public static int min(int a, int b, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.min(a, b) : MathWrapper.min(a, b);
  }

  public static long min(long a, long b, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.min(a, b) : MathWrapper.min(a, b);
  }

  public static int max(int a, int b, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.max(a, b) : MathWrapper.max(a, b);
  }

  public static long max(long a, long b, boolean useStrictMath) {
    return useStrictMath ? StrictMathWrapper.max(a, b) : MathWrapper.max(a, b);
  }
```

**File:** platform/src/main/java/x86/org/tron/common/math/MathWrapper.java (L39-53)
```java
  public static int min(int a, int b) {
    return Math.min(a, b);
  }

  public static long min(long a, long b) {
    return Math.min(a, b);
  }

  public static int max(int a, int b) {
    return Math.max(a, b);
  }

  public static long max(long a, long b) {
    return Math.max(a, b);
  }
```

**File:** common/src/main/java/org/tron/common/math/StrictMathWrapper.java (L66-89)
```java
  public static int min(int a, int b) {
    return StrictMath.min(a, b);
  }

  /**
   * finally calls {@link java.lang.Math#min(long, long)}
   */
  public static long min(long a, long b) {
    return StrictMath.min(a, b);
  }

  /**
   * finally calls {@link java.lang.Math#max(int, int)}
   */
  public static int max(int a, int b) {
    return StrictMath.max(a, b);
  }

  /**
   * finally calls {@link java.lang.Math#max(long, long)}
   */
  public static long max(long a, long b) {
    return StrictMath.max(a, b);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/utils/FreezeV2Util.java (L142-169)
```java
  public static long queryDelegatableResource(byte[] address, long type, Repository repository) {
    if (!VMConfig.allowTvmFreezeV2()) {
      return 0L;
    }

    AccountCapsule accountCapsule = repository.getAccount(address);
    if (accountCapsule == null) {
      return 0L;
    }

    if (type == 0) {
      // self frozenV2 resource
      long frozenV2Resource = accountCapsule.getFrozenV2BalanceForBandwidth();

      // total Usage.
      Pair<Long, Long> usagePair =
          repository.getAccountNetUsageBalanceAndRestoreSeconds(accountCapsule);
      if (usagePair == null || usagePair.getLeft() == null) {
        return frozenV2Resource;
      }

      long usage = usagePair.getLeft();
      if (usage <= 0) {
        return frozenV2Resource;
      }

      long v2NetUsage = getV2NetUsage(accountCapsule, usage, VMConfig.disableJavaLangMath());
      return max(0L, frozenV2Resource - v2NetUsage, VMConfig.disableJavaLangMath());
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L162-169)
```java
        long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
              "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
```
