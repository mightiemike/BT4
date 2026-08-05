## Analog Found

### Title
Unfreeze cooldown (`UNFREEZE_DELAY_DAYS`) is locked-in at unfreeze time and not updated when the parameter is later strengthened - (File: `actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java`)

### Summary
The reported bug is a "start the timer while cooldown is weak, exploit it after cooldown is strengthened" class of issue. The java-tron analog exists in the resource unfreezing (staking v2) flow: `UnfreezeBalanceV2Actuator` computes and permanently stores an unfreeze expiration time using whatever value `UNFREEZE_DELAY_DAYS` has *at the moment of unfreezing*. If the committee later increases `UNFREEZE_DELAY_DAYS` via a governance proposal to lengthen the cooldown, any `UnFreezeV2` entries already queued keep their original (shorter) expiry and are unaffected by the stronger cooldown.

### Finding Description
`UnfreezeBalanceV2Actuator.calcUnfreezeExpireTime(now)` reads the current `UNFREEZE_DELAY_DAYS` chain parameter and bakes it into the stored expire time for the unfreeze record: [1](#0-0) 

This value is written into the account's `UnFreezeV2` list at execution time: [2](#0-1) 

The only gate on submitting an unfreeze transaction is that `UNFREEZE_DELAY_DAYS` currently be greater than 0 (`supportUnfreezeDelay()`): [3](#0-2) [4](#0-3) 

`UNFREEZE_DELAY_DAYS` is a normal proposal-controlled chain parameter that the committee can change (increase) at any later time via `ProposalService.process`: [5](#0-4) 

Once an `UnFreezeV2` entry's `unfreezeExpireTime` has been computed and stored, no later logic retroactively recalculates or extends it based on a subsequently increased `UNFREEZE_DELAY_DAYS`. The withdrawal path (`WithdrawExpireUnfreezeActuator`/`WithdrawExpireUnfreezeProcessor`) simply checks whether the stored `unfreezeExpireTime` has already passed `now`, independent of the current chain parameter value: [6](#0-5) [7](#0-6) 

This mirrors the reported bug class exactly: a user "starts the withdrawal timer" (here, calls `UnfreezeBalanceV2`) while the cooldown parameter is at a weaker value, permanently locking in the weaker cooldown for that batch of frozen TRX, and is unaffected by the committee subsequently increasing the cooldown to strengthen it protocol-wide.

### Impact Explanation
Users who anticipate (or observe, since proposals are on-chain and have a voting/maintenance-cycle delay before taking effect) an upcoming increase to `UNFREEZE_DELAY_DAYS` can front-run the change by unfreezing their BANDWIDTH/ENERGY/TRON_POWER stakes just before it takes effect. Those unfreeze requests will use the old, shorter delay and become withdrawable earlier than newly-created unfreeze requests, bypassing the strengthened cooldown intent for that specific balance. This is an economic/staking-security bypass (early unstake), not a direct fund-theft bug, matching a Medium-severity "invalid-state / cooldown-bypass" class of impact.

### Likelihood Explanation
Any account holder can trigger `UnfreezeBalanceV2Contract` at will (no privileged role required) as long as they hold frozen balance and `UNFREEZE_DELAY_DAYS > 0`. Governance parameter changes such as `UNFREEZE_DELAY_DAYS` are proposed and voted on-chain, so an attentive user can observe an approaching increase and race to unfreeze beforehand, making this a realistically reachable (not purely theoretical) scenario for anyone actively staking/unstaking TRX.

### Recommendation
When applying a `UNFREEZE_DELAY_DAYS` increase via `ProposalService`, either (a) recompute/extend the `unfreezeExpireTime` of all pending `UnFreezeV2` entries to reflect the new stricter cooldown, or (b) explicitly document/accept that already-queued unfreeze entries are governed by the delay in effect at submission time and are not retroactively affected — and if the latter is the intended design, ensure this is clearly stated as expected behavior rather than a bypass, since it currently allows silent front-running of parameter changes.

### Proof of Concept
1. `UNFREEZE_DELAY_DAYS = D1` (small value, already > 0 so `UnfreezeBalanceV2Contract` is enabled).
2. Attacker observes a proposal on-chain to raise `UNFREEZE_DELAY_DAYS` to `D2 > D1`.
3. Before the proposal takes effect (during the maintenance cycle delay), attacker calls `UnfreezeBalanceV2Contract`, which stores `unfreezeExpireTime = now + D1 * FROZEN_PERIOD` via `calcUnfreezeExpireTime` [1](#0-0) .
4. Proposal takes effect, `UNFREEZE_DELAY_DAYS` becomes `D2` via `ProposalService` [8](#0-7) .
5. At `now + D1 * FROZEN_PERIOD` (still less than what `D2` would have required), the attacker calls `WithdrawExpireUnfreezeContract` and successfully withdraws the balance, since the stored expire time is checked, not the current `UNFREEZE_DELAY_DAYS` [6](#0-5) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L83-88)
```java
    ResourceCode freezeType = unfreezeBalanceV2Contract.getResource();

    long expireTime = this.calcUnfreezeExpireTime(now);
    accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime);

    this.updateTotalResourceWeight(accountCapsule, unfreezeBalanceV2Contract, unfreezeBalance);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L119-122)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support UnfreezeV2 transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L229-234)
```java
  public long calcUnfreezeExpireTime(long now) {
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    long unfreezeDelayDays = dynamicStore.getUnfreezeDelayDays();

    return now + unfreezeDelayDays * FROZEN_PERIOD;
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2803-2812)
```java
  public long getUnfreezeDelayDays() {
    return Optional.ofNullable(getUnchecked(UNFREEZE_DELAY_DAYS))
            .map(BytesCapsule::getData)
            .map(ByteArray::toLong)
            .orElseThrow(() -> new IllegalArgumentException("not found UNFREEZE_DELAY_DAYS"));
  }

  public boolean supportUnfreezeDelay() {
    return getUnfreezeDelayDays() > 0;
  }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L303-317)
```java
        case UNFREEZE_DELAY_DAYS: {
          DynamicPropertiesStore dynamicStore = manager.getDynamicPropertiesStore();
          dynamicStore.saveUnfreezeDelayDays(entry.getValue());
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.FreezeBalanceV2Contract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.UnfreezeBalanceV2Contract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.WithdrawExpireUnfreezeContract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.DelegateResourceContract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.UnDelegateResourceContract_VALUE);
          break;
        }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L107-119)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    List<UnFreezeV2> unfrozenV2List = accountCapsule.getInstance().getUnfrozenV2List();
    long totalWithdrawUnfreeze = getTotalWithdrawUnfreeze(unfrozenV2List, now);
    if (totalWithdrawUnfreeze <= 0) {
      throw new ContractValidateException("no unFreeze balance to withdraw ");
    }
    try {
      LongMath.checkedAdd(accountCapsule.getBalance(), totalWithdrawUnfreeze);
    } catch (ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
    return true;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java (L42-55)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    List<Protocol.Account.UnFreezeV2> unfrozenV2List = accountCapsule.getInstance()
        .getUnfrozenV2List();
    long totalWithdrawUnfreeze = getTotalWithdrawUnfreeze(unfrozenV2List, now);
    if (totalWithdrawUnfreeze < 0) {
      throw new ContractValidateException("no unFreeze balance to withdraw ");
    }
    try {
      LongMath.checkedAdd(accountCapsule.getBalance(), totalWithdrawUnfreeze);
    } catch (ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
  }
```
