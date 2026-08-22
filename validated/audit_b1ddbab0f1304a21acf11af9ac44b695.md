### Title
Old-freeze `FREEZE`/native-contract TVM opcode path bypasses the "freeze v2 is open, old freeze is closed" restriction enforced by `FreezeBalanceActuator` - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java])

### Summary
This is the closest unprivileged analog to the reported bug class ("checked wrapper vs. unchecked underlying call reaching the same state"). In the report, `BatcherPaymentService` enforces checks (`onlyBatcher`, merkle-root/signature validation, pausable) before calling `AlignedLayerServiceManager.createNewTask()`, but that same state-changing function is reachable directly, with none of those checks applied. In java-tron, the same "checked front door / unchecked side door" pattern exists between the ordinary `FreezeBalanceContract` transaction path (`FreezeBalanceActuator`) and the TVM opcode-level native-contract path (`Program.freeze()` → `FreezeBalanceProcessor`), which implement the same underlying account-freezing state transition but with divergent validation logic.

### Finding Description
`FreezeBalanceActuator.validate()` contains an explicit guard that disables the legacy (v1) freeze mechanism once FreezeBalance V2 is active on the network: [1](#0-0) 

This means once `dynamicStore.supportUnfreezeDelay()` is true (Freeze V2 hard fork active), no ordinary account can submit a `FreezeBalanceContract` transaction through the normal actuator path — it is unconditionally rejected.

However, the TVM-level native contract implementation of the very same "freeze" state transition, `FreezeBalanceProcessor.validate()` (invoked from `Program.freeze()` for the TVM `freeze` precompiled opcode), does not contain this check: [2](#0-1) [3](#0-2) 

`Program.freeze()` builds a `FreezeBalanceParam`, invokes `FreezeBalanceProcessor.validate()`/`.execute()`, and commits directly to the repository — completely independent of `FreezeBalanceActuator`. Any contract that invokes the TVM `freeze` opcode is therefore not subject to the "old freeze is closed" restriction that governs the same operation reached via a normal transaction. This mirrors the reported flaw: a validation rule intended to gate a specific state-changing operation is enforced only in one caller (`BatcherPaymentService`/`FreezeBalanceActuator`) and is absent in the alternate caller that reaches the identical underlying mutation (`AlignedLayerServiceManager`/`FreezeBalanceProcessor`).

### Impact Explanation
If the `FREEZE` TVM opcode remains callable after the Freeze V2 hard fork (gated only by a separate `allowTvmFreeze()`/`allowTvmFreezeV2()` config flag rather than by `supportUnfreezeDelay()`), any smart contract could continue creating legacy v1 frozen balances after the network intended to fully retire the old freeze mechanism in favor of Freeze V2. This would produce inconsistent resource-accounting state (mixed v1/v2 frozen balances, total-weight accounting, and unfreeze eligibility windows) across the network, which the protocol explicitly tried to prevent by hard-disabling v1 freeze at the actuator level. This falls into the "resource and reward accounting corruption" / "actuator state transition" unprivileged-analog category permitted by scope.

### Likelihood Explanation
Reachability requires only deploying/calling a smart contract that issues the TVM `freeze` opcode — no privileged role, leaked key, or malicious peer is needed, consistent with an anonymous broadcast-transaction/contract-call vector. The actual exploitability hinges on whether the opcode itself is still enabled (via `VMConfig.allowTvmFreeze()`) at the same time `supportUnfreezeDelay()` (Freeze V2) is active; I was not able to fully confirm the exact hard-fork gating relationship between `allowTvmFreeze()` and `allowTvmFreezeV2()`/`supportUnfreezeDelay()` (the two flags could be mutually exclusive by config activation policy, which would neutralize this specific bypass). This is the key open uncertainty in this analysis.

### Recommendation
Add the same guard used in `FreezeBalanceActuator.validate()` — rejecting the operation when `repo.getDynamicPropertiesStore().supportUnfreezeDelay()` is true — into `FreezeBalanceProcessor.validate()`, ensuring the TVM native-contract path cannot be used to perform legacy freeze operations once Freeze V2 is active. More generally, any business rule change applied to a legacy actuator (deprecation, feature-flag gating, fee logic) should be mirrored in its corresponding TVM native-contract processor to avoid divergent enforcement between the two entry points.

### Proof of Concept
Conceptual reproduction (pending confirmation of the opcode enable-flag interaction):
1. Network activates Freeze V2 (`supportUnfreezeDelay()` becomes true), making all `FreezeBalanceContract` transactions fail via `FreezeBalanceActuator.validate()`.
2. If `VMConfig.allowTvmFreeze()` is still enabled (not tied to `supportUnfreezeDelay()`), a contract calls the `freeze` TVM opcode, hitting `Program.freeze()` → `FreezeBalanceProcessor.validate()`/`.execute()`.
3. Because `FreezeBalanceProcessor.validate()` lacks the `supportUnfreezeDelay()` check, the freeze succeeds, creating a legacy v1 frozen balance entry that should have been impossible post-Freeze-V2, corrupting the intended single-source-of-truth resource accounting model.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L271-274)
```java
    if (dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException(
              "freeze v2 is open, old freeze is closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L21-70)
```java
  public void validate(FreezeBalanceParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    // validate arg @frozenBalance
    byte[] ownerAddress = param.getOwnerAddress();
    AccountCapsule ownerCapsule = repo.getAccount(ownerAddress);
    long frozenBalance = param.getFrozenBalance();
    if (frozenBalance <= 0) {
      throw new ContractValidateException("FrozenBalance must be positive");
    } else if (frozenBalance < TRX_PRECISION) {
      throw new ContractValidateException("FrozenBalance must be greater than or equal to 1 TRX");
    } else if (frozenBalance > ownerCapsule.getBalance()) {
      throw new ContractValidateException("FrozenBalance must be less than or equal to accountBalance");
    }

    // validate frozen count of owner account
    int frozenCount = ownerCapsule.getFrozenCount();
    if (frozenCount != 0 && frozenCount != 1) {
      throw new ContractValidateException("FrozenCount must be 0 or 1");
    }

    // validate arg @resourceType
    switch (param.getResourceType()) {
      case BANDWIDTH:
      case ENERGY:
        break;
      default:
        throw new ContractValidateException(
            "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
    }

    // validate for delegating resource
    byte[] receiverAddress = param.getReceiverAddress();
    if (!FastByteComparisons.isEqual(ownerAddress, receiverAddress)) {
      param.setDelegating(true);

      // check if receiver account exists. if not, then create a new account
      AccountCapsule receiverCapsule = repo.getAccount(receiverAddress);
      if (receiverCapsule == null) {
        receiverCapsule = repo.createNormalAccount(receiverAddress);
      }

      // forbid delegating resource to contract account
      if (receiverCapsule.getType() == Protocol.AccountType.Contract) {
        throw new ContractValidateException(
            "Do not allow delegate resources to contract addresses");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L1916-1950)
```java
  public boolean freeze(DataWord receiverAddress, DataWord frozenBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();
    byte[] receiver = receiverAddress.toTronAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, receiver,
        frozenBalance.longValue(), null,
        "freezeFor" + convertResourceToString(resourceType), nonce, null);

    FreezeBalanceParam param = new FreezeBalanceParam();
    param.setOwnerAddress(owner);
    param.setReceiverAddress(receiver);
    boolean needCheckFrozenTime = CommonParameter.getInstance()
        .getCheckFrozenTime() == 1; // for test
    param.setFrozenDuration(needCheckFrozenTime
        ? repository.getDynamicPropertiesStore().getMinFrozenTime() : 0);
    param.setResourceType(parseResourceCode(resourceType));
    try {
      FreezeBalanceProcessor processor = new FreezeBalanceProcessor();
      param.setFrozenBalance(frozenBalance.sValue().longValueExact());
      processor.validate(param, repository);
      processor.execute(param, repository);
      repository.commit();
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM Freeze: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM Freeze: frozenBalance out of long range.");
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```
