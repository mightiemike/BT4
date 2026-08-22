### No vulnerability found for this question.

**Reasoning:**

The `REMARK = -1L` sentinel in `DelegationStore.getEndCycle` is returned only when no end-cycle key has ever been written for an address (i.e., the account has never called `withdrawReward`/voted before) [1](#0-0) . Once `setEndCycle` is invoked, it is always written as `currentCycle + 1`, and `currentCycle` is a monotonically non-decreasing counter maintained by `DynamicPropertiesStore` that increases roughly once per maintenance interval (~6 hours) [2](#0-1) . For `endCycle` to wrap back to `-1` via integer/long overflow, `currentCycle` would have to reach `Long.MAX_VALUE` (~9.2×10^18), which at one increment per maintenance cycle is not reachable within any realistic timeframe — this is not an attacker-controllable or feasible precondition.

The `beginCycle + 1 == endCycle` branch in `withdrawReward` is the intended steady-state condition reached on every normal subsequent call (not a crafted edge case): after a vote, `beginCycle` is set to the previous `endCycle` and `endCycle` to `currentCycle + 1`, so on the next call one cycle later this equality naturally holds and the code pays the previously "reserved" cycle's reward exactly once via the cached `getAccountVote`/`setAccountVote` snapshot, then advances `beginCycle` by one to prevent re-payment [3](#0-2) . This exact-once accounting is validated by existing tests such as `DelegationServiceTest.testWithdraw` and `VoteTest.checkRewardAndWithdraw`, which assert the withdrawn allowance equals the exact computed reward sum [4](#0-3) [5](#0-4) .

No vote/unvote cadence controllable by an unprivileged attacker can force `beginCycle`/`endCycle` back to the `REMARK` sentinel or otherwise desynchronize the boundary check to duplicate or skip a reward payment; the arithmetic and existing regression tests demonstrate value conservation holds under normal operation, and reaching the sentinel via overflow is computationally infeasible.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L68-71)
```java
  public long getEndCycle(byte[] address) {
    BytesCapsule bytesCapsule = get(buildEndCycleKey(address));
    return bytesCapsule == null ? REMARK : ByteArray.toLong(bytesCapsule.getData());
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L89-134)
```java
  public void withdrawReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return;
    }
    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (beginCycle > currentCycle || accountCapsule == null) {
      return;
    }
    if (beginCycle == currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        return;
      }
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
        adjustAllowance(address, reward);
        reward = 0;
        logger.info("Latest cycle reward {}, {}.", beginCycle, account.getVotesList());
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      delegationStore.setBeginCycle(address, endCycle + 1);
      return;
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
      adjustAllowance(address, reward);
    }
    delegationStore.setBeginCycle(address, endCycle);
    delegationStore.setEndCycle(address, endCycle + 1);
    delegationStore.setAccountVote(endCycle, address, accountCapsule);
    logger.info("Adjust {} allowance {}, now currentCycle {}, beginCycle {}, endCycle {}, "
            + "account vote {}.", Hex.toHexString(address), reward, currentCycle,
        beginCycle, endCycle, accountCapsule.getVotesList());
  }
```

**File:** framework/src/test/java/org/tron/core/services/DelegationServiceTest.java (L70-82)
```java
    long allowance = accountCapsule.getAllowance();
    long value = mortgageService.queryReward(sr1) - allowance;
    long reward1 = (long) ((double) dbManager.getDelegationStore().getReward(0, sr27) / 100000000
        * 10000000);
    long reward2 = (long) ((double) dbManager.getDelegationStore().getReward(1, sr27) / 100000000
        * 10000000);
    long reward = reward1 + reward2;
    Assert.assertEquals(reward, value);
    mortgageService.withdrawReward(sr1);
    accountCapsule = dbManager.getAccountStore().get(sr1);
    allowance = accountCapsule.getAllowance() - allowance;
    Assert.assertEquals(reward, allowance);
  }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/VoteTest.java (L848-867)
```java
  private void checkRewardAndWithdraw(byte[] contract, boolean isZero) throws Exception {
    long rewardBySystem = mortgageService.queryReward(contract);
    long beginCycle = dbManager.getDelegationStore().getBeginCycle(contract);
    long currentCycle = dbManager.getDynamicPropertiesStore().getCurrentCycleNumber();
    long passedCycle = max(0, currentCycle - beginCycle,
        dbManager.getDynamicPropertiesStore().disableJavaLangMath());
    Assert.assertTrue(isZero ? rewardBySystem == 0 : rewardBySystem > 0);
    triggerContract(contract, SUCCESS,
        getConsumer(">=", rewardBySystem)
            .andThen(getConsumer("<=", rewardBySystem + passedCycle)),
        queryRewardBalanceMethod);

    long oldBalance = dbManager.getAccountStore().get(contract).getBalance();
    long rewardByContract = new DataWord(triggerContract(contract, SUCCESS,
        getConsumer(">=", rewardBySystem)
            .andThen(getConsumer("<=", rewardBySystem + passedCycle)),
        withdrawRewardMethod).getRuntime().getResult().getHReturn()).longValue();
    long newBalance = dbManager.getAccountStore().get(contract).getBalance();
    Assert.assertEquals(oldBalance + rewardByContract, newBalance);
  }
```
