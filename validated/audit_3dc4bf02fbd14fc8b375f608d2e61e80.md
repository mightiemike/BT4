### Title
Missing `supportUnfreezeDelay` feature-gate check in TVM native FreezeV2 processors allows contract-triggered unfreeze/withdraw operations to bypass the network-level activation switch - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java`, `WithdrawExpireUnfreezeProcessor.java`, `CancelAllUnfreezeV2Processor.java`)

### Summary
The regular transaction actuators for the FreezeV2/unfreeze-delay feature check the committee-controlled `supportUnfreezeDelay` dynamic property before allowing any unfreeze/withdraw state transition. The TVM native-contract processors that implement the identical state transitions for smart-contract callers (via `freezeBalanceV2`, `unfreezeBalanceV2`, `withdrawExpireUnfreeze`, `cancelAllUnfreezeV2` opcodes in `Program.java`) omit this check entirely.

### Finding Description
`UnfreezeBalanceV2Actuator.validate()` and `WithdrawExpireUnfreezeActuator.validate()` both explicitly gate the unfreeze-delay feature: [1](#0-0) [2](#0-1) 

These checks throw `ContractValidateException` ("Not support ... transaction, need to be opened by the committee") whenever the `supportUnfreezeDelay` dynamic property has not been switched on by committee proposal.

The equivalent state-transition logic reachable from TVM (via the `unfreezeBalanceV2`, `withdrawExpireUnfreeze`, and `cancelAllUnfreezeV2` native opcodes exposed in `Program.java`) is implemented in: [3](#0-2) [4](#0-3) [5](#0-4) 

None of these `validate()` methods call `dynamicStore.supportUnfreezeDelay()`. The only gate present at the TVM call site is the separate `allowTvmFreezeV2` flag stored in `VMConfig.Snapshot`: [6](#0-5) 

`allowTvmFreezeV2` and `supportUnfreezeDelay` are independently controlled committee proposals — one gates the TVM opcode surface, the other gates whether the unfreeze-delay accounting model (the `UnfrozenV2`/`FreezeV2` fields, `calcUnfreezeExpireTime`, total-weight bookkeeping) is considered active on the network. This mirrors the `confirmWithdrawal()` bug class: the "pause"/feature-gate check exists on one code path (the ordinary actuator) but is missing on a functionally identical, reachable code path (the TVM native-contract processor invoked by any contract call), so completing the same restricted state transition is possible by going through the alternate path.

### Impact Explanation
If `allowTvmFreezeV2` is enabled by the committee ahead of (or independently of) `supportUnfreezeDelay`, or during any window where the two flags diverge, any deployed contract can call the `unfreezeBalanceV2`, `withdrawExpireUnfreeze`, or `cancelAllUnfreezeV2` TVM native operations and mutate `FreezeV2`/`UnfrozenV2` account fields and global resource-weight totals even though the network has not formally activated the unfreeze-delay accounting model via `supportUnfreezeDelay`. This causes resource/reward accounting corruption (total net/energy/TRON-power weight adjustments, vote invalidation, balance credits from `unfreezeExpire`) that can diverge from the state produced by the ordinary transaction path, which strictly rejects the same operation while the flag is off.

### Likelihood Explanation
Reachable by any unprivileged account deploying or calling a smart contract — no special permission is required beyond normal contract execution. The precondition is a configuration/timing gap between the two independent feature flags (`allowTvmFreezeV2` vs `supportUnfreezeDelay`), which is plausible since committee proposals for TVM opcode enablement and for chain-level economic model changes are typically voted on and activated separately.

### Recommendation
Add the same `supportUnfreezeDelay` check present in `UnfreezeBalanceV2Actuator.validate()` and `WithdrawExpireUnfreezeActuator.validate()` to `UnfreezeBalanceV2Processor.validate()`, `WithdrawExpireUnfreezeProcessor.validate()`, and `CancelAllUnfreezeV2Processor.validate()`, e.g.:
```java
if (!repo.getDynamicPropertiesStore().supportUnfreezeDelay()) {
  throw new ContractValidateException("Not support UnfreezeV2 transaction, need to be opened by the committee");
}
```

### Proof of Concept
1. Committee enables `allowTvmFreezeV2` (TVM opcode gate) while `supportUnfreezeDelay` remains disabled (e.g. proposal sequencing/timing gap).
2. A contract calls the native `unfreezeBalanceV2` operation (surfaced through `Program.java`), which invokes `UnfreezeBalanceV2Processor.validate()`/`execute()`.
3. Because `UnfreezeBalanceV2Processor.validate()` never checks `supportUnfreezeDelay`, the call succeeds, mutating `FreezeV2`/`UnfrozenV2` state and adjusting global resource weights — despite the same operation being rejected with "Not support UnfreezeV2 transaction, need to be opened by the committee" when attempted through `UnfreezeBalanceV2Actuator` (ordinary `UnfreezeBalanceV2Contract` transaction) under identical dynamic-property configuration.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L33-89)
```java
  public void validate(UnfreezeBalanceV2Param param, Repository repo)
      throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }
    AccountCapsule accountCapsule = repo.getAccount(ownerAddress);
    if (accountCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + "] does not exist");
    }
    long now = dynamicStore.getLatestBlockHeaderTimestamp();
    int unfreezingCount = accountCapsule.getUnfreezingV2Count(now);
    if (UnfreezeBalanceV2Actuator.getUNFREEZE_MAX_TIMES() <= unfreezingCount) {
      throw new ContractValidateException("Invalid unfreeze operation, unfreezing times is over limit");
    }
    switch (param.getResourceType()) {
      case BANDWIDTH:
        // validate frozen balance
        if (!this.checkExistFrozenBalance(accountCapsule, Common.ResourceCode.BANDWIDTH)) {
          throw new ContractValidateException("no frozenBalance(BANDWIDTH)");
        }
        break;
      case ENERGY:
        // validate frozen balance
        if (!this.checkExistFrozenBalance(accountCapsule, Common.ResourceCode.ENERGY)) {
          throw new ContractValidateException("no frozenBalance(ENERGY)");
        }
        break;
      case TRON_POWER:
        if (dynamicStore.supportAllowNewResourceModel()) {
          if (!this.checkExistFrozenBalance(accountCapsule, Common.ResourceCode.TRON_POWER)) {
            throw new ContractValidateException("no frozenBalance(TRON_POWER)");
          }
        } else {
          throw new ContractValidateException("Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
        break;
      default:
        if (dynamicStore.supportAllowNewResourceModel()) {
          throw new ContractValidateException("Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY、TRON_POWER]");
        } else {
          throw new ContractValidateException("Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
    }

    if (!checkUnfreezeBalance(accountCapsule, param.getUnfreezeBalance(), param.getResourceType())) {
      throw new ContractValidateException(
          "Invalid unfreeze_balance, [" + param.getUnfreezeBalance() + "] is invalid");
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

**File:** common/src/main/java/org/tron/core/vm/config/VMConfig.java (L255-257)
```java
  public static boolean allowTvmFreezeV2() {
    return current().allowTvmFreezeV2;
  }
```
