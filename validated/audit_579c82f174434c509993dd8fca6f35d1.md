### Title
Truncating (floor) rounding in `DelegateResourceActuator`/`DelegateResourceProcessor` "available V2 balance" check lets delegators over-delegate resource that is still consumed by their own usage - ([File: actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java])

### Summary
`DelegateResourceActuator.validate()` and its TVM-native equivalent `DelegateResourceProcessor.validate()` gate the `DelegateResourceContract`/`delegateResource` precompile by converting an account's current bandwidth/energy *usage* into an equivalent amount of frozen TRX, using a global rate (`totalWeight / totalLimit`) exactly analogous to an oracle-provided exchange rate in the `YTokenL2` report. That converted value (`netUsage`/`energyUsage`) is computed with `(long) (double * double)`, which always truncates toward zero for the positive inputs involved — i.e. it always rounds down in favor of the caller, never in favor of the protocol's resource accounting. The truncated usage is then subtracted from the frozen V2 balance to determine how much may still be delegated. This is structurally the same bug class as the reported `previewMint`/`previewWithdraw` issue: a rate-based conversion that should round in favor of the "vault" (the resource-accounting invariant) instead rounds in favor of the "user" (the delegator).

### Finding Description
In `DelegateResourceActuator.validate()`: [1](#0-0) 
and identically in `DelegateResourceProcessor.validate()`: [2](#0-1) 

`netUsage`/`energyUsage` estimate how much of the account's *already consumed* bandwidth/energy is attributable to a fluctuating global rate (`totalNetWeight/totalNetLimit` or `totalEnergyWeight/totalEnergyCurrentLimit`), which — like the L1→L2 oracle exchange rate in the YieldFi report — is external, protocol-wide state that is not directly managed by the delegating account's own balance bookkeeping. The result is cast with `(long)`, which truncates toward zero. Because all operands are non-negative, this always rounds the estimated usage **down**.

That (rounded-down) usage is then fed into `FreezeV2Util.getV2NetUsage` / `getV2EnergyUsage`: [3](#0-2) 
which subtracts the account's other resource sources from `netUsage`/`energyUsage` to derive the portion of usage that must be "backed" by `FrozenV2` balance (`v2NetUsage`/`v2EnergyUsage`). Since the underlying usage figure was floored, `v2NetUsage`/`v2EnergyUsage` is systematically underestimated (or clamped to 0 sooner than it should be).

Finally, the check:
```java
if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) { ... }
```
uses this underestimated `v2NetUsage`, so it overestimates the truly "free" (undelegated, unused) `FrozenV2` balance. This is the exact analog of `previewMint`/`previewWithdraw` rounding in favor of the caller instead of the vault/ledger: the caller (delegator) is systematically permitted to delegate a slightly larger `delegateBalance` than what is actually free once real (non-truncated) usage is accounted for.

### Impact Explanation
This lets an unprivileged account (any `DelegateResourceContract` sender, or any contract invoking the `delegateResource` TVM precompile) delegate TRX-backed bandwidth/energy that is, in exact arithmetic, still being consumed by that very account's own usage. Functionally this is a resource-accounting divergence: the same underlying frozen TRX can end up simultaneously "backing" the owner's consumed resource and "delegated away" to another account's resource pool, inflating the effective resource supply beyond what is truly collateralized by frozen TRX. As in the original report, each individual instance is a small rounding-driven leak (bounded by unit conversion error), but it is systematic and directional (always favors the delegator, never the network), so it can accumulate with repeated/automated delegate calls, degrading the invariant that delegated V2 resource must always be less than or equal to truly free frozen balance.

### Likelihood Explanation
Both call paths (`DelegateResourceContract` actuator and the TVM `delegateResource` native precompile) are reachable by any unprivileged account with frozen V2 balance — no special role is required. The rounding direction is deterministic (not probabilistic): any account whose usage/weight/limit ratio is a non-integer will trigger the floor truncation on every call, so the condition is trivial to hit in normal operation (most floating-point ratios are non-integer).

### Recommendation
Round the usage-to-frozen-balance conversion in favor of the ledger/network invariant rather than the caller: use ceiling division (as already done elsewhere in the codebase, e.g. `RepositoryImpl.divideCeil`/`divideCeilExact`) when computing `netUsage`/`energyUsage` in `DelegateResourceActuator.validate()` and `DelegateResourceProcessor.validate()`, so that the estimated usage attributable to `FrozenV2` balance is never underestimated. This mirrors the recommended fix in the report: replace ad-hoc rounding with an explicit, invariant-preserving rounding direction, consistently rounding "usage owed" up rather than down.

### Proof of Concept
Given:
- `ownerCapsule.getNetUsage() = 3`, `TRX_PRECISION = 1_000_000`
- `totalNetWeight = 1`, `totalNetLimit = 3` (ratio = 1/3, a non-terminating fraction)
- exact `netUsage_exact = 3 * 1_000_000 * (1/3) = 1_000_000.0`, but due to floating point and truncation with slightly different values (e.g. `totalNetWeight = 2`, `totalNetLimit = 3` → ratio 0.6667):
  `netUsage_exact ≈ 3 * 1_000_000 * 0.6666... = 1_999_999.999...`
  `(long)` truncation yields `1_999_999` instead of the mathematically-correct `2_000_000`.
- This 1 TRX-unit deficit propagates into `v2NetUsage` (`FreezeV2Util.getV2NetUsage`), understating consumed usage by 1 unit.
- At the exact boundary where `FrozenV2BalanceForBandwidth - v2NetUsage_exact == delegateBalance - 1`, the correct (ceiling) computation would reject the delegate call (`... < delegateBalance` true), but with the current floor truncation the check passes and the `DelegateResourceContract`/`delegateResource` call succeeds, delegating 1 unit of TRX that is still backing real usage.

Repeating this at scale (many accounts, many delegate calls, or automated bots crafting weight/limit ratios that maximize the truncation error) accumulates a persistent divergence between total frozen V2 balance and total (delegated + self-used) resource backing, analogous to the "slow, continuous loss" characterized in the original report. [4](#0-3)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L152-169)
```java
    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
        processor.updateUsageForDelegated(ownerCapsule);

        long accountNetUsage = ownerCapsule.getNetUsage();
        if (null != this.getTx() && this.getTx().isTransactionCreate()) {
          accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
                  ownerCapsule.getFrozenV2BalanceForBandwidth());
        }
        long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
              "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L63-71)
```java
        long netUsage = (long) (ownerCapsule.getNetUsage() * TRX_PRECISION * ((double)
            (repo.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));

        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage, disableJavaLangMath);

        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
                  "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
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
