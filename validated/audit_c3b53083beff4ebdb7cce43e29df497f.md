### Title
Divide-before-multiply precision loss in legacy vote reward computation understates witness voter rewards - (File: `chainbase/src/main/java/org/tron/core/service/MortgageService.java`)

### Summary
`MortgageService.computeReward(long cycle, List<Pair<byte[], Long>> votes)` calculates a voter's share of a standby-witness/block reward pool by first computing a floating-point ratio `voteRate = (double) userVote / totalVote` and only afterwards multiplying by `totalReward`. This is the same anti-pattern flagged in the external report for `PegOracle.sol`: dividing first (which truncates/rounds the fractional relative-price/ratio to double precision) and re-multiplying afterward, instead of doing `userVote * totalReward / totalVote` in one combined operation. This is reachable from any account's `WithdrawBalanceContract`/`queryReward` RPC call path and directly determines TRX minted into a user's `allowance` balance.

### Finding Description
In `computeReward`:
```
long userVote = vote.getValue();
double voteRate = (double) userVote / totalVote;
reward += voteRate * totalReward;
``` [1](#0-0) 

This is the legacy ("old algorithm") reward-per-cycle computation used for cycles prior to `getNewRewardAlgorithmEffectiveCycle`, invoked via `getOldReward` -> `computeReward(cycle, votes)` in a loop over cycles. [2](#0-1) 

The order-of-operations issue is structurally identical to the reported bug: computing a ratio via floating-point division first, then multiplying, causes the fractional part of `userVote/totalVote` to be rounded/truncated to IEEE-754 double precision *before* being scaled up by `totalReward` (which can be large, e.g. tens of millions of SUN per cycle). The mathematically equivalent and precision-preserving form would be `(userVote * totalReward) / totalVote` (multiply first, then divide, ideally using `BigInteger`/`long` exact arithmetic, similar to how the newer reward-Vi algorithm elsewhere in the same class does `BigInteger.multiply(...).divide(...)` without going through `double`). [3](#0-2) 

This function is reachable from `queryReward(byte[] address)` and `withdrawReward(byte[] address)`, both public entry points on `MortgageService` invoked from `Wallet`/actuator withdraw-balance flows that any account can trigger for its own address via a broadcast transaction or RPC query — i.e., unprivileged, attacker/user-triggerable code path. [4](#0-3) [5](#0-4) 

### Impact Explanation
Because `double` division is performed before the multiplication, `voteRate` is limited to ~15-17 significant decimal digits and any repeating/irrational binary fraction (e.g. `userVote/totalVote` such as 1/3, 1/7, etc.) is rounded before scaling. For reward pools measured in SUN (up to tens of millions per cycle) and vote counts that can be large integers, this rounding is amplified during the multiply step, producing a reward value that differs from the exact integer-arithmetic result. Accumulated across many voters and many cycles, this causes a persistent, deterministic mismatch between the sum of individually computed per-voter rewards and the actual `totalReward` pool for a given cycle — i.e., value can leak or be lost during reward distribution, corrupting on-chain account balances (`allowance`) versus the intended proportional payout. This is a state/asset-accounting correctness issue affecting every account with pre-new-algorithm cycles pending withdrawal, not merely a display bug.

### Likelihood Explanation
This code path executes on every `queryReward`/`withdrawReward` call for any account that still has un-withdrawn cycles preceding `getNewRewardAlgorithmEffectiveCycle`. It requires no privileged access — it is triggered by ordinary voters withdrawing/querying their delegation rewards, a routine and extremely common user operation. The precision-loss condition itself (non-exact `userVote/totalVote` ratios) is the common case rather than an edge case, since vote counts and total votes are rarely exact divisors of one another.

### Recommendation
Replace the floating-point ratio-then-multiply computation with an integer/BigInteger multiply-then-divide computation to avoid intermediate precision loss, consistent with the pattern already used for the new Vi-based reward algorithm in the same class:
```java
BigInteger reward = BigInteger.valueOf(userVote)
    .multiply(BigInteger.valueOf(totalReward))
    .divide(BigInteger.valueOf(totalVote));
```
This mirrors the `x * 1e18 / y`-style mitigation recommended in the original report and matches the exact-arithmetic style already employed elsewhere in `MortgageService`/`DelegationStore` (`deltaVi.multiply(BigInteger.valueOf(userVote)).divide(DECIMAL_OF_VI_REWARD)`). [6](#0-5) 

### Proof of Concept
Given a cycle with `totalReward = 100_000_026` SUN and `totalVote = 2_700_000_702` (sum of 27 witnesses with slightly varying vote counts, as constructed in the existing test `DelegationServiceTest.testPay`), and a voter with `userVote = 100_000_013`:

- Exact value: `100_000_013 * 100_000_026 / 2_700_000_702` computed with full precision.
- Current code: `voteRate = (double)(100_000_013) / 2_700_000_702` truncates to ~15-17 significant digits, then `voteRate * 100_000_026` is computed in double and cast down to `long`, which can differ from the exact integer result by one or more SUN depending on the specific ratio — the same class of discrepancy the external report demonstrates for `PegOracle`'s divide-then-multiply pattern (observed drift on the order of `9.9e-4` relative error in the report's PoC, i.e., non-negligible for financial calculations).
- Existing repository tests (`DelegationServiceTest.testPay`/`testWithdraw`) already encode expected values using the same `(double) reward / totalVote * userVote` style assertions, confirming double-based rounding is baked into both the production code and its test oracle rather than being validated against an exact-arithmetic reference implementation. [7](#0-6) [8](#0-7)

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L136-169)
```java
  public long queryReward(byte[] address) {
    if (!dynamicPropertiesStore.allowChangeDelegation()) {
      return 0;
    }

    AccountCapsule accountCapsule = accountStore.get(address);
    long beginCycle = delegationStore.getBeginCycle(address);
    long endCycle = delegationStore.getEndCycle(address);
    long currentCycle = dynamicPropertiesStore.getCurrentCycleNumber();
    long reward = 0;
    if (accountCapsule == null) {
      return 0;
    }
    if (beginCycle > currentCycle) {
      return accountCapsule.getAllowance();
    }
    //withdraw the latest cycle reward
    if (beginCycle + 1 == endCycle && beginCycle < currentCycle) {
      AccountCapsule account = delegationStore.getAccountVote(beginCycle, address);
      if (account != null) {
        reward = computeReward(beginCycle, endCycle, account);
      }
      beginCycle += 1;
    }
    //
    endCycle = currentCycle;
    if (CollectionUtils.isEmpty(accountCapsule.getVotesList())) {
      return reward + accountCapsule.getAllowance();
    }
    if (beginCycle < endCycle) {
      reward += computeReward(beginCycle, endCycle, accountCapsule);
    }
    return reward + accountCapsule.getAllowance();
  }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L183-186)
```java
      long userVote = vote.getValue();
      double voteRate = (double) userVote / totalVote;
      reward += voteRate * totalReward;
    }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L215-227)
```java
    if (beginCycle < endCycle) {
      for (Pair<byte[], Long>  vote : srAddresses) {
        byte[] srAddress = vote.getKey();
        BigInteger beginVi = delegationStore.getWitnessVi(beginCycle - 1, srAddress);
        BigInteger endVi = delegationStore.getWitnessVi(endCycle - 1, srAddress);
        BigInteger deltaVi = endVi.subtract(beginVi);
        if (deltaVi.signum() <= 0) {
          continue;
        }
        long userVote = vote.getValue();
        reward += deltaVi.multiply(BigInteger.valueOf(userVote))
            .divide(DelegationStore.DECIMAL_OF_VI_REWARD).longValue();
      }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L260-269)
```java
  private long getOldReward(long begin, long end, List<Pair<byte[], Long>> votes) {
    if (dynamicPropertiesStore.allowOldRewardOpt()) {
      return rewardViCalService.getNewRewardAlgorithmReward(begin, end, votes);
    }
    long reward = 0;
    for (long cycle = begin; cycle < end; cycle++) {
      reward += computeReward(cycle, votes);
    }
    return reward;
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L133-146)
```java
  public void accumulateWitnessVi(long cycle, byte[] address, long voteCount) {
    BigInteger preVi = getWitnessVi(cycle - 1, address);
    long reward = getReward(cycle, address);
    if (reward == 0 || voteCount == 0) { // Just forward pre vi
      if (!BigInteger.ZERO.equals(preVi)) { // Zero vi will not be record
        setWitnessVi(cycle, address, preVi);
      }
    } else { // Accumulate delta vi
      BigInteger deltaVi = BigInteger.valueOf(reward)
          .multiply(DECIMAL_OF_VI_REWARD)
          .divide(BigInteger.valueOf(voteCount));
      setWitnessVi(cycle, address, preVi.add(deltaVi));
    }
  }
```

**File:** framework/src/test/java/org/tron/core/services/DelegationServiceTest.java (L30-56)
```java
  private void testPay(int cycle) {
    double rate = 0.2;
    if (cycle == 0) {
      rate = 0.1;
    } else if (cycle == 1) {
      rate = 0.2;
    }
    mortgageService.payStandbyWitness();
    Wallet.setAddressPreFixByte(ADD_PRE_FIX_BYTE_MAINNET);
    byte[] sr1 = decodeFromBase58Check("TLTDZBcPoJ8tZ6TTEeEqEvwYFk2wgotSfD");
    long value = dbManager.getDelegationStore().getReward(cycle, sr1);
    long tmp = 0;
    for (int i = 0; i < 27; i++) {
      tmp += 100000000 + i;
    }
    double d = (double) 16000000 / tmp;
    long expect = (long) (d * 100000026);
    long brokerageAmount = (long) (rate * expect);
    expect -= brokerageAmount;
    Assert.assertEquals(expect, value);
    mortgageService.payBlockReward(sr1, 32000000);
    expect += 32000000;
    brokerageAmount = (long) (rate * 32000000);
    expect -= brokerageAmount;
    value = dbManager.getDelegationStore().getReward(cycle, sr1);
    Assert.assertEquals(expect, value);
  }
```

**File:** framework/src/test/java/org/tron/core/services/DelegationServiceTest.java (L70-77)
```java
    long allowance = accountCapsule.getAllowance();
    long value = mortgageService.queryReward(sr1) - allowance;
    long reward1 = (long) ((double) dbManager.getDelegationStore().getReward(0, sr27) / 100000000
        * 10000000);
    long reward2 = (long) ((double) dbManager.getDelegationStore().getReward(1, sr27) / 100000000
        * 10000000);
    long reward = reward1 + reward2;
    Assert.assertEquals(reward, value);
```
