### Title
Malicious Super Representative can instantly set `brokerage` to 100% to steal an entire voting cycle's rewards from delegators - ([File: actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java])

### Summary
`UpdateBrokerageContract`/`UpdateBrokerageActuator` lets any witness (Super Representative, "SR") change the percentage of block/vote rewards it keeps ("brokerage") with no timelock and no ability for delegators/voters to pin the fee they expect, mirroring the Teller `setMarketFeePercent`/`setProtocolFee` front-running issue where a privileged party can unilaterally reprice a pending settlement and divert counterparty funds.

### Finding Description
`UpdateBrokerageActuator.execute` lets the witness owner update its brokerage instantly via `delegationStore.setBrokerage(ownerAddress, brokerage)`, which is `setBrokerage(-1, address, brokerage)` — i.e. it overwrites the "current" (`REMARK = -1`) brokerage value in a single transaction, bounded only to `[0, 100]`: [1](#0-0) [2](#0-1) [3](#0-2) 

The per-cycle brokerage that actually governs reward splitting is snapshotted at each maintenance-cycle boundary in `MaintenanceManager.doMaintenance()`, which copies whatever the "current" (`-1`) brokerage value is at that instant into the slot for the **next** cycle: [4](#0-3) 

`MortgageService.payReward` then uses the cycle-locked brokerage value to split every block reward and transaction-fee reward between the witness and its voters for that whole cycle: [5](#0-4) 

The maintenance boundary time (`nextMaintenanceTime`) is a fully deterministic, publicly known value derived from `MAINTENANCE_TIME_INTERVAL`, so any witness can predict exactly which block will trigger `doMaintenance()` and time an `UpdateBrokerageContract` transaction (or repeatedly reset it) to land immediately before that block, then revert it back afterward. This is structurally identical to the Teller bug: a privileged actor (market/protocol owner → witness) can front-run the moment fees are "locked in" for a pending settlement (bid acceptance → reward-cycle snapshot) and set the fee to the maximum allowed value, diverting funds that should go to a counterparty (lender → voter/delegator).

### Impact Explanation
Setting brokerage to 100 for one cycle means the witness keeps 100% of both block rewards and transaction-fee rewards for that cycle, and voters who delegated their votes/stake to that witness receive **zero** reward for the entire cycle (`brokerageAmount = value`, `value -= brokerageAmount` leaves 0 for `delegationStore.addReward`). Because cycles default to `MAINTENANCE_TIME_INTERVAL` (6 hours) and reward pools are computed chain-wide (block rewards, `WITNESS_PAY_PER_BLOCK`, and transaction fee rewards), this can represent a large, direct, unconsented transfer of funds from delegators to a single malicious/compromised SR, repeated every cycle if undetected. This satisfies the "accounting/underpriced-settlement" impact category: real, unprivileged users (TRX voters) lose real funds due to a privileged actor's unilateral, un-timelocked parameter change.

### Likelihood Explanation
Likelihood is realistic but requires a malicious or compromised witness (a privileged, but not "trusted-by-protocol-invariant" role — SRs are elected and can behave adversarially, similar to a market owner in Teller). The maintenance boundary is deterministic and publicly computable, so no mempool front-running/race condition is even needed — the attacker can simply schedule the `UpdateBrokerageContract` transaction for the block immediately preceding the known maintenance time and immediately revert it afterward, making the exploit low-cost, repeatable, and stealthy (only detectable by delegators watching each cycle's brokerage value).

### Recommendation
Introduce a delay (analogous to the Teller fix and the requested timelock) before a brokerage change takes effect — e.g., require the new value to only apply after N cycles instead of the immediately upcoming one, or lock a cycle's brokerage value earlier (e.g., freeze the "next-next" cycle's brokerage rather than the immediately upcoming one) so voters have visibility/exit time before the rate change applies to a cycle whose votes are already committed. Additionally, consider capping the maximum per-update brokerage delta to prevent instantaneous jumps to 100%.

### Proof of Concept
1. Attacker controls witness `W` with delegated voters.
2. Attacker computes the deterministic next maintenance time (`getNextMaintenanceTime`) and identifies the last block before that boundary.
3. Attacker submits `UpdateBrokerageContract{owner_address: W, brokerage: 100}` in that block; `UpdateBrokerageActuator.execute` writes it instantly to the `-1` slot. [1](#0-0) 
4. The maintenance block runs `doMaintenance()`, which snapshots `getBrokerage(W)` (now 100) into `nextCycle`'s brokerage slot for `W`. [4](#0-3) 
5. For the entire following cycle, every `payReward` call for `W` computes `brokerageAmount = value` (100%), leaving `0` for voters via `delegationStore.addReward`. [5](#0-4) 
6. Attacker resets brokerage back to a normal value (e.g., 20) before the next maintenance boundary, hiding the attack and restoring appearances, while having captured the full cycle's rewards intended for voters.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java (L49-53)
```java
    byte[] ownerAddress = updateBrokerageContract.getOwnerAddress().toByteArray();
    int brokerage = updateBrokerageContract.getBrokerage();

    delegationStore.setBrokerage(ownerAddress, brokerage);
    ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateBrokerageActuator.java (L93-95)
```java
    if (brokerage < 0 || brokerage > ActuatorConstant.ONE_HUNDRED) {
      throw new ContractValidateException("Invalid brokerage");
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/DelegationStore.java (L99-118)
```java
  public void setBrokerage(long cycle, byte[] address, int brokerage) {
    put(buildBrokerageKey(cycle, address), new BytesCapsule(ByteArray.fromInt(brokerage)));
  }

  public int getBrokerage(long cycle, byte[] address) {
    BytesCapsule bytesCapsule = get(buildBrokerageKey(cycle, address));
    if (bytesCapsule == null) {
      return DEFAULT_BROKERAGE;
    } else {
      return ByteArray.toInt(bytesCapsule.getData());
    }
  }

  public void setBrokerage(byte[] address, int brokerage) {
    setBrokerage(-1, address, brokerage);
  }

  public int getBrokerage(byte[] address) {
    return getBrokerage(-1, address);
  }
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
