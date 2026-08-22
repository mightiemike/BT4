### Title
Native TVM freeze/unfreeze precompiles bypass reward-model-aware weight accounting, causing `TotalNetWeight`/`TotalEnergyWeight` drift - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java])

### Summary
`FreezeBalanceProcessor.execute()` and `UnfreezeBalanceProcessor.execute()` (the native-contract implementations invoked by the TVM `freeze`/`unfreeze` opcodes, callable by any Solidity contract) always adjust the global `TotalNetWeight`/`TotalEnergyWeight` counters using the raw `frozenBalance / TRX_PRECISION` (or `-unfreezeBalance / TRX_PRECISION`) amount of the *current* call. The RPC/legacy actuator counterparts (`FreezeBalanceActuator`/`UnfreezeBalanceActuator`) instead compute the *actual* weight delta (`newNetWeight - oldNetWeight`, based on floor-division of the account's cumulative frozen balance) whenever `allowNewReward()` is enabled, and only fall back to the raw amount when it is disabled. This mirrors the reported Mochi `debts` bug pattern: one code path increments/decrements a global accumulator using a value that differs from the value used by the "correct"/reward-aware code path, so the global counter permanently drifts from the true sum of per-account weights when the two paths are mixed or when the `allowNewReward` flag is enabled while the TVM path is exercised.

### Finding Description
In `FreezeBalanceActuator.execute()`/`addTotalWeight()`: [1](#0-0) [2](#0-1) 

`increment` is computed as the difference of `getFrozenBalance()/TRX_PRECISION` before and after adding the newly frozen amount to the account's cumulative frozen balance. Because integer division floors, this delta can differ from `frozenBalance / TRX_PRECISION` whenever the account already holds a frozen balance that is not an exact multiple of `TRX_PRECISION` boundaries (e.g. repeated partial freezes). `addTotalWeight` deliberately picks `increment` (the rounding-correct value) over the raw amount when `dynamicStore.allowNewReward()` is true.

In contrast, the TVM-native `FreezeBalanceProcessor.execute()` unconditionally uses the raw amount, ignoring `allowNewReward()`: [3](#0-2) 

And `UnfreezeBalanceProcessor.execute()` does the symmetric unconditional raw subtraction: [4](#0-3) 

Because these processors never look at `allowNewReward()` and never compute an actual before/after weight delta, calls to the TVM `freeze()`/`unfreeze()` opcodes (reachable from any smart contract, triggered by any user's broadcast transaction that invokes such a contract, exposed via `OperationActions`/`Program.java`) will, over repeated partial-freeze/partial-unfreeze cycles, credit/debit `TotalNetWeight`/`TotalEnergyWeight` by an amount that differs from the true weight change of the account, in exactly the same "increment computed differently on the way up vs. the way down" pattern flagged in the report (`debts += _amount` vs. `debts -= details[_id].debt`).

### Impact Explanation
`TotalNetWeight`/`TotalEnergyWeight` are core, network-global accounting values used to compute every account's bandwidth/energy limit and TRX staking rewards (via `BandwidthProcessor`, `EnergyProcessor`, and the resource/vote-reward system). A drift in the raw counters — even by small per-transaction rounding amounts — accumulates over many TVM-triggered freeze/unfreeze cycles and permanently desynchronizes the global weight from the true sum of accounts' frozen balances. This degrades:
- Bandwidth/energy limit calculations for all accounts (`calculateGlobalNetLimit`, `getTotalNetLimit`/`getTotalEnergyCurrentLimit` usage),
- Resource/voting weight-based reward distribution correctness.

This is a consensus-relevant state variable, so any divergence is deterministic across all nodes (same code executes identically), but the accounted totals no longer reflect real frozen balances, corrupting resource/reward accounting network-wide — analogous to the "debts will end up incorrect" impact described in the report.

### Likelihood Explanation
Reachability is unprivileged: any user can deploy or call a contract that uses the TVM freeze/unfreeze opcodes (guarded only by `VMConfig`/`allowTvmFreeze`-style feature flags, not by any permission check), and the divergence condition (partial/incremental freeze or unfreeze calls where cumulative frozen balance crosses a `TRX_PRECISION` rounding boundary differently than the per-call raw amount) is a routine usage pattern, not an edge case requiring privileged access. However, the magnitude of drift per call is bounded by rounding (< 1 TRX-unit of weight per call), so severity accumulates gradually rather than being catastrophic in a single transaction.

### Recommendation
Make `FreezeBalanceProcessor.execute()` and `UnfreezeBalanceProcessor.execute()` compute weight deltas the same way as `FreezeBalanceActuator`/`UnfreezeBalanceActuator`: capture `oldWeight = accountCapsule.getFrozenBalance()/TRX_PRECISION` (or energy equivalent) before mutating the frozen balance, compute `newWeight` after, and add/subtract `newWeight - oldWeight` to `TotalNetWeight`/`TotalEnergyWeight`, respecting the same `allowNewReward()` gating used in the actuator path (or simply always use the rounding-correct delta, removing the inconsistency between the RPC and TVM code paths).

### Proof of Concept
Given `TRX_PRECISION = 1_000_000`:
1. Account has 0 frozen balance. Contract calls TVM `freeze(1_500_000)` for BANDWIDTH. `FreezeBalanceProcessor.execute` sets `frozenBalance = 1_500_000`, adds `1_500_000/1_000_000 = 1` to `TotalNetWeight`. True weight = 1. Consistent so far.
2. Same account calls TVM `freeze(1_500_000)` again. New cumulative frozen balance = `3_000_000`; true weight = `3_000_000/1_000_000 = 3`, i.e., the delta should be `3 - 1 = 2`. But `FreezeBalanceProcessor.execute` (per [5](#0-4) ) only adds the raw `1_500_000/1_000_000 = 1` for this call, under-crediting `TotalNetWeight` by 1 versus the true cumulative weight — the same style of accounting mismatch as the reported `debts` bug, where the "add" path and the correctly-computed delta diverge.
3. Repeating this pattern (via `UnfreezeBalanceProcessor` symmetric raw subtraction) across many accounts and many partial freeze/unfreeze operations accumulates a persistent, unrecoverable drift in the global `TotalNetWeight`/`TotalEnergyWeight` state, unlike the legacy actuator path which is rounding-correct when `allowNewReward()` is enabled.

Note: I was not able to fully verify from the index whether `allowNewReward()` is enabled by default on current mainnet configuration, nor whether the TVM freeze/unfreeze opcodes are currently gated by a separate `VMConfig` flag that might be disabled in production — this would need to be confirmed in a live/full-repo Devin session, as index size limits may have excluded some configuration/gating files relevant to feature-flag defaults.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L87-96)
```java
        } else {
          long oldNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          long newFrozenBalanceForBandwidth =
              frozenBalance + accountCapsule.getFrozenBalance();
          accountCapsule.setFrozenForBandwidth(newFrozenBalanceForBandwidth, expireTime);
          long newNetWeight = accountCapsule.getFrozenBalance() / TRX_PRECISION;
          increment = newNetWeight - oldNetWeight;
        }
        addTotalWeight(BANDWIDTH, dynamicStore, frozenBalance, increment);
        break;
```

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L134-150)
```java
  private void addTotalWeight(ResourceCode resourceCode, DynamicPropertiesStore dynamicStore,
                              long frozenBalance, long increment) {
    long weight = dynamicStore.allowNewReward() ? increment : frozenBalance / TRX_PRECISION;
    switch (resourceCode) {
      case BANDWIDTH:
        dynamicStore.addTotalNetWeight(weight);
        break;
      case ENERGY:
        dynamicStore.addTotalEnergyWeight(weight);
        break;
      case TRON_POWER:
        dynamicStore.addTotalTronPowerWeight(weight);
        break;
      default:
        logger.debug("Resource Code Error.");
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L99-129)
```java
    } else { // acquire resource
      switch (param.getResourceType()) {
        case BANDWIDTH:
          accountCapsule.setFrozenForBandwidth(
              frozenBalance + accountCapsule.getFrozenBalance(),
              expireTime);
          break;
        case ENERGY:
          accountCapsule.setFrozenForEnergy(
              frozenBalance + accountCapsule.getAccountResource()
                  .getFrozenBalanceForEnergy()
                  .getFrozenBalance(),
              expireTime);
          break;
        default:
          logger.debug("Resource Code Error.");
      }
    }

    // adjust total resource
    switch (param.getResourceType()) {
      case BANDWIDTH:
        repo.addTotalNetWeight(frozenBalance / TRX_PRECISION);
        break;
      case ENERGY:
        repo.addTotalEnergyWeight(frozenBalance / TRX_PRECISION);
        break;
      default:
        //this should never happen
        break;
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L188-201)
```java
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
