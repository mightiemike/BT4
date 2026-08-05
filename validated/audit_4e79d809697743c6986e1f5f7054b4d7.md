### Title
Global energy/bandwidth resource weight is manipulable within a single account-controlled window, causing a live-ratio "unfair distribution" of the shared resource pool - (File: `chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java`)

### Summary
`totalEnergyWeight`/`totalNetWeight` are global counters that are updated **instantly** on every freeze/unfreeze call (both from ordinary transactions and from TVM-callable native contracts), and are read **live** to compute each account's proportional share of the shared, capped resource pool (`totalEnergyCurrentLimit` / `totalNetLimit`). This is structurally the same "live ratio of user_stake / total_stake" pattern flagged in the ZivoeYDL finding (M-11), where a party can temporarily inflate the denominator/numerator pair it controls, capture a larger share of a fixed pool, then reverse the stake.

### Finding Description
`EnergyProcessor.calculateGlobalEnergyLimit` / `calculateGlobalEnergyLimitV2` compute an account's energy allocation as:
`energyLimit = accountFrozenForEnergy/TRX_PRECISION * totalEnergyCurrentLimit / totalEnergyWeight` [1](#0-0) 

`totalEnergyWeight` (and the bandwidth equivalent `totalNetWeight`) is a single global mutable counter maintained in `DynamicPropertiesStore`, updated synchronously and immediately whenever any account freezes or unfreezes TRX for `ENERGY`/`BANDWIDTH` — there is no cycle/epoch snapshot delay like the one used for witness-vote rewards (`DelegationStore`/`MortgageService`, which deliberately batch vote-count changes to once per maintenance cycle to prevent exactly this class of manipulation, see `MaintenanceManager.doMaintenance`). [2](#0-1) [3](#0-2) 

Critically, `FreezeBalanceV2Processor`/`UnfreezeBalanceV2Processor` are **TVM native contracts**, callable directly by a smart contract via the `freezeBalanceV2`/`unfreezeBalanceV2` precompiles, and they mutate `totalEnergyWeight`/`totalNetWeight` immediately upon execution with no minimum holding period (unlike legacy V1 freeze, which enforced `minFrozenTime`). [4](#0-3) 

Because `energyLimit` for every other frozen account is computed live against the same global `totalEnergyWeight`, an account that freezes a very large amount of TRX for ENERGY/BANDWIDTH temporarily inflates the shared denominator (diluting every other staker's resource limit for that block/window) while simultaneously inflating its own numerator by the same freeze, netting itself a disproportionately larger slice of the fixed pool (`totalEnergyCurrentLimit`) for that period, before unfreezing (which — via `UnfreezeBalanceV2Processor.updateTotalResourceWeight` — reduces `totalEnergyWeight` back down immediately, regardless of the multi-day TRX withdrawal lock). [5](#0-4) 

This is the exact structural analog of the reported bug class: a live totalSupply/total-weight ratio used to split a shared reward/resource pool, where staking and unstaking the ratio's own contribution can be done without the intended long-term commitment, at other participants' expense.

### Impact Explanation
Impact is a temporary but real misallocation of the network's finite, priced resource pool (`totalEnergyCurrentLimit`/`totalNetLimit`), analogous to the "unfair distribution of rewards" impact in the original finding: legitimate long-term stakers see their free energy/bandwidth allocation diluted whenever a large staker freezes-and-unfreezes around their own usage window, while the manipulator obtains an outsized allocation for that period without a comparable long-term commitment of capital (only the multi-day unstaking lock on withdrawal, not on the weight accounting). This falls under the "resources" and "underpriced public work" categories explicitly in scope. It does not involve any privileged role — freeze/unfreeze and the TVM precompiles are open to any account/contract.

### Likelihood Explanation
Medium-low. Unlike the ERC-20 `totalSupply()` case, TRX freezing requires the actor to actually own/borrow real TRX (not an ERC-20 flash loan primitive), and the effect (temporarily diluting/ inflating a shared ratio) benefits the actor mainly during the exact block/period it is executed in, requiring precise timing around the victim's resource-consuming transaction. It is realistically executable by any account or smart contract via the TVM `freezeBalanceV2`/`unfreezeBalanceV2` precompiles without any special privilege, and no minimum holding time gates the weight update (in contrast to the deliberately time-locked witness-vote reward mechanism in the same codebase), so the primary bug-class ingredient (live, unlocked total-weight ratio) is present and reachable.

### Recommendation
Apply the same mitigation the codebase already uses for witness-vote rewards: snapshot `totalEnergyWeight`/`totalNetWeight` per resource epoch (or enforce a minimum holding period before a freeze contributes to `totalEnergyWeight`, mirroring the removed `minFrozenTime` check from V1 freeze), rather than reading the live, instantly-mutable global counter for every transaction's resource-limit computation.

### Proof of Concept
1. Attacker acquires a large amount of TRX (own funds or short-term loan).
2. Attacker (EOA or smart contract) calls `FreezeBalanceV2Contract`/`freezeBalanceV2` precompile for `ENERGY`, instantly increasing both their own frozen energy balance and the global `totalEnergyWeight` (`FreezeBalanceV2Actuator.execute` / `FreezeBalanceV2Processor.execute`).
3. Attacker's `calculateGlobalEnergyLimit` is now computed against the inflated `totalEnergyWeight`, granting them (and diluting everyone else's) share of `totalEnergyCurrentLimit` for the current window, per [6](#0-5) .
4. Attacker executes resource-intensive contract calls benefiting from the boosted energy limit.
5. Attacker calls `UnfreezeBalanceV2Contract`/`unfreezeBalanceV2`, which immediately reverses `totalEnergyWeight` via `updateTotalResourceWeight`, per [3](#0-2) , restoring the pre-attack ratio for other stakers — the multi-day TRX withdrawal lock does not gate this weight reversal, only the return of principal.

### Citations

**File:** chainbase/src/main/java/org/tron/core/db/EnergyProcessor.java (L145-179)
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

  public long calculateGlobalEnergyLimitV2(long frozeBalance) {
    long totalEnergyLimit = dynamicPropertiesStore.getTotalEnergyCurrentLimit();
    long totalEnergyWeight = dynamicPropertiesStore.getTotalEnergyWeight();
    if (totalEnergyWeight == 0) {
      return 0;
    }
    if (hardenCalculation()) {
      return calculateGlobalLimitV2(frozeBalance, totalEnergyLimit, totalEnergyWeight);
    }
    double energyWeight = (double) frozeBalance / TRX_PRECISION;
    return (long) (energyWeight * ((double) totalEnergyLimit / totalEnergyWeight));
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L60-72)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L118-146)
```java
  public long execute(UnfreezeBalanceV2Param param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    long unfreezeBalance = param.getUnfreezeBalance();
    VoteRewardUtil.withdrawReward(ownerAddress, repo);

    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();

    long unfreezeExpireBalance = this.unfreezeExpire(accountCapsule, now);

    if (repo.getDynamicPropertiesStore().supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }

    long expireTime = this.calcUnfreezeExpireTime(now, repo);
    accountCapsule.addUnfrozenV2List(param.getResourceType(), unfreezeBalance, expireTime);

    this.updateTotalResourceWeight(accountCapsule, param.getResourceType(), unfreezeBalance, repo);
    this.updateVote(accountCapsule, param.getResourceType(), ownerAddress, repo);

    if (repo.getDynamicPropertiesStore().supportAllowNewResourceModel()
        && !accountCapsule.oldTronPowerIsInvalid()) {
      accountCapsule.invalidateOldTronPower();
    }

    repo.updateAccount(accountCapsule.createDbKey(), accountCapsule);
    return unfreezeExpireBalance;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L178-205)
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
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java (L68-97)
```java
  public void execute(FreezeBalanceV2Param param, Repository repo) {
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();

    byte[] ownerAddress = param.getOwnerAddress();
    long frozenBalance = param.getFrozenBalance();
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    if (dynamicStore.supportAllowNewResourceModel()
        && accountCapsule.oldTronPowerIsNotInitialized()) {
      accountCapsule.initializeOldTronPower();
    }
    switch (param.getResourceType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        repo.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        repo.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(frozenBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
```
