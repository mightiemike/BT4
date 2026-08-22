### Title
Legacy `unfreezeBalance()` TVM precompile miscomputes total resource weight delta, causing `TotalNetWeight`/`TotalEnergyWeight` divergence from actual account state - (File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java)

### Summary
`UnfreezeBalanceProcessor.execute()`, reachable via the TVM `unfreezeBalanceAction` precompile invoked by any smart contract (`Program.java`), updates the global `TotalNetWeight`/`TotalEnergyWeight` counters by directly subtracting `unfreezeBalance / TRX_PRECISION` instead of computing the actual before/after weight delta of the account, unlike every sibling actuator that performs the same conceptual operation (`UnfreezeBalanceActuator`, `FreezeBalanceActuator`, `FreezeBalanceV2Actuator`, `UnfreezeBalanceV2Actuator`, `CancelAllUnfreezeV2Actuator/Processor`). This is the same bug class as the reported yAxis `setCap` issue: a shared/global tracked balance is decremented by a locally-derived raw amount instead of the real state delta, causing the aggregate accounting value to diverge from the sum of the underlying per-account state it is supposed to mirror.

### Finding Description
In `UnfreezeBalanceProcessor.execute()` [1](#0-0) , the non-delegating BANDWIDTH/ENERGY unfreeze path clears the account's frozen entries and computes `unfreezeBalance` as the sum of removed frozen amounts, then at the end does:

```java
// adjust total resource, used to be a bug here
switch (param.getResourceType()) {
  case BANDWIDTH:
    repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION);
    break;
  case ENERGY:
    repo.addTotalEnergyWeight(-unfreezeBalance / TRX_PRECISION);
    break;
  ...
}
``` [2](#0-1) 

The self-referential comment "used to be a bug here" indicates this exact spot has already been the site of an accounting bug once. Every other place in the codebase that performs the equivalent operation instead computes the weight delta from the account's frozen balance snapshots before and after mutation:

- `UnfreezeBalanceActuator` (legacy contract path): `decrease = newNetWeight - oldNetWeight` computed from `accountCapsule.getFrozenBalance()/TRX_PRECISION` before/after [3](#0-2) 
- `FreezeBalanceActuator`: `increment = newNetWeight - oldNetWeight` [4](#0-3) 
- `FreezeBalanceV2Actuator`/`FreezeBalanceV2Processor` and `UnfreezeBalanceV2Actuator`/`CancelAllUnfreezeV2Processor`: all consistently use `newWeight - oldWeight` derived from `getFrozenV2BalanceWithDelegated(...)` before/after the mutation [5](#0-4) [6](#0-5) 

Because the account frozen balance is composed of a list of `Frozen` entries with independent integer-truncated amounts (`accountCapsule.getFrozenBalance() / TRX_PRECISION`), directly subtracting `unfreezeBalance / TRX_PRECISION` (the raw removed amount, truncated independently) is not equivalent to `oldWeight - newWeight` (truncation of the aggregate sums before and after). For example, if the account holds two frozen entries of 900,000 and 900,000 (total 1,800,000 → weight = 1) and one of them (900,000, exactly the expired entry) is unfrozen, the correct weight delta is `1 - 0 = 1`, but the buggy code computes `-900,000 / 1,000,000 = 0`. `TotalNetWeight`/`TotalEnergyWeight` is therefore left stale/too high relative to the real sum of all accounts' weights.

This global counter (`DynamicPropertiesStore.getTotalNetWeight()`/`getTotalEnergyWeight()`) is the divisor used network-wide to compute every account's proportional bandwidth/energy limit via `calculateGlobalNetLimit`/`calculateGlobalEnergyLimit` [7](#0-6) [8](#0-7) , and it is also asserted to be strictly positive in some legacy code paths (`assert totalEnergyWeight > 0;`) [9](#0-8) , mirroring the original report's pattern where a stale aggregate tripped an invariant check and locked/misallocated funds elsewhere in the system.

### Impact Explanation
This is a consensus-critical accounting bug reachable by any smart contract that calls `unfreezeBalance()` on itself via the TVM native contract path (`Program.unfreezeBalanceAction` → `UnfreezeBalanceProcessor.execute`). Repeated triggering skews `TotalNetWeight`/`TotalEnergyWeight` upward relative to the true sum of frozen resources across the network, which (a) systematically under-allocates bandwidth/energy limits to every account proportionally (since the divisor is inflated), degrading network resource allocation fairness for all users, and (b) can, in combination with the `assert totalEnergyWeight > 0` invariant and downstream divide-by-mismatched-weight computations, produce persistent divergence between the tracked global weight and actual on-chain frozen resource totals — the same class of "shared accounting balance diverges from real underlying state" defect as the reported `setCap` vulnerability, but here at protocol/consensus level rather than contract level.

### Likelihood Explanation
The legacy (non-V2) freeze/unfreeze resource model and its TVM precompile are still present and reachable in this codebase (confirmed via `Program.java` invoking `UnfreezeBalanceProcessor`) and require no special privilege — any deployed contract or account can call `unfreezeBalance()` through the TVM once it has frozen balance eligible for unfreezing. Triggering the divergence requires a specific frozen-entry composition (multiple frozen entries whose independent truncation differs from the aggregate truncation), which occurs naturally whenever multiple `freezeBalance` operations are made in different amounts and later selectively unfrozen — a routine, unprivileged usage pattern, not an edge case requiring adversarial setup.

### Recommendation
Replace the direct `-unfreezeBalance / TRX_PRECISION` weight adjustment in `UnfreezeBalanceProcessor.execute()` with the same before/after weight-delta pattern used everywhere else in the codebase, e.g.:
```java
long oldWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION; // or energy equivalent, captured before mutation
... mutate frozen list / clear frozen energy balance ...
long newWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
repo.addTotalNetWeight(newWeight - oldWeight);
```
applied analogously for the ENERGY branch, consistent with `UnfreezeBalanceActuator` and the V2 processors.

### Proof of Concept
1. Via a smart contract, freeze balance twice for BANDWIDTH from the same account: 900,000 sun and 900,000 sun (each below `TRX_PRECISION` individually contributes to a combined weight but let the first entry expire before the second).
2. After the first entry's expiration time passes, invoke `unfreezeBalanceV2`/`unfreezeBalance()` via the TVM precompile for that account, unfreezing only the expired 900,000 sun entry (`unfreezeBalance = 900,000`).
3. Compute `oldWeight = 1,800,000 / 1,000,000 = 1`, `newWeight = 900,000 / 1,000,000 = 0` → correct delta is `-1`.
4. The buggy code instead computes `-unfreezeBalance / TRX_PRECISION = -900,000 / 1,000,000 = 0`, leaving `TotalNetWeight` unchanged even though the account's real contribution to bandwidth weight has dropped by 1.
5. Repeating this pattern across many accounts/contracts causes `TotalNetWeight` to accumulate a persistent positive bias relative to the true sum of all accounts' `frozenBalance / TRX_PRECISION`, corrupting the network-wide bandwidth/energy limit calculation for every account indefinitely.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L155-201)
```java
    } else {
      switch (param.getResourceType()) {
        case BANDWIDTH:
          List<Protocol.Account.Frozen> frozenList = Lists.newArrayList();
          frozenList.addAll(accountCapsule.getFrozenList());
          Iterator<Protocol.Account.Frozen> iterator = frozenList.iterator();
          long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
          while (iterator.hasNext()) {
            Protocol.Account.Frozen next = iterator.next();
            if (next.getExpireTime() <= now) {
              unfreezeBalance += next.getFrozenBalance();
              iterator.remove();
            }
          }
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .clearFrozen().addAllFrozen(frozenList).build());
          break;
        case ENERGY:
          unfreezeBalance = accountCapsule.getAccountResource().getFrozenBalanceForEnergy()
              .getFrozenBalance();
          Protocol.Account.AccountResource newAccountResource =
              accountCapsule.getAccountResource().toBuilder()
              .clearFrozenBalanceForEnergy().build();
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .setAccountResource(newAccountResource).build());
          break;
        default:
          //this should never happen
          break;
      }

    }

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

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L192-235)
```java
    } else {
      switch (unfreezeBalanceContract.getResource()) {
        case BANDWIDTH:
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          List<Frozen> frozenList = Lists.newArrayList();
          frozenList.addAll(accountCapsule.getFrozenList());
          Iterator<Frozen> iterator = frozenList.iterator();
          long now = dynamicStore.getLatestBlockHeaderTimestamp();
          while (iterator.hasNext()) {
            Frozen next = iterator.next();
            if (next.getExpireTime() <= now) {
              unfreezeBalance += next.getFrozenBalance();
              iterator.remove();
            }
          }

          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .clearFrozen().addAllFrozen(frozenList).build());
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          decrease = newNetWeight - oldNetWeight;
          break;
        case ENERGY:
          long oldEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          unfreezeBalance = accountCapsule.getAccountResource().getFrozenBalanceForEnergy()
              .getFrozenBalance();

          AccountResource newAccountResource = accountCapsule.getAccountResource().toBuilder()
              .clearFrozenBalanceForEnergy().build();
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .setAccountResource(newAccountResource).build());
          long newEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          decrease = newEnergyWeight - oldEnergyWeight;
          break;
        case TRON_POWER:
          long oldTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
          unfreezeBalance = accountCapsule.getTronPowerFrozenBalance();
          accountCapsule.setInstance(accountCapsule.getInstance().toBuilder()
              .setBalance(oldBalance + unfreezeBalance)
              .clearTronPower().build());
          long newTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
          decrease = newTPWeight - oldTPWeight;
          break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L88-119)
```java
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForBandwidth =
              frozenBalance + accountCapsule.getFrozenBalance();
          accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          increment = newNetWeight - oldNetWeight;
        }
        addTotalWeight(BANDWIDTH, dynamicStore, frozenBalance, increment);
        break;
      case ENERGY:
        if (!ArrayUtils.isEmpty(receiverAddress)
            && dynamicStore.supportDR()) {
          increment = delegateResource(ownerAddress, receiverAddress, false,
                  frozenBalance, expireTime);
          accountCapsule.addDelegatedFrozenBalanceForEnergy(frozenBalance);
        } else {
          long oldEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForEnergy =
              frozenBalance + accountCapsule.getEnergyFrozenBalance();
          accountCapsule.setFrozenForEnergy(newFrozenBalanceForEnergy, expireTime);
          long newEnergyWeight = accountCapsule.getEnergyFrozenBalance() / TRX_PRECISION;
          increment = newEnergyWeight - oldEnergyWeight;
        }
        addTotalWeight(ENERGY, dynamicStore, frozenBalance, increment);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
        long newFrozenBalanceForTronPower =
            frozenBalance + accountCapsule.getTronPowerFrozenBalance();
        accountCapsule.setFrozenForTronPower(newFrozenBalanceForTronPower, expireTime);
        long newTPWeight = accountCapsule.getTronPowerFrozenBalance() / TRX_PRECISION;
        increment = newTPWeight - oldTPWeight;
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L60-78)
```java
    switch (freezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(frozenBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java (L72-97)
```java
  public void updateFrozenInfoAndTotalResourceWeight(
      AccountCapsule accountCapsule, Protocol.Account.UnFreezeV2 unFreezeV2, Repository repo) {
    switch (unFreezeV2.getType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(unFreezeV2.getUnfreezeAmount());
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(unFreezeV2.getUnfreezeAmount());
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(unFreezeV2.getUnfreezeAmount());
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        // this should never happen
        break;
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java (L432-453)
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
