### Title
Total resource weight (`TotalNetWeight`/`TotalEnergyWeight`) accounting drift in `FreezeBalanceProcessor` diverges from per-account frozen weight - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java`)

### Summary
This mirrors the Gearbox report's bug class: a global aggregate counter (`totalBorrowed` / here `TotalNetWeight`/`TotalEnergyWeight`) is updated using a different formula than the one used to compute the per-account contribution, so the aggregate silently drifts away from the true sum of individual account weights over repeated operations.

### Finding Description
When freezing TRX for bandwidth/energy through the ordinary actuators (`FreezeBalanceActuator`, `FreezeBalanceV2Actuator`, `FreezeBalanceV2Processor`), the code consistently computes the weight delta as `(newTotalFrozen / TRX_PRECISION) - (oldTotalFrozen / TRX_PRECISION)` before applying it to the global weight counter, e.g.: [1](#0-0) 

This pattern correctly accounts for the sub-`TRX_PRECISION` remainder that may already exist on the account: if an account already has a fractional remainder frozen (e.g. 0.5 TRX), a new freeze that completes the remainder into a full TRX unit is properly reflected as +1 weight.

However, `FreezeBalanceProcessor` (the native/TVM contract-invoked path for the old-style freeze, reachable from a smart contract executing the freeze-balance native opcode) does **not** follow this pattern. It updates the global weight using the raw newly-frozen amount alone, ignoring the account's pre-existing fractional remainder: [2](#0-1) 

Because the account-level frozen balance (`accountCapsule.getFrozenBalance()` / `getEnergyFrozenBalance()`) is stored with full precision, but the global weight adjustment truncates only the newly added `frozenBalance` argument to `TRX_PRECISION` in isolation instead of computing `(new total / TRX_PRECISION) - (old total / TRX_PRECISION)`, repeated freezes with sub-precision remainders on the same account will systematically undercount the true weight contributed to `TotalNetWeight`/`TotalEnergyWeight`. For example: account already holds `500_000` frozen (0.5 TRX, contributing weight 0 due to truncation). A subsequent freeze of `500_000` via this processor computes `frozenBalance / TRX_PRECISION = 0`, so the global counter is not incremented, even though the account's true frozen total is now `1_000_000` (weight 1). The sum of per-account truncated weights therefore exceeds what was actually added to the global `TotalNetWeight`/`TotalEnergyWeight` counters.

### Impact Explanation
`TotalNetWeight`/`TotalEnergyWeight` are used to compute each account's proportional share of the network-wide bandwidth/energy limit via `calculateGlobalEnergyLimit`/analogous bandwidth calculations: [3](#0-2) 
If the global denominator (`TotalNetWeight`/`TotalEnergyWeight`) is understated relative to the true aggregate frozen weight across accounts, every account's computed resource limit share is inflated, effectively over-allocating free bandwidth/energy network-wide. This is a resource-accounting correctness issue that can be abused by adversarial accounts to obtain more resource allowance than they are entitled to, potentially enabling cheaper/free execution of transactions or contract calls at the expense of network-wide resource guarantees — a resource-accounting corruption analogous to the Gearbox pool/borrowed-amount mismatch.

### Likelihood Explanation
Triggering this path requires a smart contract that invokes the freeze-balance native contract call with balances that leave a sub-`TRX_PRECISION` remainder and then follow up with an additional freeze — both of which are actions an unprivileged contract deployer/caller can perform via ordinary broadcast transactions. No special privileges are required. Repeated small freezes accumulate drift over time.

### Recommendation
In `FreezeBalanceProcessor.execute`, compute the weight delta the same way the other actuators do — as the difference between the post-freeze truncated weight and the pre-freeze truncated weight of the account's total frozen balance — rather than truncating the newly added `frozenBalance` in isolation:
```java
long oldWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION; // before update
...
long newWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION; // after update
repo.addTotalNetWeight(newWeight - oldWeight);
```
Apply the analogous fix for the ENERGY branch.

### Proof of Concept
1. Deploy a contract that calls the native freeze-balance opcode for BANDWIDTH with `frozenBalance = 500_000` (0.5 TRX) — global `TotalNetWeight` increments by `500_000 / 1_000_000 = 0`; account frozen balance becomes `500_000`.
2. Call the same native freeze-balance opcode again with `frozenBalance = 500_000` — the processor again computes `500_000 / 1_000_000 = 0` and adds `0` to `TotalNetWeight`, even though the account's total frozen balance is now `1_000_000` (a true weight of `1`).
3. Read `dynamicStore.getTotalNetWeight()` before/after and compare against the sum of `accountCapsule.getFrozenBalance() / TRX_PRECISION` across affected accounts — the global counter is now permanently understated relative to the true aggregate, confirming the drift.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L61-65)
```java
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L118-129)
```java
    // adjust total resource
    switch (param.getResourceType()) {
      case BANDWIDTH:
        repo.addTotalNetWeight(frozenBalance / TRX_PRECISION);
        break;
      case ENERGY:
        repo.addTotalEnergyWeight(frozenBalance / TRX_PRECISION);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L967-984)
```java
  public long calculateGlobalEnergyLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForEnergy();
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }
    long energyWeight = frozeBalance / TRX_PRECISION;
    long totalEnergyLimit = getDynamicPropertiesStore().getTotalEnergyCurrentLimit();
    long totalEnergyWeight = getDynamicPropertiesStore().getTotalEnergyWeight();

    assert totalEnergyWeight > 0;

    if (hardenResourceCalculation()) {
      return BigInteger.valueOf(energyWeight)
          .multiply(BigInteger.valueOf(totalEnergyLimit))
          .divide(BigInteger.valueOf(totalEnergyWeight))
          .longValueExact();
    }
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
```
