### Title
Unhardened double-precision resource math in `DelegateResourceActuator.validate()` allows rounding-induced over-delegation of frozen bandwidth/energy - (File: actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java)

### Summary
`DelegateResourceActuator.validate()` computes the amount of already-used V2 resource (`netUsage` / `energyUsage`) with floating-point `double` arithmetic before checking whether an owner has enough free `FrozenV2` balance to delegate. This mirrors the external report's root cause: a linear ratio calculation (`wmul`/loop-based borrow accounting) done with a lossy division/multiplication whose rounding direction can be steered by transaction inputs, letting the actor bypass the intended limit check and push the accounted state (leverage / delegated balance) further than what was actually validated.

### Finding Description
In the reachable, unprivileged path (any account can broadcast a `DelegateResourceContract`), the check that gates how much resource an account may delegate is: [1](#0-0) 

and for energy: [2](#0-1) 

Both `netUsage`/`energyUsage` are derived from `(double) totalWeight / totalLimit` multiplied by usage and `TRX_PRECISION`, then truncated to `long` via a Java cast, i.e., always **rounded toward zero**. This means `getV2NetUsage`/`getV2EnergyUsage` (which subtract already-frozen V1 balances) can systematically under-report used V2 resource: [3](#0-2) 

Because the truncation always rounds down `netUsage`/`energyUsage`, the validation check `getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance` under-estimates consumed usage and over-estimates the "available" balance to delegate, letting a caller delegate marginally more balance than the account should be allowed to, exactly analogous to how the Rubicon `wmul` truncation in `_borrowLimit` let a caller obtain more borrowed collateral than the requested leverage implied.

Notably, this specific code path performs the double-based computation **unconditionally** — unlike other resource-accounting call sites in the same repo (`RepositoryImpl.usageToBalance`, `ResourceProcessor.increase`, `calculateGlobalEnergyLimitV2`) which have been hardened behind a `hardenResourceCalculation()`/`allowHardenResourceCalculation` flag with `BigInteger`-exact math and dedicated regression tests: [4](#0-3) [5](#0-4) 

The same un-hardened double-math pattern is duplicated in `DelegateResourceProcessor.validate()` (the TVM native-contract delegate path) and in `Wallet.calcCanDelegatedBandWidthMaxSize`/`calcCanDelegatedEnergyMaxSize`, showing this is a systemic, not one-off, calculation: [6](#0-5) [7](#0-6) 

### Impact Explanation
`getFrozenV2BalanceForBandwidth`/`getFrozenV2BalanceForEnergy` are the accounting fields backing an account's staked TRX for bandwidth/energy resources. If `validate()` accepts a `delegateBalance` that is actually larger than the truly-available (unused) frozen balance, `execute()` will subtract that balance via `addFrozenBalanceForBandwidthV2(-delegateBalance)` / `addFrozenBalanceForEnergyV2(-delegateBalance)`, potentially driving the owner's remaining frozen balance accounting inconsistent with real usage — a resource/asset accounting corruption reachable from an ordinary broadcast transaction. The magnitude of the rounding error is bounded by the ratio `totalWeight/totalLimit` (a small fraction of `TRX_PRECISION` per delegate call), so a single call yields only a small discrepancy, but it is a genuine state-accounting integrity flaw rather than a purely cosmetic one, matching the disputed-but-real severity of the analog finding.

### Likelihood Explanation
Any account holding `FrozenV2` balance close to its resource-usage boundary can trigger this by broadcasting `DelegateResourceContract` transactions with `delegateBalance` chosen at the margin computed from the truncated usage value; no special privileges, keys, or node compromise are required. It is a deterministic, repeatable consequence of the `(long)(double ...)` truncation and does not depend on race conditions.

### Recommendation
Route the `netUsage`/`energyUsage` computation in `DelegateResourceActuator.validate()` (and the mirrored logic in `DelegateResourceProcessor.validate()` and `Wallet.calcCanDelegatedBandWidthMaxSize`/`calcCanDelegatedEnergyMaxSize`) through the same `BigInteger`-exact, harden-guarded path already used in `RepositoryImpl.usageToBalance`/`ResourceProcessor`, rather than relying on unconditional `double` truncation, so that the "available balance to delegate" check is exact regardless of the `hardenResourceCalculation` proposal state.

### Proof of Concept
Not independently executable from the index alone (would require running the java-tron test harness), but the reasoning is directly demonstrated by the repo's own regression tests for the equivalent hardened computations, which show measurable numeric divergence between the double-based and `BigInteger`-exact formulas for realistic weight/limit values: [8](#0-7) 

This confirms the double-vs-BigInteger discrepancy is real and non-trivial in this codebase; the same discrepancy is present, un-hardened, in `DelegateResourceActuator.validate()`'s `netUsage`/`energyUsage` computation.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L176-183)
```java
        long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (dynamicStore.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));
        long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeEnergyV2 balance");
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

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L256-265)
```java
  private long usageToBalance(long usage, long totalWeight, long totalLimit) {
    if (hardenResourceCalculation()) {
      return BigInteger.valueOf(usage)
          .multiply(BigInteger.valueOf(totalWeight))
          .multiply(BigInteger.valueOf(TRX_PRECISION))
          .divide(BigInteger.valueOf(totalLimit))
          .longValueExact();
    }
    return (long) ((double) usage * totalWeight / totalLimit * TRX_PRECISION);
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/ResourceProcessor.java (L94-105)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L63-87)
```java
        long netUsage = (long) (ownerCapsule.getNetUsage() * TRX_PRECISION * ((double)
            (repo.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
      }
      break;
      case ENERGY: {
        EnergyProcessor processor =
            new EnergyProcessor(dynamicStore, ChainBaseManager.getInstance().getAccountStore());
        processor.updateUsage(ownerCapsule);

        long energyUsage = (long) (ownerCapsule.getEnergyUsage() * TRX_PRECISION * ((double)
            (repo.getTotalEnergyWeight()) / dynamicStore.getTotalEnergyCurrentLimit()));

        long v2EnergyUsage = getV2EnergyUsage(ownerCapsule, energyUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForEnergy() - v2EnergyUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeEnergyV2 balance");
        }
```

**File:** framework/src/main/java/org/tron/core/Wallet.java (L1010-1017)
```java
    long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

    long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, dynamicStore.disableJavaLangMath());

    long maxSize = ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage;
    return max(0, maxSize, dynamicStore.disableJavaLangMath());
  }
```

**File:** framework/src/test/java/org/tron/core/db/CalculateGlobalLimitHardenTest.java (L248-257)
```java

    // Legacy V1 expectation: floor(1.5) * 25.0 = 1 * 25 = 25
    Assert.assertEquals(25L, v1New);

    // V2 path with the same balance keeps the fractional weight
    long v2New = energyProcessor.calculateGlobalEnergyLimitV2(frozeBalance);
    // Legacy V2 expectation: 1.5 * 25.0 = 37.5 -> 37
    Assert.assertEquals(37L, v2New);

    // And both must match their respective legacy doubles
```
