## Title
`CancelAllUnfreezeV2Processor` (TVM native contract) silently drops `TRON_POWER` unfreeze requests, causing frozen balance and weight accounting to diverge from the actuator path - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java`)

### Summary
The `CancelAllUnfreezeV2` feature has two parallel implementations that are supposed to perform the same accounting operation: the ordinary transaction actuator `CancelAllUnfreezeV2Actuator` and the TVM-callable native contract `CancelAllUnfreezeV2Processor` (invoked via `Program.cancelAllUnfreezeV2Action()`). The actuator correctly restores pending `BANDWIDTH`, `ENERGY`, and `TRON_POWER` unfreeze entries back into frozen balance and updates the corresponding global weight, but the TVM processor's helper only handles `BANDWIDTH` and `ENERGY`, silently falling through `default: break;` for `TRON_POWER`. This mirrors the Gondi `settleWithBuyout()` bug class: one settlement/restoration code path correctly calls the full accounting update, while a second, reachable code path for the same operation skips part of it, corrupting global and per-account accounting.

### Finding Description
`CancelAllUnfreezeV2Actuator.updateFrozenInfoAndTotalResourceWeight()` handles all three resource types and restores state for each: [1](#0-0) 

In contrast, the TVM-facing `CancelAllUnfreezeV2Processor` — used when a smart contract invokes the `cancelAllUnfreezeV2()` native precompile — only imports and switches on `BANDWIDTH` and `ENERGY` (note `TRON_POWER` is not even imported): [2](#0-1) [3](#0-2) 

The `execute()` method iterates over every pending `UnFreezeV2` entry, and for any entry whose expire time is still in the future, it calls `updateFrozenInfoAndTotalResourceWeight` and then, regardless of resource type, clears the entire `unfrozenV2` list at the end: [4](#0-3) 

Because `updateFrozenInfoAndTotalResourceWeight` falls into the `default` branch for `TRON_POWER`, no code ever calls `accountCapsule.addFrozenForTronPowerV2(...)` or `repo.addTotalTronPowerWeight(...)` for that entry. Yet the entry is still removed from `unfrozenV2` by `ownerCapsule.clearUnfrozenV2()`, and it is not added to `withdrawExpireBalance` either (since it only enters that branch when the expire time is `<= now`, and this entry's expire time is still in the future). The pending TRON_POWER unfreeze amount is therefore erased from all three places it could legitimately live: not in `unfrozenV2` (pending withdraw), not in `FrozenV2` for TRON_POWER (re-frozen/voting power), and not added to `balance`. This is functionally identical to the Gondi bug: the "restore/settle" accounting call (`updateFrozenInfoAndTotalResourceWeight`) is skipped for one code path, breaking the invariant that total frozen + unfrozen + balance is conserved.

This is reachable by any unprivileged account: an account that froze TRX for `TRON_POWER` (voting), then requested `UnfreezeBalanceV2` for `TRON_POWER` (still within the unfreeze delay window, so it is pending in `unfrozenV2`), and then triggers the `cancelAllUnfreezeV2()` TVM precompile from a smart contract (rather than sending the `CancelAllUnfreezeV2Contract` transaction directly) will have their pending TRON_POWER amount discarded.

### Impact Explanation
Any user who has requested `UnfreezeBalanceV2` with resource type `TRON_POWER` and later calls the TVM-exposed `cancelAllUnfreezeV2()` native contract loses the corresponding amount of TRX tracked value entirely — it disappears from `unfrozenV2`, is not restored to `FrozenV2ForTronPower`, and is not credited to `balance`. In addition, `TotalTronPowerWeight` (global voting weight accounting used for reward/voting-power calculations network-wide) is left stale/under-counted relative to what it should be if the cancel had succeeded correctly, corrupting protocol-wide accounting state. This is a direct, unprivileged-user-triggerable state/accounting corruption and fund-loss bug.

### Likelihood Explanation
High likelihood of occurrence in practice: any account can freeze TRX for TRON_POWER, request `UnfreezeBalanceV2`, and — instead of sending the normal `CancelAllUnfreezeV2Contract` transaction — deploy or call a trivial smart contract that invokes the `cancelAllUnfreezeV2` TVM precompile (`Program.cancelAllUnfreezeV2Action()`). No special privileges or unusual preconditions are required; this is a straightforward divergence between two production code paths for the exact same feature.

### Recommendation
Add the missing `case TRON_POWER:` branch to `CancelAllUnfreezeV2Processor.updateFrozenInfoAndTotalResourceWeight()`, mirroring `CancelAllUnfreezeV2Actuator`'s logic: restore the amount via `accountCapsule.addFrozenForTronPowerV2(unFreezeV2.getUnfreezeAmount())` and update the corresponding total via `repo.addTotalTronPowerWeight(newTPWeight - oldTPWeight)`. Ideally, refactor both implementations to share a single accounting helper to prevent this kind of actuator/native-contract logic drift from recurring.

### Proof of Concept
1. Account `A` freezes TRX with `freezeBalanceV2` for resource type `TRON_POWER` (obtaining voting power).
2. Account `A` calls `unfreezeBalanceV2` for `TRON_POWER`, creating a pending `UnFreezeV2` entry in `unfrozenV2` with `unfreezeExpireTime` in the future (unfreeze delay not yet elapsed).
3. A smart contract belonging to (or called by) account `A` invokes the TVM native `cancelAllUnfreezeV2()` precompile, which routes through `Program.cancelAllUnfreezeV2Action()` → `CancelAllUnfreezeV2Processor.execute()`.
4. In `execute()`, since `unFreezeV2.getUnfreezeExpireTime() > now`, `updateFrozenInfoAndTotalResourceWeight` is called; because the type is `TRON_POWER` it hits `default: break;` and does nothing.
5. `ownerCapsule.clearUnfrozenV2()` removes the entry regardless.
6. Resulting state: the TRON_POWER amount is absent from `unfrozenV2`, absent from `FrozenV2ForTronPower`, and absent from `balance` — permanently lost from account-level accounting, and `TotalTronPowerWeight` is never incremented back, corrupting global accounting. [5](#0-4) [6](#0-5)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java (L176-205)
```java
  public void updateFrozenInfoAndTotalResourceWeight(
      AccountCapsule accountCapsule, UnFreezeV2 unFreezeV2,
      Triple<Pair<AtomicLong, AtomicLong>, Pair<AtomicLong, AtomicLong>,
          Pair<AtomicLong, AtomicLong>> triple) {
    switch (unFreezeV2.getType()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(unFreezeV2.getUnfreezeAmount());
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        triple.getLeft().getLeft().addAndGet(newNetWeight - oldNetWeight);
        triple.getLeft().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(unFreezeV2.getUnfreezeAmount());
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        triple.getMiddle().getLeft().addAndGet(newEnergyWeight - oldEnergyWeight);
        triple.getMiddle().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(unFreezeV2.getUnfreezeAmount());
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        triple.getRight().getLeft().addAndGet(newTPWeight - oldTPWeight);
        triple.getRight().getRight().addAndGet(unFreezeV2.getUnfreezeAmount());
        break;
      default:
        break;
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java (L1-8)
```java
package org.tron.core.vm.nativecontract;

import static org.tron.core.actuator.ActuatorConstant.ACCOUNT_EXCEPTION_STR;
import static org.tron.core.actuator.ActuatorConstant.NOT_EXIST_STR;
import static org.tron.core.actuator.ActuatorConstant.STORE_NOT_EXIST;
import static org.tron.core.config.Parameter.ChainConstant.TRX_PRECISION;
import static org.tron.protos.contract.Common.ResourceCode.BANDWIDTH;
import static org.tron.protos.contract.Common.ResourceCode.ENERGY;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java (L44-97)
```java
  public Map<String, Long> execute(CancelAllUnfreezeV2Param param, Repository repo) throws ContractExeException {
    Map<String, Long> result = new HashMap<>();
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    long now = repo.getDynamicPropertiesStore().getLatestBlockHeaderTimestamp();
    long withdrawExpireBalance = 0L;
    for (Protocol.Account.UnFreezeV2 unFreezeV2: ownerCapsule.getUnfrozenV2List()) {
      if (unFreezeV2.getUnfreezeExpireTime() > now) {
        String resourceName = unFreezeV2.getType().name();
        result.put(resourceName, result.getOrDefault(resourceName, 0L) + unFreezeV2.getUnfreezeAmount());

        updateFrozenInfoAndTotalResourceWeight(ownerCapsule, unFreezeV2, repo);
      } else {
        // withdraw
        withdrawExpireBalance += unFreezeV2.getUnfreezeAmount();
      }
    }
    if (withdrawExpireBalance > 0) {
      ownerCapsule.setBalance(ownerCapsule.getBalance() + withdrawExpireBalance);
    }
    ownerCapsule.clearUnfrozenV2();

    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);

    result.put(VMConstant.WITHDRAW_EXPIRE_BALANCE, withdrawExpireBalance);
    return result;
  }

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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2115-2155)
```java
  public boolean cancelAllUnfreezeV2Action() {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner, 0, null,
        "cancelAllUnfreezeV2", nonce, null);

    try {
      CancelAllUnfreezeV2Param param = new CancelAllUnfreezeV2Param();
      param.setOwnerAddress(owner);

      CancelAllUnfreezeV2Processor processor = new CancelAllUnfreezeV2Processor();
      processor.validate(param, repository);
      Map<String, Long> result = processor.execute(param, repository);
      repository.commit();

      if (result.get(VMConstant.WITHDRAW_EXPIRE_BALANCE) > 0) {
        increaseNonce();
        addInternalTx(null, owner, owner, result.get(VMConstant.WITHDRAW_EXPIRE_BALANCE), null,
            "withdrawExpireUnfreezeWhileCanceling", nonce, null);
      }

      if (internalTx != null && CommonParameter.getInstance().saveCancelAllUnfreezeV2Details) {
        internalTx.setExtra(String.format("{\"%s\":%d,\"%s\":%d,\"%s\":%d}",
            BANDWIDTH.name(), result.getOrDefault(BANDWIDTH.name(), 0L),
            ENERGY.name(), result.getOrDefault(ENERGY.name(), 0L),
            TRON_POWER.name(), result.getOrDefault(TRON_POWER.name(), 0L)));
      }

      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM CancelAllUnfreezeV2: validate failure. Reason: {}", e.getMessage());
    } catch (ContractExeException e) {
      logger.warn("TVM CancelAllUnfreezeV2: execute failure. Reason: {}", e.getMessage());
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```
