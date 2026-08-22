### Title
Users with matured unfreeze balances can be permanently blocked from withdrawing via the `supportUnfreezeDelay()` flag - (File: `actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java`)

### Summary
`UnfreezeBalanceV2Actuator` (the "request" step, which locks tokens and schedules an unfreeze-expire time) and `WithdrawExpireUnfreezeActuator` (the "finalize/claim" step, which lets a user pull out the balance once it has matured) both gate on the exact same committee-controlled boolean-like flag, `DynamicPropertiesStore.supportUnfreezeDelay()`. This mirrors the reported bug class: a single "feature enabled" flag is checked both at request time and at final-claim time, so toggling the flag after users have already queued withdrawals can strand their already-matured funds.

### Finding Description
`UnfreezeBalanceV2Actuator.validate()` requires the flag to be on before a user can create an `UnFreezeV2` entry (the "request"): [1](#0-0) 

`WithdrawExpireUnfreezeActuator.validate()` re-checks the identical flag before letting the user pull out the funds that have already matured (i.e. `unfreezeExpireTime <= now`): [2](#0-1) 

The flag itself is a committee/governance-controlled chain parameter (`UNFREEZE_DELAY_DAYS`), toggled through proposals: [3](#0-2) 

Because both the "start" actuator (`UnfreezeBalanceV2Actuator`) and the "finalize" actuator (`WithdrawExpireUnfreezeActuator`) share the same gate, once a user has already unfrozen tokens (creating `UnFreezeV2` entries with a set `unfreezeExpireTime`) and the committee later disables `supportUnfreezeDelay` (e.g., sets `UNFREEZE_DELAY_DAYS` back to 0 for a protocol migration or rollback), the user's already-matured balance sitting in `AccountCapsule.getUnfrozenV2List()` becomes unclaimable — `WithdrawExpireUnfreezeContract` transactions will throw `ContractValidateException("Not support WithdrawExpireUnfreeze transaction, need to be opened by the committee")` even though the funds have already passed their unfreeze period and morally belong to the user. Notably, `CancelAllUnfreezeV2Actuator`, which also drains `unfrozenV2List` and can release matured balances, does not carry this same flag check, underscoring the inconsistency between the request/finalize checks that this flag pattern introduces.

### Impact Explanation
If the flag is toggled off after users have unfrozen tokens but before they call `WithdrawExpireUnfreezeContract`, those users cannot retrieve the already-matured TRX through the normal withdraw path; their balance is effectively locked at the protocol level until the committee re-enables the flag. This is a fund-availability/DoS issue affecting any account with pending, matured unfreeze entries — reachable purely through normal broadcast transactions (`UnfreezeBalanceV2Contract` then `WithdrawExpireUnfreezeContract`) with no privileged access required by the affected user.

### Likelihood Explanation
This requires a super-representative/committee proposal action (`UNFREEZE_DELAY_DAYS` toggle) to occur, which is a governance decision rather than something an attacker directly triggers. However, unlike a purely "privileged actor breaks their own system" scenario, the harm here falls on unprivileged end users who already had legitimately queued withdrawals — exactly the report's bug class (a state-gating flag reused for both request and claim actions). The likelihood of the specific committee-flag flip is low/environment-dependent, but the code-level defect (same flag reused across both lifecycle stages) is definite and directly analogous to the reported issue.

### Recommendation
Do not gate `WithdrawExpireUnfreezeActuator` (and `WithdrawExpireUnfreezeProcessor` used by the TVM native contract path) on `supportUnfreezeDelay()`. Once a user's `UnFreezeV2` entry has matured, the withdrawal of an already-processed amount should not depend on whether new unfreeze requests are currently allowed. Use a separate, additive-only flag (or no flag at all) for the finalize/claim step, so that disabling new unfreeze requests never blocks users from retrieving already-matured balances.

### Proof of Concept
1. Committee enables `UNFREEZE_DELAY_DAYS` (sets it > 0), enabling `supportUnfreezeDelay()`.
2. User A broadcasts `UnfreezeBalanceV2Contract`; `UnfreezeBalanceV2Actuator.execute()` adds an `UnFreezeV2` entry with `unfreezeExpireTime = now + unfreezeDelayDays*FROZEN_PERIOD` ( [4](#0-3) ).
3. Before the expire time passes, committee submits a proposal setting `UNFREEZE_DELAY_DAYS` back to 0, disabling `supportUnfreezeDelay()`.
4. Time passes; User A's unfreeze entry matures (`unfreezeExpireTime <= now`).
5. User A broadcasts `WithdrawExpireUnfreezeContract` to claim the matured balance; `WithdrawExpireUnfreezeActuator.validate()` throws `ContractValidateException` at the `supportUnfreezeDelay()` check ( [2](#0-1) ), and the matured balance remains stuck in `unfrozenV2List` indefinitely until the committee re-enables the flag.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L85-86)
```java
    long expireTime = this.calcUnfreezeExpireTime(now);
    accountCapsule.addUnfrozenV2List(freezeType, unfreezeBalance, expireTime);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceV2Actuator.java (L119-122)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support UnfreezeV2 transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WithdrawExpireUnfreezeActuator.java (L84-87)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support WithdrawExpireUnfreeze transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L1-1)
```java
package org.tron.core.utils;
```
