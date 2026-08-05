### Title
Truncation mismatch between `FreezeBalanceProcessor`/`UnfreezeBalanceProcessor` weight accounting drives `TOTAL_NET_WEIGHT`/`TOTAL_ENERGY_WEIGHT` out of sync with actual frozen balances - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java])

### Summary
`FreezeBalanceProcessor.execute` increments the global `TotalNetWeight`/`TotalEnergyWeight` by `frozenBalance / TRX_PRECISION` (the *incremental* amount, truncated) each time an account freezes via the TVM `freeze` opcode, while `UnfreezeBalanceProcessor.execute` decrements it by `unfreezeBalance / TRX_PRECISION` computed from the *entire accumulated* frozen amount at unfreeze time. Because integer division is subadditive (`floor(a)+floor(b) <= floor(a+b)`), repeated small, non-`TRX_PRECISION`-aligned freeze operations followed by a single unfreeze can subtract more weight than was ever added, and `RepositoryImpl.addTotalNetWeight`/`addTotalEnergyWeight` apply this delta with no floor-at-zero clamp.

### Finding Description
`FreezeBalanceProcessor.execute` (native VM contract reachable via `Program.freeze`) supports repeated freezing into the same slot: on the non-delegating path it does `accountCapsule.setFrozenForBandwidth(frozenBalance + accountCapsule.getFrozenBalance(), expireTime)` [1](#0-0)  and then updates the global weight using only the newly added increment: `repo.addTotalNetWeight(frozenBalance / TRX_PRECISION)` [2](#0-1) , instead of computing `(oldTotal+frozenBalance)/TRX_PRECISION - oldTotal/TRX_PRECISION` as the sibling V2 processors do.

Later, `UnfreezeBalanceProcessor.execute` sums *all* expired frozen entries into a single `unfreezeBalance` (the true accumulated total) and subtracts it in one shot: `repo.addTotalNetWeight(-unfreezeBalance / TRX_PRECISION)` [3](#0-2) . Since `floor(x1)+floor(x2)+...+floor(xn) <= floor(x1+x2+...+xn)` whenever the `xi` are not exact multiples of `TRX_PRECISION`, an attacker who freezes many small, non-round amounts (validation only requires `frozenBalance >= TRX_PRECISION`, not that it be a multiple of it — see `FreezeBalanceProcessor.validate` [4](#0-3) ) and then triggers a single unfreeze accumulates a net negative bias in `TotalNetWeight`/`TotalEnergyWeight` relative to the true sum of account frozen balances.

Critically, `RepositoryImpl.addTotalNetWeight`/`addTotalEnergyWeight` apply this delta unconditionally with no lower bound: [5](#0-4) . This is unlike `DynamicPropertiesStore.addTotalEnergyWeight`/`addTotalTronPowerWeight`, which clamp to zero when `allowNewReward()` is enabled [6](#0-5) ; the VM-path `RepositoryImpl` version has no such guard, so the accumulated bias can drive the persisted weight below the true value, including negative.

This path is reachable by any unprivileged account by deploying/calling a smart contract that repeatedly invokes the `freeze`/`unfreeze` TVM precompiled opcodes (`Program.freeze` / `Program.unfreeze`) [7](#0-6)  — no admin or governance privilege is required, and no existing check reconciles `TotalNetWeight` against the sum of `AccountCapsule` frozen balances.

### Impact Explanation
`TotalNetWeight`/`TotalEnergyWeight` are the network-wide denominators used to allocate bandwidth/energy limits proportionally to each account's frozen balance in the legacy (non-V2) freeze/resource model. If an attacker can drive this denominator down relative to the true aggregate frozen balance, every account frozen under this model receives a proportionally larger resource allocation than it should, i.e., bandwidth/energy work is priced below its true cost for the whole network — a materially underpriced public-cost path. Additionally, since the value is not floor-clamped in `RepositoryImpl`, the stored total can diverge into an invalid (even negative) state, which is a state-invariant violation independent of any specific downstream consumer.

### Likelihood Explanation
The precondition is trivial: any address can deploy a contract that calls the `freeze` TVM opcode multiple times with amounts that are ≥ `1 TRX` but not exact multiples of `TRX_PRECISION` (1,000,000 sun), then call `unfreeze` once the freeze period has elapsed. No special permissions, races, or governance actions are needed; the divergence accumulates linearly with the number of freeze operations performed before the single unfreeze, making it fully attacker-controlled and repeatable across many accounts/contracts to amplify the effect.

### Recommendation
Make `FreezeBalanceProcessor.execute`'s weight update symmetric with the `UnfreezeBalanceProcessor`/V2 processors by computing the delta from `(oldTotalFrozen)/TRX_PRECISION` vs `(newTotalFrozen)/TRX_PRECISION` rather than from the raw incremental `frozenBalance`, exactly as `FreezeBalanceV2Processor`/`UnfreezeBalanceV2Processor` already do with `oldNetWeight`/`newNetWeight`. Additionally, add a floor-at-zero clamp to `RepositoryImpl.addTotalNetWeight`/`addTotalEnergyWeight` matching the guard already present in `DynamicPropertiesStore`, and add an invariant check/migration to reconcile `TOTAL_NET_WEIGHT`/`TOTAL_ENERGY_WEIGHT` against the true sum of account frozen balances.

### Proof of Concept
```java
// Fuzz/invariant test sketch (JUnit, using RepositoryImpl test harness similar to FreezeTest.java)
@Test
public void testFreezeUnfreezeWeightDivergence() {
    // 1. Deploy/simulate account calling FreezeBalanceProcessor.execute multiple times
    //    with frozenBalance values NOT divisible by TRX_PRECISION, e.g. 1_500_001L sun each,
    //    accumulating into the same BANDWIDTH frozen slot (frozenCount stays 0/1).
    long[] amounts = {1_500_001L, 1_500_001L, 1_500_001L, 1_500_001L, 1_500_001L}; // sum = 7_500_005
    for (long amt : amounts) {
        FreezeBalanceParam param = buildFreezeParam(owner, owner, amt, ResourceCode.BANDWIDTH);
        freezeBalanceProcessor.execute(param, repo);
    }
    long weightAfterFreezes = repo.getTotalNetWeight(); // sum(floor(amt/1e6)) == 5 (each amt floors to 1)

    // 2. Advance time past expiry, then unfreeze all at once.
    advanceBlockTimePastExpiry();
    UnfreezeBalanceParam unfreezeParam = buildUnfreezeParam(owner, owner, ResourceCode.BANDWIDTH);
    unfreezeBalanceProcessor.execute(unfreezeParam, repo);
    long weightAfterUnfreeze = repo.getTotalNetWeight();

    // Expected under correct accounting: weightAfterUnfreeze should return exactly
    // to the pre-test baseline (0), since total frozen amount (7_500_005) matches original increments.
    // Actual: unfreezeBalance = 7_500_005, floor(7_500_005/1e6) = 7,
    // but only 5 was ever added -> weightAfterUnfreeze = 0 - (7 - 5) = -2 (negative / diverged).
    assertEquals(0L, weightAfterUnfreeze,
        "TotalNetWeight diverged from true frozen-balance sum due to truncation mismatch");
}
```
This demonstrates that `repo.getTotalNetWeight()` diverges from (and can go below) the value implied by the actual sum of frozen balances, confirming the accounting inconsistency described.

### Citations

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L29-36)
```java
    long frozenBalance = param.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("FrozenBalance must be positive");
    } else if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("FrozenBalance must be greater than or equal to 1 TRX");
    } else if (frozenBalance > ownerCapsule.getBalance()) {
      throw new ContractValidateException("FrozenBalance must be less than or equal to accountBalance");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L99-105)
```java
    } else { // acquire resource
      switch (param.getResourceType()) {
        case BANDWIDTH:
          accountCapsule.setFrozenForBandwidth(
              frozenBalance + accountCapsule.getFrozenBalance(),
              expireTime);
          break;
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L118-129)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceProcessor.java (L190-201)
```java
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

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L1165-1179)
```java
  //The unit is trx
  @Override
  public void addTotalNetWeight(long amount) {
    long totalNetWeight = getTotalNetWeight();
    totalNetWeight += amount;
    saveTotalNetWeight(totalNetWeight);
  }

  //The unit is trx
  @Override
  public void addTotalEnergyWeight(long amount) {
    long totalEnergyWeight = getTotalEnergyWeight();
    totalEnergyWeight += amount;
    saveTotalEnergyWeight(totalEnergyWeight);
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2282-2306)
```java
  //The unit is trx
  public void addTotalEnergyWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalEnergyWeight = getTotalEnergyWeight();
    totalEnergyWeight += amount;
    if (allowNewReward()) {
      totalEnergyWeight = max(0, totalEnergyWeight, disableJavaLangMath());
    }
    saveTotalEnergyWeight(totalEnergyWeight);
  }

  //The unit is trx
  public void addTotalTronPowerWeight(long amount) {
    if (amount == 0) {
      return;
    }
    long totalWeight = getTotalTronPowerWeight();
    totalWeight += amount;
    if (allowNewReward()) {
      totalWeight = max(0, totalWeight, disableJavaLangMath());
    }
    saveTotalTronPowerWeight(totalWeight);
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1952-1981)
```java
  public boolean unfreeze(DataWord receiverAddress, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();
    byte[] receiver = receiverAddress.toTronAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, receiver, 0, null,
        "unfreezeFor" + convertResourceToString(resourceType), nonce, null);

    UnfreezeBalanceParam param = new UnfreezeBalanceParam();
    param.setOwnerAddress(owner);
    param.setReceiverAddress(receiver);
    param.setResourceType(parseResourceCode(resourceType));
    try {
      UnfreezeBalanceProcessor processor = new UnfreezeBalanceProcessor();
      processor.validate(param, repository);
      long unfreezeBalance = processor.execute(param, repository);
      repository.commit();
      if (internalTx != null) {
        internalTx.setValue(unfreezeBalance);
      }
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM Unfreeze: validate failure. Reason: {}", e.getMessage());
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```
