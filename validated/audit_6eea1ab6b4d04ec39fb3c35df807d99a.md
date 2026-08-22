### Title
Witness `UpdateBrokerageContract` allows unbounded, timelock-free brokerage-fee changes that instantly cut voters' rewards - (File: `actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java`)

### Summary
`UpdateBrokerageActuator` lets any Super Representative (SR/witness) unilaterally change the `brokerage` percentage it keeps from block/transaction-fee rewards, in the range `0-100`, with the change taking effect starting the very next voting cycle and no delay for voters to react. This is the java-tron analog of the "cred creator can change `sellShareRoyalty_`" bug: a value that other users have already committed funds against (TRX voted/frozen for a witness) can be redirected almost entirely to the witness with no notice period.

### Finding Description
`UpdateBrokerageActuator.execute()` unpacks the caller-supplied `brokerage` value and stores it via `delegationStore.setBrokerage(ownerAddress, brokerage)`, which is `setBrokerage(-1, address, brokerage)` — a "pending/current" slot, independent of cycle. [1](#0-0) 

`validate()` only checks that `brokerage` is between 0 and 100 and that the caller is a registered witness — there is no cooldown, no rate limit, and no advance-notice mechanism: [2](#0-1) 

The stored "-1" brokerage value is snapshotted into the **next** cycle's per-cycle brokerage record at every maintenance boundary, which occurs automatically and unconditionally: [3](#0-2) 

`MortgageService.payReward()` then uses the *current cycle's* brokerage to split block/transaction-fee rewards between the witness (brokerage cut) and the delegation pool that ultimately pays out to voters: [4](#0-3) 

Because a witness can submit `UpdateBrokerageContract` (via a broadcast transaction) at any time before the maintenance cycle rolls over, the new brokerage (up to 100%) becomes active for the immediately following cycle with zero warning to the voters who have already frozen/voted TRX for that witness in prior cycles. Voters have no way to preemptively withdraw votes for a cycle that has already begun accruing rewards under the old rate, mirroring the cred report's core issue: an unprivileged, permissionless actor (witness status is earned purely by TRX-holder votes, not an admin/committee role) controls a fee parameter over pooled user funds with no timelock or grace period.

### Impact Explanation
Voters who have frozen TRX and voted for a witness receive rewards computed via `VoteRewardUtil.computeReward()`/`accumulateWitnessVi()`, which are a function of the witness's `Vi` accumulator — itself derived from `value` after the brokerage cut is deducted in `MortgageService.payReward()`. If a witness sets brokerage to 100 right before a cycle rollover, all block/tx-fee reward for that witness for the entire next cycle is diverted to the witness, and voters who already have votes locked in (`accountCapsule.getVotesList()`) receive zero reward for that cycle without any opportunity to react, since `withdrawReward`/vote changes only take effect from the following cycle onward. This is a direct, unauthorized diversion of pooled voter funds analogous to the cred royalty issue, though bounded to reward income rather than principal.

### Likelihood Explanation
Any account that has enough votes to become an active witness (permissionless, achievable by any TRX holder) can trigger this at will by simply broadcasting an `UpdateBrokerageContract` transaction shortly before a maintenance cycle boundary — no special privileges beyond normal witness registration are required, and the maintenance cycle timing is public/predictable (`getNextMaintenanceTime`), making front-running of cycle boundaries straightforward.

### Recommendation
Introduce a timelock/notice period for brokerage changes: instead of applying the new brokerage to the very next cycle, delay activation by N cycles (e.g., require the change to be requested and only applied after the currently-locked cycle(s) complete), giving voters a window to withdraw/re-vote before the new rate takes effect. Alternatively, cap the maximum per-update change (e.g., a few percentage points per cycle) to bound worst-case reward diversion.

### Proof of Concept
1. Witness `W` has existing voters with TRX frozen and voted (`accountCapsule.getVotesList()` non-empty) for prior cycles, with `DEFAULT_BROKERAGE = 20`.
2. Shortly before the next maintenance boundary, `W` broadcasts `UpdateBrokerageContract{ownerAddress=W, brokerage=100}` — this passes `validate()` (0 ≤ 100 ≤ `ONE_HUNDRED`) and is stored via `setBrokerage(W, 100)` (cycle=-1). [1](#0-0) 
3. At maintenance, `MaintenanceManager.doMaintenance()` copies this pending value into `nextCycle`'s brokerage slot with no validation of prior notice: [3](#0-2) 
4. During the next cycle, `MortgageService.payReward()` computes `brokerageAmount = 100% * value`, leaving `value = 0` credited to `delegationStore.addReward(cycle, W, 0)` — i.e., voters' pool reward for that entire cycle is zero, all diverted to `W`'s allowance. [4](#0-3) 
5. Voters who already had votes locked in from the prior cycle cannot avoid this loss, since vote/withdraw effects only propagate starting from the following cycle boundary (per `withdrawReward`/`computeReward` cycle bookkeeping).

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java (L49-53)
```java
    byte[] ownerAddress = updateBrokerageContract.getOwnerAddress().toByteArray();
    int brokerage = updateBrokerageContract.getBrokerage();

    delegationStore.setBrokerage(ownerAddress, brokerage);
    ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java (L86-107)
```java
    byte[] ownerAddress = updateBrokerageContract.getOwnerAddress().toByteArray();
    int brokerage = updateBrokerageContract.getBrokerage();

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }

    if (brokerage < 0 || brokerage > ActuatorConstant.ONE_HUNDRED) {
      throw new ContractValidateException("Invalid brokerage");
    }

    WitnessCapsule witnessCapsule = witnessStore.get(ownerAddress);
    if (witnessCapsule == null) {
      throw new ContractValidateException("Not existed witness:" + Hex.toHexString(ownerAddress));
    }

    AccountCapsule account = accountStore.get(ownerAddress);
    if (account == null) {
      throw new ContractValidateException("Account does not exist");
    }

    return true;
```

**File:** consensus/src/main/java/org/tron/consensus/dpos/MaintenanceManager.java (L154-162)
```java
    if (dynamicPropertiesStore.allowChangeDelegation()) {
      long nextCycle = dynamicPropertiesStore.getCurrentCycleNumber() + 1;
      dynamicPropertiesStore.saveCurrentCycleNumber(nextCycle);
      consensusDelegate.getAllWitnesses().forEach(witness -> {
        delegationStore.setBrokerage(nextCycle, witness.createDbKey(),
            delegationStore.getBrokerage(witness.createDbKey()));
        delegationStore.setWitnessVote(nextCycle, witness.createDbKey(), witness.getVoteCount());
      });
    }
```

**File:** chainbase/src/main/java/org/tron/core/service/MortgageService.java (L79-87)
```java
  private void payReward(byte[] witnessAddress, long value) {
    long cycle = dynamicPropertiesStore.getCurrentCycleNumber();
    int brokerage = delegationStore.getBrokerage(cycle, witnessAddress);
    double brokerageRate = (double) brokerage / 100;
    long brokerageAmount = (long) (brokerageRate * value);
    value -= brokerageAmount;
    delegationStore.addReward(cycle, witnessAddress, value);
    adjustAllowance(witnessAddress, brokerageAmount);
  }
```
