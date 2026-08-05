## Finding

**FreezeBalanceV2 / CancelAllUnfreezeV2 / WithdrawExpireUnfreeze TVM native-contract processors omit the committee-activation ("start") check that their actuator counterparts enforce - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java])**

### Summary
This mirrors the reported `Eggs.sol` bug class: one code path (`buy`) enforces a global "system started" flag, while a sibling path (`leverage`) that performs an equivalent operation omits it, letting users act before the feature is actually turned on. In java-tron the same pattern appears between the transaction-level actuators for the FreezeV2 resource model and their TVM native-contract "processor" twins, which are invoked directly from TVM opcodes.

### Finding Description
`FreezeBalanceV2Actuator.validate()` explicitly gates the feature behind a committee-controlled flag: [1](#0-0) 

The same is true for `CancelAllUnfreezeV2Actuator.validate()`: [2](#0-1) 

and for `WithdrawExpireUnfreezeActuator.validate()`: [3](#0-2) 

However, the TVM native-contract processor equivalents that back the corresponding VM opcodes/precompiles do **not** perform this check at all:
- `FreezeBalanceV2Processor.validate()` never calls `supportUnfreezeDelay()`: [4](#0-3) 
- `CancelAllUnfreezeV2Processor.validate()` never calls `supportAllowCancelAllUnfreezeV2()`: [5](#0-4) 
- `WithdrawExpireUnfreezeProcessor.validate()` never calls `supportUnfreezeDelay()`: [6](#0-5) 

These processors are wired into TVM opcode execution (confirmed by matches for `FreezeBalanceV2Processor`, `CancelAllUnfreezeV2Processor`, and `WithdrawExpireUnfreezeProcessor` in `actuator/src/main/java/org/tron/core/vm/program/Program.java`, and corresponding freeze/cancel/withdraw action handlers in `actuator/src/main/java/org/tron/core/vm/OperationActions.java`), so a smart contract can invoke these operations directly through the VM path, entirely bypassing the actuator-level `validate()` gate that requires the committee to have opened the FreezeV2/UnfreezeDelay/CancelAllUnfreezeV2 features (`UNFREEZE_DELAY_DAYS` proposal, `ALLOW_CANCEL_ALL_UNFREEZE_V2` proposal).

`supportUnfreezeDelay()` is defined purely from the dynamic property `UNFREEZE_DELAY_DAYS`: [7](#0-6) 

### Impact Explanation
Before the committee formally enables the V2 freeze/unfreeze/cancel-all-unfreeze feature (i.e., before `UNFREEZE_DELAY_DAYS`/`ALLOW_CANCEL_ALL_UNFREEZE_V2` are proposed and approved), a contract using the TVM opcode path can still perform FreezeBalanceV2, WithdrawExpireUnfreeze, and CancelAllUnfreezeV2 operations that mutate `FrozenV2`/`UnfrozenV2` account state and adjust global `TotalNetWeight`/`TotalEnergyWeight`/`TotalTronPowerWeight` counters — counters that are also consumed by bandwidth/energy pricing and voting logic. This creates a state/accounting divergence between the transaction-level actuator surface (properly gated) and the TVM-callable surface (ungated), analogous to opening a leveraged position before `start` is set in Eggs.sol: unprivileged contract callers gain early, unauthorized access to a not-yet-activated resource/accounting model, corrupting global resource weight accounting and giving early adopters resource/voting advantages unavailable to normal (non-TVM) users until the committee activates the feature.

### Likelihood Explanation
This is directly reachable by any user deploying/calling a smart contract that exercises the corresponding freeze/unfreeze VM opcodes — no privileged role is required. It requires no fork/version gating beyond what's already active for the opcode itself, and only depends on the committee not yet having approved the `UNFREEZE_DELAY_DAYS`/`ALLOW_CANCEL_ALL_UNFREEZE_V2` proposals, which is a normal transitional network state.

### Recommendation
Add the same committee-activation checks used in the actuator `validate()` methods to `FreezeBalanceV2Processor.validate()`, `CancelAllUnfreezeV2Processor.validate()`, and `WithdrawExpireUnfreezeProcessor.validate()`:
- `FreezeBalanceV2Processor.validate()`: require `repo.getDynamicPropertiesStore().supportUnfreezeDelay()`.
- `CancelAllUnfreezeV2Processor.validate()`: require `repo.getDynamicPropertiesStore().supportAllowCancelAllUnfreezeV2()`.
- `WithdrawExpireUnfreezeProcessor.validate()`: require `repo.getDynamicPropertiesStore().supportUnfreezeDelay()`.

### Proof of Concept
Not independently executable from static analysis (would require a running node/TVM harness), but the code path is concrete: deploy a contract that invokes the FreezeBalanceV2/CancelAllUnfreezeV2/WithdrawExpireUnfreeze VM opcode/precompile handlers in `OperationActions.java` while `UNFREEZE_DELAY_DAYS == 0` (feature not yet approved by committee). The transaction-level `FreezeBalanceV2Actuator` would reject such a call with "Not support FreezeV2 transaction, need to be opened by the committee", but the TVM path in `FreezeBalanceV2Processor.validate()`/`execute()` performs no such check and will process the freeze, confirmed by reading lines 21-45 and 68-105 of `FreezeBalanceV2Processor.java` where no `supportUnfreezeDelay()` call exists anywhere in the class.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L107-110)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support FreezeV2 transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/CancelAllUnfreezeV2Actuator.java (L130-133)
```java
    if (!dynamicStore.supportAllowCancelAllUnfreezeV2()) {
      throw new ContractValidateException("Not support CancelAllUnfreezeV2 transaction,"
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java (L21-45)
```java
  public void validate(FreezeBalanceV2Param param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    if (ownerCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + "] does not exist");
    }
    long frozenBalance = param.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("FrozenBalance must be positive");
    } else if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("FrozenBalance must be greater than or equal to 1 TRX");
    } else if (frozenBalance > ownerCapsule.getBalance()) {
      throw new ContractValidateException(
          "FrozenBalance must be less than or equal to accountBalance");
    }

```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/CancelAllUnfreezeV2Processor.java (L27-42)
```java
  public void validate(CancelAllUnfreezeV2Param param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    if (Objects.isNull(accountCapsule)) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + NOT_EXIST_STR);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/WithdrawExpireUnfreezeProcessor.java (L25-55)
```java
  public void validate(WithdrawExpireUnfreezeParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    if (Objects.isNull(accountCapsule)) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(ACCOUNT_EXCEPTION_STR
          + readableOwnerAddress + NOT_EXIST_STR);
    }

    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    List<Protocol.Account.UnFreezeV2> unfrozenV2List = accountCapsule.getInstance()
        .getUnfrozenV2List();
    long totalWithdrawUnfreeze = getTotalWithdrawUnfreeze(unfrozenV2List, now);
    if (totalWithdrawUnfreeze < 0) {
      throw new ContractValidateException("no unFreeze balance to withdraw ");
    }
    try {
      LongMath.checkedAdd(accountCapsule.getBalance(), totalWithdrawUnfreeze);
    } catch (ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/DynamicPropertiesStore.java (L2810-2812)
```java
  public boolean supportUnfreezeDelay() {
    return getUnfreezeDelayDays() > 0;
  }
```
