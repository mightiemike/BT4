### Title
`totalNetWeight`/`totalEnergyWeight`/`totalTronPowerWeight` underflow via TVM native contracts bypasses the clamp added in `DynamicPropertiesStore` - ([File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java])

### Summary
Just like the reported Omnipool `totalDeposited` accounting variable that tracks deposits/withdrawals and can underflow, java-tron maintains global accounting counters `TOTAL_NET_WEIGHT`, `TOTAL_ENERGY_WEIGHT`, and `TOTAL_TRON_POWER_WEIGHT` that track the sum of all users' frozen balances used to compute each account's share of network bandwidth/energy. These counters are incremented on freeze and decremented on unfreeze. The codebase shows that the underflow of these counters is a *known, previously-fixed* bug class — `DynamicPropertiesStore.addTotalNetWeight/addTotalEnergyWeight/addTotalTronPowerWeight` were hardened with a `max(0, ...)` clamp guarded by `allowNewReward()` — but the parallel implementation in `RepositoryImpl`, which is the path used by TVM native contracts (freeze/unfreeze precompiles reachable from smart contracts), was never given the same protection.

### Finding Description
`DynamicPropertiesStore` clamps the weight counters to zero: [1](#0-0) 

But `RepositoryImpl`, which implements the same `Repository` interface used by TVM native contracts, performs the identical add operation with no such protection: [2](#0-1) 

`UnfreezeBalanceProcessor` (the logic backing the TVM `unfreezeBalanceV1`-style native contract, reachable from smart contract code) explicitly calls the unprotected `repo.addTotalNetWeight`/`repo.addTotalEnergyWeight`, and a comment even acknowledges this was previously buggy: [3](#0-2) 

`UnfreezeBalanceV2Processor.updateTotalResourceWeight` similarly calls `repo.addTotalNetWeight`/`addTotalEnergyWeight`/`addTotalTronPowerWeight` (routed to the unprotected `RepositoryImpl` implementation) after computing `newWeight - oldWeight` from `FreezeV2` list amounts: [4](#0-3) 

This is architecturally identical to the reported analog: a single global accounting variable (`totalDeposited` in the report; `totalNetWeight`/`totalEnergyWeight`/`totalTronPowerWeight` here) is incremented on "deposit" (freeze) and decremented on "withdraw" (unfreeze), and one code path lacks protection against the decrement exceeding the running total. `calculateGlobalNetLimit`/`calculateGlobalNetLimitV2` divide by `totalNetWeight` to compute each account's bandwidth allotment: [5](#0-4) 

If `totalNetWeight` (or the energy/TRON-power equivalents) goes negative through the `RepositoryImpl` path, this global divisor becomes corrupted, which distorts the bandwidth/energy limit computation for every account on the network — not just the caller's — since this is a chain-wide dynamic property, not a per-account value.

### Impact Explanation
A corrupted (negative) global weight divisor affects `calculateGlobalNetLimit`/`calculateGlobalNetLimitV2` for every account, producing invalid-state divergence in resource accounting across the whole network — analogous to "funds getting stuck"/other users being harmed by one path's underflow, since the shared global counter is used by all subsequent freeze/unfreeze/bandwidth-consumption calculations. This constitutes a chain-wide invalid-state/accounting corruption once triggered, not a localized loss.

### Likelihood Explanation
Likelihood is uncertain and could not be fully confirmed within available tool calls. I was unable to conclusively establish the exact scenario/opcode sequence (e.g., precision loss in `FreezeV2`/`UnfreezeV2` amount tracking, or interaction between old-style and V2 freeze/unfreeze processed through the TVM path) that would actually drive `newWeight - oldWeight` negative enough, and by enough magnitude, to underflow the total below zero via `RepositoryImpl`. The presence of the explicit clamp in `DynamicPropertiesStore` and the "used to be a bug here" comment in `UnfreezeBalanceProcessor` strongly suggests the underlying scenario is realistic and previously observed, but the asymmetric fix (protecting one implementation and not the other) means this needs runtime/precision-loss verification (e.g., truncating division by `TRX_PRECISION` repeated across many freeze/unfreeze cycles) to confirm exploitability through the TVM-reachable path specifically.

### Recommendation
Apply the same `max(0, ...)` clamp (guarded by `allowNewReward()`/equivalent flag) used in `DynamicPropertiesStore.addTotalNetWeight/addTotalEnergyWeight/addTotalTronPowerWeight` to `RepositoryImpl.addTotalNetWeight/addTotalEnergyWeight/addTotalTronPowerWeight`, so both implementations of the `Repository`/`DynamicPropertiesStore` interfaces protect the shared global weight counters consistently, regardless of whether the freeze/unfreeze logic is invoked from a normal actuator or from a TVM native contract.

### Proof of Concept
Could not be fully constructed/verified with available tooling — would require tracing precise long-division rounding behavior across `FreezeV2`/`UnfreezeV2` amount tracking and TRX_PRECISION division in `updateTotalResourceWeight`/`unfreezeExpire` to demonstrate a concrete sequence of freeze/unfreeze/delegate calls (via TVM native contracts) that drives `oldWeight - newWeight` to underflow `totalNetWeight`/`totalEnergyWeight`/`totalTronPowerWeight` below zero through the unprotected `RepositoryImpl` path.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2269-2280)
```java
  //The unit is trx
  public void addTotalNetWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalNetWeight = getTotalNetWeight();
    totalNetWeight += amount;
    if (allowNewReward()) {
      totalNetWeight = max(0, totalNetWeight, disableJavaLangMath());
    }
    saveTotalNetWeight(totalNetWeight);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L1165-1186)
```java
  //The unit is trx
  @Override
  public void addTotalNetWeight(long amount) {
    long totalNetWeight = getTotalNetWeight();
    totalNetWeight += amount;
    saveTotalNetWeight(totalNetWeight);
  }

  //The unit is trx
  @Override
  public void addTotalEnergyWeight(long amount) {
    long totalEnergyWeight = getTotalEnergyWeight();
    totalEnergyWeight += amount;
    saveTotalEnergyWeight(totalEnergyWeight);
  }

  @Override
  public void addTotalTronPowerWeight(long amount) {
    long totalTronPowerWeight = getTotalTronPowerWeight();
    totalTronPowerWeight += amount;
    saveTotalTronPowerWeight(totalTronPowerWeight);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L190-201)
```java
    // adjust total resource, used to be a bug here
    switch (param.getResourceType()) {
      case BANDWIDTH:
        repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      case ENERGY:
        repo.addTotalEnergyWeight(-unfreezeBalance / TRX_PRECISION);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-204)
```java
  public void updateTotalResourceWeight(AccountCapsule accountCapsule,
                                        Common.ResourceCode freezeType,
                                        long unfreezeBalance,
                                        Repository repo) {
    switch (freezeType) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(-unfreezeBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(-unfreezeBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(-unfreezeBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L432-466)
```java
  public long calculateGlobalNetLimit(AccountCapsule accountCapsule) {
    long frozeBalance = accountCapsule.getAllFrozenBalanceForBandwidth();
    if (dynamicPropertiesStore.supportUnfreezeDelay()) {
      return calculateGlobalNetLimitV2(frozeBalance);
    }
    if (frozeBalance < TRX_PRECISION) {
      return 0;
    }
    long totalNetLimit = chainBaseManager.getDynamicPropertiesStore().getTotalNetLimit();
    long totalNetWeight = chainBaseManager.getDynamicPropertiesStore().getTotalNetWeight();
    if (dynamicPropertiesStore.allowNewReward() && totalNetWeight <= 0) {
      return 0;
    }
    if (totalNetWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV1(frozeBalance, totalNetLimit, totalNetWeight);
    }
    long netWeight = frozeBalance / TRX_PRECISION;
    return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
  }

  public long calculateGlobalNetLimitV2(long frozeBalance) {
    long totalNetLimit = dynamicPropertiesStore.getTotalNetLimit();
    long totalNetWeight = dynamicPropertiesStore.getTotalNetWeight();
    if (totalNetWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV2(frozeBalance, totalNetLimit, totalNetWeight);
    }
    double netWeight = (double) frozeBalance / TRX_PRECISION;
    return (long) (netWeight * ((double) totalNetLimit / totalNetWeight));
  }
```
