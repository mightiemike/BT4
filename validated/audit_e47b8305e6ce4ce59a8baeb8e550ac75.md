Confirmed: `FreezeBalanceV2Actuator` immediately updates `TotalNetWeight`/`TotalEnergyWeight` with no lock [1](#0-0) , and `UnDelegateResourceProcessor.validate()` (invoked from TVM contract execution via native contracts) contains no time-lock/expiry check before allowing un-delegation — it only checks that the delegated balance amount is sufficient [2](#0-1) .

### Title
Atomic freeze→delegate→undelegate→unfreeze within a single TVM transaction allows flashloan-style manipulation of TotalNetWeight/TotalEnergyWeight and resource pricing - (File: actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java, actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java)

### Summary
The externally reported bug (M-24) is a "flashloan governance manipulation" pattern: a caller borrows tokens, performs a state-changing action that shifts weighted allocations, immediately triggers the downstream effect that consumes those weights in the *same transaction*, then reverses the state and repays the loan — leaving no lasting economic exposure while corrupting the weight-dependent computation for other participants. The analogous mechanism in java-tron is the TVM-exposed native resource contracts (`freezeBalanceV2`, `delegateResource`, `unDelegateResource`, `unfreezeBalanceV2`) reachable from `Program.java`'s native-contract opcodes. These let a smart contract, in one atomic transaction: freeze TRX to instantly bump `TotalNetWeight`/`TotalEnergyWeight` [3](#0-2) , delegate that frozen balance to inflate a receiver's resource limit [4](#0-3) , and then un-delegate it right back with no cooldown check [5](#0-4)  — all within the same block/transaction, before any other actor's usage/limit computation (which divides by `TotalNetWeight`/`TotalEnergyWeight`, see `DelegateResourceActuator`/`DelegateResourceProcessor` validate logic) is finalized.

### Finding Description
- `DelegateResourceProcessor.validate()`/`execute()` and `UnDelegateResourceProcessor.validate()`/`execute()` are native-contract processors invoked directly from TVM opcodes (via `Program.java`), meaning a malicious smart contract can chain freeze → delegate → undelegate calls inside one externally-triggered transaction.
- `UnDelegateResourceProcessor.validate()` only checks `unDelegateBalance <= delegatedResourceCapsule.getFrozenBalanceForXxx()`; there is no minimum holding period / expire-time check comparable to the old `UnfreezeBalanceActuator`'s `expireTimeForBandwidth/Energy > now` guard [6](#0-5) . That older, non-V2 delegation path enforces a time lock before unfreezing delegated resources, but the V2 delegate/undelegate native-contract path used by TVM does not.
- `FreezeBalanceV2Actuator.execute()` updates the global `TotalNetWeight`/`TotalEnergyWeight` synchronously and unconditionally the moment funds are frozen [3](#0-2) ; these totals are the denominator used elsewhere (e.g., `DelegateResourceActuator.validate()`) to compute usage/limit ratios [7](#0-6) .
- Because freeze, delegate, and undelegate are all reachable and reversible within one atomic transaction with no lock period, an attacker can transiently inflate their own or a target account's `Total*Weight`/resource limit, exploit that inflated state within the same transaction (e.g., to pass a resource-availability check, or to skew another contract's per-transaction weight-based computation that reads `TotalNetWeight`/`TotalEnergyWeight` mid-transaction), and then reverse everything before the transaction ends — the exact "manipulate-then-revert-atomically" pattern described in the flashloan/DeltaAllocations report.

### Impact Explanation
An attacker-controlled contract can, in a single broadcast transaction, freeze a large TRX balance (possibly obtained via borrowing/flash-style arrangement outside the chain, or via account transfers of already-owned capital), delegate it to inflate a receiver account's resource weight/limit, perform an economically favorable action that depends on that inflated weight (e.g., pass an energy/bandwidth-availability validation, or affect any other logic keyed off `TotalNetWeight`/`TotalEnergyWeight`), and then undelegate/unfreeze to fully unwind with no penalty. This corrupts resource/weight accounting integrity that other actuators and off-chain systems rely on, and is a genuine within-protocol accounting-manipulation vector.

### Likelihood Explanation
Reachability is high: `FreezeBalanceV2Contract`, `DelegateResourceContract`, and `UnDelegateResourceContract` are all standard broadcastable contract types, and the same operations are additionally exposed as TVM native-contract calls invocable atomically from a single smart-contract transaction [8](#0-7) . No special privilege is required — any account with sufficient TRX balance can execute this sequence.

### Recommendation
Introduce a minimum holding/lock period for `DelegateResourceContract`/`UnDelegateResourceContract` (mirroring the `expireTimeForBandwidth/Energy` check already present in the legacy `UnfreezeBalanceActuator`) so that delegated resources cannot be undelegated within the same transaction or block they were delegated in. Additionally, consider snapshotting `TotalNetWeight`/`TotalEnergyWeight` at the start of a block (rather than updating them synchronously mid-transaction) so that no single transaction can transiently manipulate global weight ratios for other computations executing in the same block.

### Proof of Concept
1. Attacker deploys a TVM smart contract.
2. Within one triggered transaction, the contract calls the native `freezeBalanceV2` operation for ENERGY, immediately increasing `TotalEnergyWeight` (`FreezeBalanceV2Actuator.execute()`).
3. The same transaction calls `delegateResource` to move the frozen balance's weight to a target/receiver account (`DelegateResourceProcessor.execute()`), inflating that receiver's momentary energy limit/available headroom.
4. The transaction performs whatever downstream logic benefits from the inflated resource weight/limit.
5. The same transaction calls `unDelegateResource` (no cooldown enforced by `UnDelegateResourceProcessor.validate()`) followed by `unfreezeBalanceV2`, fully reversing steps 2–3 before the transaction commits.
6. Net effect: momentary manipulation of `TotalNetWeight`/`TotalEnergyWeight`/receiver resource limit with full reversal in the same transaction and no lasting cost — the java-tron analog of the reported "manipulate-then-revert-in-one-transaction" flashloan pattern.

Note: I was not able to fully trace every downstream consumer of `TotalNetWeight`/`TotalEnergyWeight` that could be exploited mid-block for concrete economic gain (e.g., a precise scenario where this inflated weight lets a specific check pass that otherwise wouldn't) within the available search iterations; a full audit of all mid-transaction consumers of these totals would be needed to fully quantify exploitable impact.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L57-81)
```java
    long frozenBalance = freezeBalanceV2Contract.getFrozenBalance();
    long newBalance = accountCapsule.getBalance() - frozenBalance;

    switch (freezeBalanceV2Contract.getResource()) {
      case BANDWIDTH:
        long oldNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForBandwidthV2(frozenBalance);
        long newNetWeight = accountCapsule.getFrozenV2BalanceWithDelegated(BANDWIDTH) / TRX_PRECISION;
        dynamicStore.addTotalNetWeight(newNetWeight - oldNetWeight);
        break;
      case ENERGY:
        long oldEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        accountCapsule.addFrozenBalanceForEnergyV2(frozenBalance);
        long newEnergyWeight = accountCapsule.getFrozenV2BalanceWithDelegated(ENERGY) / TRX_PRECISION;
        dynamicStore.addTotalEnergyWeight(newEnergyWeight - oldEnergyWeight);
        break;
      case TRON_POWER:
        long oldTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        accountCapsule.addFrozenForTronPowerV2(frozenBalance);
        long newTPWeight = accountCapsule.getTronPowerFrozenV2Balance() / TRX_PRECISION;
        dynamicStore.addTotalTronPowerWeight(newTPWeight - oldTPWeight);
        break;
      default:
        logger.debug("Resource Code Error.");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L32-88)
```java
  public void validate(UnDelegateResourceParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    if (ownerCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + "] does not exist");
    }

    byte[] receiverAddress = param.getReceiverAddress();
    if (!DecodeUtil.addressValid(receiverAddress)) {
      throw new ContractValidateException("Invalid receiverAddress");
    }
    if (Arrays.equals(receiverAddress, ownerAddress)) {
      throw new ContractValidateException(
          "receiverAddress must not be the same as ownerAddress");
    }

    byte[] key = DelegatedResourceCapsule.createDbKeyV2(ownerAddress, receiverAddress, false);
    DelegatedResourceCapsule delegatedResourceCapsule = repo.getDelegatedResource(key);
    if (delegatedResourceCapsule == null) {
      throw new ContractValidateException(
          "delegated Resource does not exist");
    }

    long unDelegateBalance = param.getUnDelegateBalance();
    if (unDelegateBalance <= 0) {
      throw new ContractValidateException("unDelegateBalance must be more than 0 TRX");
    }
    switch (param.getResourceType()) {
      case BANDWIDTH:
        if (delegatedResourceCapsule.getFrozenBalanceForBandwidth() < unDelegateBalance) {
          throw new ContractValidateException("insufficient delegatedFrozenBalance(BANDWIDTH), request="
              + unDelegateBalance + ", balance=" + delegatedResourceCapsule.getFrozenBalanceForBandwidth());
        }
        break;
      case ENERGY:
        if (delegatedResourceCapsule.getFrozenBalanceForEnergy() < unDelegateBalance) {
          throw new ContractValidateException("insufficient delegateFrozenBalance(ENERGY), request="
              + unDelegateBalance + ", balance=" + delegatedResourceCapsule.getFrozenBalanceForEnergy());
        }
        break;
      default:
        throw new ContractValidateException(
            "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L31-55)
```java
public class DelegateResourceProcessor {

  public void validate(DelegateResourceParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    if (ownerCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }
    long delegateBalance = param.getDelegateBalance();
    if (delegateBalance < TRX_PRECISION) {
      throw new ContractValidateException("delegateBalance must be greater than or equal to 1 TRX");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L117-144)
```java
  public void execute(DelegateResourceParam param, Repository repo) {
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(param.getOwnerAddress());
    long delegateBalance = param.getDelegateBalance();
    byte[] receiverAddress = param.getReceiverAddress();

    // delegate resource to receiver
    switch (param.getResourceType()) {
      case BANDWIDTH:
        delegateResource(ownerAddress, receiverAddress, true,
            delegateBalance, repo);

        ownerCapsule.addDelegatedFrozenV2BalanceForBandwidth(delegateBalance);
        ownerCapsule.addFrozenBalanceForBandwidthV2(-delegateBalance);
        break;
      case ENERGY:
        delegateResource(ownerAddress, receiverAddress, false,
            delegateBalance, repo);

        ownerCapsule.addDelegatedFrozenV2BalanceForEnergy(delegateBalance);
        ownerCapsule.addFrozenBalanceForEnergyV2(-delegateBalance);
        break;
      default:
        logger.debug("Resource Code Error.");
    }

    repo.updateAccount(ownerCapsule.createDbKey(), ownerCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnfreezeBalanceActuator.java (L396-398)
```java
          if (delegatedResourceCapsule.getExpireTimeForBandwidth() > now) {
            throw new ContractValidateException("It's not time to unfreeze.");
          }
```

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L152-169)
```java
    switch (delegateResourceContract.getResource()) {
      case BANDWIDTH: {
        BandwidthProcessor processor = new BandwidthProcessor(chainBaseManager);
        processor.updateUsageForDelegated(ownerCapsule);

        long accountNetUsage = ownerCapsule.getNetUsage();
        if (null != this.getTx() && this.getTx().isTransactionCreate()) {
          accountNetUsage += TransactionUtil.estimateConsumeBandWidthSize(dynamicStore,
                  ownerCapsule.getFrozenV2BalanceForBandwidth());
        }
        long netUsage = (long) (accountNetUsage * TRX_PRECISION * ((double)
            (dynamicStore.getTotalNetWeight()) / dynamicStore.getTotalNetLimit()));
        long v2NetUsage = getV2NetUsage(ownerCapsule, netUsage,
            this.disableJavaLangMath());
        if (ownerCapsule.getFrozenV2BalanceForBandwidth() - v2NetUsage < delegateBalance) {
          throw new ContractValidateException(
              "delegateBalance must be less than or equal to available FreezeBandwidthV2 balance");
        }
```
