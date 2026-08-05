### Title
Dynamic energy factor catch-up applies newly-proposed rate parameters retroactively to unaccrued past cycles - (File: chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java)

### Summary
`ContractStateCapsule#catchUpToCycle` lazily "accrues" a contract's dynamic-energy-price factor for every cycle that has passed since it was last touched. It always uses the *current* values of `DYNAMIC_ENERGY_THRESHOLD`, `DYNAMIC_ENERGY_INCREASE_FACTOR`, and `DYNAMIC_ENERGY_MAX_FACTOR` to replay the whole missed period, instead of the values that were in effect during each of those past cycles. This is the same bug class as the reported `setDebtInterestApr` finding: a rate parameter is changed by governance without first accruing/settling the pending state, so the new rate is applied retroactively to a window it was never meant to cover.

### Finding Description
`ContractStateCapsule` stores, per contract, `updateCycle` and `energyFactor` — the current dynamic-energy multiplier used to price TVM execution for congested contracts (`DYNAMIC_ENERGY_THRESHOLD/INCREASE_FACTOR/MAX_FACTOR`). [1](#0-0) 

Because contracts are only touched (and thus only "accrued") when they are invoked, `catchUpToCycle` computes the number of cycles between `lastCycle` (`updateCycle`) and `newCycle` (the current cycle) and replays both the "increase" step and the exponential "decrease" step for that entire gap in a single call: [2](#0-1) 

The parameters used for that replay (`threshold`, `increaseFactor`, `maxFactor`) are always read from `DynamicPropertiesStore` **at the moment the catch-up runs**, via the convenience overload: [3](#0-2) 

These same dynamic properties are mutated directly and immediately by governance proposals in `ProposalService`, with no code path that first walks every outstanding `ContractStateCapsule` forward (i.e., no global "accrue()" is performed before the rate changes): [4](#0-3) 

As a result, if the committee changes `DYNAMIC_ENERGY_INCREASE_FACTOR` or `DYNAMIC_ENERGY_MAX_FACTOR` (or `DYNAMIC_ENERGY_THRESHOLD`), any contract that has not been invoked since before the change will have its entire un-accrued history (potentially many cycles that elapsed under the *old* parameter) recomputed with the *new* parameter the next time it is invoked, in `catchUpToCycle`. This mirrors exactly the reported analog: the new "rate" (increase/decrease factor) is applied retroactively to a period during which the old rate should have governed.

### Impact Explanation
The dynamic energy factor directly multiplies the energy price charged for executing a contract (an underpriced/overpriced-public-work style accounting mechanism). Because the catch-up is lazy and per-contract, two contracts that were equally congested under the old parameters can end up with materially different `energyFactor` values purely depending on when (relative to a parameter-change proposal taking effect) they happen to be next invoked — some will have the old rate compounded over the gap, others the new rate. This causes divergent, unfair, and non-deterministic-feeling fee/pricing outcomes across contracts for the exact same historical congestion window, i.e., an accounting/state divergence, analogous to the "borrowers can incur more debt than they should" impact called out in the original finding (here it is "contracts can be charged more/less energy-factor premium than they should" for a period that occurred under a different governance-set rate).

### Likelihood Explanation
This triggers under an entirely realistic, permissionless flow: any TVM contract that is dynamically energy-priced and goes idle across a committee proposal that adjusts `DYNAMIC_ENERGY_INCREASE_FACTOR`/`DYNAMIC_ENERGY_MAX_FACTOR`/`DYNAMIC_ENERGY_THRESHOLD` will hit this on its next unprivileged call from any user, with no special preconditions other than normal contract usage patterns and a governance vote (which is a standard/expected event on java-tron). No attacker action or special privilege is required to observe the divergence.

### Recommendation
Before (or as part of) applying `DYNAMIC_ENERGY_THRESHOLD`, `DYNAMIC_ENERGY_INCREASE_FACTOR`, or `DYNAMIC_ENERGY_MAX_FACTOR` proposals in `ProposalService`, the currently effective parameters should be snapshotted (e.g., keyed by cycle, similar to how `ENERGY_FEE`/`TRANSACTION_FEE`/`MEMO_FEE` already maintain a price history string) so that `ContractStateCapsule#catchUpToCycle` can replay each historical cycle with the parameter values that were actually in force during that cycle, rather than always using the parameter values current at call time.

### Proof of Concept
1. At cycle N, committee proposal sets `DYNAMIC_ENERGY_INCREASE_FACTOR = X`.
2. Contract A is invoked at cycle N (its `updateCycle` becomes N, using factor X for that step).
3. At cycle N+1, a new proposal changes `DYNAMIC_ENERGY_INCREASE_FACTOR = Y` (`Y != X`) via `ProposalService.process` → `saveDynamicEnergyIncreaseFactor` type calls (no accrual step run against outstanding `ContractStateCapsule`s).
4. Contract A is not invoked again until cycle N+50.
5. On that invocation, `catchUpToCycle` is called with `newCycle = N+50`, but reads the *current* `increaseFactor = Y` and computes the entire 50-cycle increase/decrease trajectory using `Y`, even though cycles N+1..N+49 should have used the pre-change value `X` for part of that window (or a mix, if further changes occurred). Contract B, invoked every cycle in between, would have accrued each step with the parameter value active at that specific time, producing a different, more "correct" `energyFactor` than Contract A for the same underlying usage pattern. [2](#0-1)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L46-76)
```java
  public long getEnergyUsage() {
    return this.contractState.getEnergyUsage();
  }

  public void setEnergyUsage(long value) {
    this.contractState = this.contractState.toBuilder().setEnergyUsage(value).build();
  }

  public void addEnergyUsage(long toAdd) {
    setEnergyUsage(getEnergyUsage() + toAdd);
  }

  public long getEnergyFactor() {
    return this.contractState.getEnergyFactor();
  }

  public void setEnergyFactor(long value) {
    this.contractState = this.contractState.toBuilder().setEnergyFactor(value).build();
  }

  public long getUpdateCycle() {
    return this.contractState.getUpdateCycle();
  }

  public void setUpdateCycle(long value) {
    this.contractState = this.contractState.toBuilder().setUpdateCycle(value).build();
  }

  public void addUpdateCycle(long toAdd) {
    setUpdateCycle(getUpdateCycle() + toAdd);
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L78-87)
```java
  public boolean catchUpToCycle(DynamicPropertiesStore dps) {
    return catchUpToCycle(
        dps.getCurrentCycleNumber(),
        dps.getDynamicEnergyThreshold(),
        dps.getDynamicEnergyIncreaseFactor(),
        dps.getDynamicEnergyMaxFactor(),
        dps.allowStrictMath(),
        dps.disableJavaLangMath()
    );
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractStateCapsule.java (L89-147)
```java
  public boolean catchUpToCycle(
      long newCycle, long threshold, long increaseFactor, long maxFactor,
      boolean useStrictMath, boolean disableMath
  ) {
    long lastCycle = getUpdateCycle();

    // Updated within this cycle
    if (lastCycle == newCycle) {
      return false;
    }

    // Guard judge and uninitialized state
    if (lastCycle > newCycle || lastCycle == 0L) {
      reset(newCycle);
      return true;
    }

    final long precisionFactor = DYNAMIC_ENERGY_FACTOR_DECIMAL;

    // Increase the last cycle
    // fix the threshold = 0 caused incompatible
    if (getEnergyUsage() > threshold) {
      lastCycle += 1;
      double increasePercent = 1 + (double) increaseFactor / precisionFactor;
      this.contractState = ContractState.newBuilder()
          .setUpdateCycle(lastCycle)
          .setEnergyFactor(min(
              maxFactor,
              (long) ((getEnergyFactor() + precisionFactor) * increasePercent) - precisionFactor,
              disableMath))
          .build();
    }

    // No need to decrease
    long cycleCount = newCycle - lastCycle;
    if (cycleCount <= 0) {
      return true;
    }

    // Calc the decrease percent (decrease factor [75% ~ 100%])
    double decreasePercent = pow(
        1 - (double) increaseFactor / DYNAMIC_ENERGY_DECREASE_DIVISION / precisionFactor,
        cycleCount, useStrictMath
    );

    // Decrease to this cycle
    // (If long time no tx and factor is 100%,
    //  we just calc it again and result factor is still 100%.
    //  That means we merge this special case to normal cases)
    this.contractState = ContractState.newBuilder()
        .setUpdateCycle(newCycle)
        .setEnergyFactor(max(
            0,
            (long) ((getEnergyFactor() + precisionFactor) * decreasePercent) - precisionFactor,
            disableMath))
        .build();

    return true;
  }
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L1-20)
```java
package org.tron.core.consensus;

import java.util.Map;
import lombok.extern.slf4j.Slf4j;
import org.tron.core.capsule.ProposalCapsule;
import org.tron.core.config.Parameter.ForkBlockVersionEnum;
import org.tron.core.db.HistoryBlockHashUtil;
import org.tron.core.db.Manager;
import org.tron.core.store.DynamicPropertiesStore;
import org.tron.core.utils.ProposalUtil;
import org.tron.protos.Protocol.Transaction.Contract.ContractType;

/**
 * Notice:
 * <p>
 * if you want to add a proposal,you just should add a enum ProposalType and add the valid in the
 * validator method, add the process in the process method
 */
@Slf4j
public class ProposalService extends ProposalUtil {
```
