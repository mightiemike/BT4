### Title
Missing Minimum Lock-Duration Check in `FreezeBalanceV2Actuator` Grants Instant, Uncommitted Voting Power - (File: `actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java`)

### Summary
The reported bug is a class of vulnerability where voting power is derived purely from stake *amount*, with no enforcement that the stake corresponds to a genuinely time-locked commitment. In java-tron's current ("V2") freeze/vote/unfreeze pipeline, `FreezeBalanceV2Actuator` grants `TRON_POWER` (and therefore voting weight) the instant TRX is frozen, with **no minimum lock-duration validation at all** — unlike the legacy `FreezeBalanceActuator` (V1), which explicitly enforces `frozenDuration >= minFrozenTime`. `UnfreezeBalanceV2Actuator` likewise imposes no minimum holding period before a user may begin unfreezing; it only delays the *return of principal* by `unfreezeDelayDays`, not the ability to unstake/reduce voting commitment. This mirrors the root cause of the veGUAN finding: voting power is computed from current stake state without validating that the lock/commitment period was actually honored.

### Finding Description
`FreezeBalanceV2Actuator.validate()` checks only that `frozenBalance` is positive and ≥ 1 TRX and ≤ account balance — there is no lock-duration parameter or minimum-time check at all: [1](#0-0) 

Compare this to the legacy `FreezeBalanceActuator`, which enforces a minimum/maximum frozen duration before allowing the freeze: [2](#0-1) 

Once `supportUnfreezeDelay()` (i.e. FreezeV2 mode) is active, the legacy V1 actuator is explicitly disabled, so V2's un-time-locked freeze becomes the only path: [3](#0-2) 

`FreezeBalanceV2Actuator.execute()` immediately increases `TotalTronPowerWeight`/`FrozenForTronPowerV2` on the account as soon as the freeze transaction is applied, i.e. voting power is granted instantaneously with no commitment period: [4](#0-3) 

Vote weight validation (`VoteWitnessActuator`/`VoteWitnessProcessor`) reads this freshly-updated `TronPower`/`AllTronPower` value with no delay or lock check whatsoever: [5](#0-4) [6](#0-5) 

Symmetrically, `UnfreezeBalanceV2Actuator.validate()` only checks that a non-zero frozen balance of the given resource type exists (`checkExistFrozenBalance`) and that the requested unfreeze amount doesn't exceed it — there is **no minimum elapsed-time-since-freeze check**, so a user can unfreeze in the very next block after freezing: [7](#0-6) 

The only delay imposed is on the *return of the underlying TRX balance*, which is deferred by `unfreezeDelayDays` via `calcUnfreezeExpireTime`: [8](#0-7) 

This means a user can: (1) freeze TRX for `TRON_POWER` — instantly gaining voting weight equal to `getAllTronPower()`, (2) cast `VoteWitnessContract` votes using that freshly acquired power (validated only against current `TronPower`, with no lock-duration requirement), and (3) immediately submit `UnfreezeBalanceV2Contract` to begin unstaking that same TRX, with the only cost being that the principal itself is locked for `unfreezeDelayDays` before withdrawal — not before the vote could be cast or before unfreezing could be initiated.

### Impact Explanation
This directly parallels the veGUAN root cause: voting power is derived from raw stake amount without validating that a genuine lock commitment backs it. In java-tron's design, an attacker can obtain voting power for governance-relevant witness elections without any minimum staking duration, undermining the intended "skin in the game" property of TRON's Proof-of-Stake voting (unlike V1, which enforced `minFrozenTime`/`maxFrozenTime`). Because witness vote tallies (`countVote`/maintenance cycle logic) are computed from stored `VotesCapsule` deltas, votes cast using such transient stake persist in the vote count for the current maintenance cycle even after the user starts unfreezing.

### Likelihood Explanation
Medium-to-Low. Unlike a true zero-cost flashloan attack, the attacker's principal remains inaccessible for `unfreezeDelayDays` (a real capital cost), because `UnfreezeBalanceV2Actuator` only removes the resource/vote weight — it does not return the TRX balance until the delayed unfreeze expires. This breaks a single-transaction, capital-free flashloan exploit. However, the underlying design flaw — instantaneous, uncommitted voting power acquisition via `FreezeBalanceV2Actuator` with zero minimum lock duration, contrasted with V1's explicit `minFrozenTime` enforcement — is a genuine and reachable divergence that removes a real safeguard the original design relied on, and is exploitable by any unprivileged account holding sufficient TRX for the duration of a maintenance cycle (not requiring council/privileged access).

### Recommendation
Reintroduce a minimum lock-duration requirement in `FreezeBalanceV2Actuator.validate()` (analogous to the removed `minFrozenTime`/`maxFrozenTime` check in the V1 actuator), and/or require that `UnfreezeBalanceV2Actuator` reject unfreeze requests submitted before a minimum elapsed time since the corresponding freeze, so that voting power cannot be acquired and relinquished without a genuine minimum-duration commitment.

### Proof of Concept
1. Attacker account funds TRX balance (own or borrowed for a period ≥ `unfreezeDelayDays`).
2. Submit `FreezeBalanceV2Contract` with `resource = TRON_POWER` — `FreezeBalanceV2Actuator.execute()` instantly adds to `TotalTronPowerWeight` and the account's `AllTronPower`.
3. In the same or next block, submit `VoteWitnessContract` voting up to the new `TronPower` — validated instantly against the freshly frozen balance with no lock-duration check.
4. Immediately submit `UnfreezeBalanceV2Contract` for the same resource — accepted because `checkExistFrozenBalance` only checks that frozen amount > 0, with no minimum elapsed-time-since-freeze requirement; this begins the unfreeze countdown (`unfreezeDelayDays`) but the vote already cast remains recorded in `VotesStore` for the ongoing maintenance/vote-tally cycle.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L73-88)
```java
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(frozenBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    accountCapsule.setBalance(newBalance);
    accountStore.put(accountCapsule.createDbKey(), accountCapsule);

    ret.setStatus(fee, code.SUCESS);

    return true;
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L131-141)
```java
    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("frozenBalance must be positive");
    }
    if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("frozenBalance must be greater than or equal to 1 TRX");
    }

    if (frozenBalance > accountCapsule.getBalance()) {
      throw new ContractValidateException("frozenBalance must be less than or equal to accountBalance");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L203-214)
```java
    long frozenDuration = freezeBalanceContract.getFrozenDuration();
    long minFrozenTime = dynamicStore.getMinFrozenTime();
    long maxFrozenTime = dynamicStore.getMaxFrozenTime();

    boolean needCheckFrozeTime = CommonParameter.getInstance()
        .getCheckFrozenTime() == 1;//for test
    if (needCheckFrozeTime && !(frozenDuration >= minFrozenTime
        && frozenDuration <= maxFrozenTime)) {
      throw new ContractValidateException(
          "frozenDuration must be less than " + maxFrozenTime + " days "
              + "and more than " + minFrozenTime + " days");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L271-274)
```java
    if (dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException(
              "freeze v2 is open, old freeze is closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VoteWitnessActuator.java (L129-143)
```java
      long tronPower;
      DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
      if (dynamicStore.supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }

      sum = LongMath
          .checkedMultiply(sum, TRX_PRECISION); //trx -> drop. The vote count is based on TRX
      if (sum > tronPower) {
        throw new ContractValidateException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/VoteWitnessProcessor.java (L88-100)
```java
      long tronPower;
      if (repo.getDynamicPropertiesStore().supportUnfreezeDelay()
          && repo.getDynamicPropertiesStore().supportAllowNewResourceModel()) {
        tronPower = accountCapsule.getAllTronPower();
      } else {
        tronPower = accountCapsule.getTronPower();
      }
      sum =  LongMath.checkedMultiply(sum, TRX_PRECISION);
      if (sum > tronPower) {
        throw new ContractExeException(
            "The total number of votes[" + sum + "] is greater than the tronPower[" + tronPower
                + "]");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L144-177)
```java
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    switch (unfreezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        if (!checkExistFrozenBalance(accountCapsule, BANDWIDTH)) {
          throw new ContractValidateException("no frozenBalance(BANDWIDTH)");
        }
        break;
      case ENERGY:
        if (!checkExistFrozenBalance(accountCapsule, ENERGY)) {
          throw new ContractValidateException("no frozenBalance(Energy)");
        }
        break;
      case TRON_POWER:
        if (dynamicStore.supportAllowNewResourceModel()) {
          if (!checkExistFrozenBalance(accountCapsule, TRON_POWER)) {
            throw new ContractValidateException("no frozenBalance(TronPower)");
          }
        } else {
          throw new ContractValidateException("ResourceCode error.valid ResourceCode[BANDWIDTH、Energy]");
        }
        break;
      default:
        if (dynamicStore.supportAllowNewResourceModel()) {
          throw new ContractValidateException("ResourceCode error.valid ResourceCode[BANDWIDTH、Energy、TRON_POWER]");
        } else {
          throw new ContractValidateException("ResourceCode error.valid ResourceCode[BANDWIDTH、Energy]");
        }
    }

    if (!checkUnfreezeBalance(accountCapsule, unfreezeBalanceV2Contract, unfreezeBalanceV2Contract.getResource())) {
      throw new ContractValidateException(
          "Invalid unfreeze_balance, [" + unfreezeBalanceV2Contract.getUnfreezeBalance() + "] is error"
      );
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
