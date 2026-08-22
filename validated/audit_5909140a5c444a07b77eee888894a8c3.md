### Title
Committee-gated FreezeV2/UnfreezeV2 feature switch (`supportUnfreezeDelay`) is bypassable via TVM precompiled opcodes - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java`, `actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java`)

### Summary
The standalone (externally-broadcastable) actuators `FreezeBalanceV2Actuator`, `UnfreezeBalanceV2Actuator`, `WithdrawExpireUnfreezeActuator`, `DelegateResourceActuator`, and `UnDelegateResourceActuator` all enforce a committee-controlled feature gate `dynamicStore.supportUnfreezeDelay()` before allowing the new FreezeV2 resource model to be used. The equivalent TVM native-contract processors that implement the same operations for smart contracts (`FreezeBalanceV2Processor`, `UnfreezeBalanceV2Processor`, and `UnDelegateResourceProcessor`) never check `supportUnfreezeDelay()`. A smart contract can therefore invoke these TVM opcodes to perform FreezeV2/UnfreezeV2/UnDelegateResource operations even while the committee has not opened (or has explicitly closed) the feature, exactly analogous to the reported `whenNotPaused` bypass pattern where an alternate code path lacks the same guard as the primary path.

### Finding Description
`FreezeBalanceV2Actuator.validate()` explicitly enforces: [1](#0-0) 

`UnfreezeBalanceV2Actuator.validate()` enforces the identical check: [2](#0-1) 

`WithdrawExpireUnfreezeActuator`, `DelegateResourceActuator`, and `UnDelegateResourceActuator` all contain the same `supportUnfreezeDelay()` gate in their `validate()` methods: [3](#0-2) [4](#0-3) 

However, the TVM-invokable equivalents omit this check entirely. `FreezeBalanceV2Processor.validate()` only checks address validity, account existence, balance bounds and resource type — never `supportUnfreezeDelay()`: [5](#0-4) 

`UnfreezeBalanceV2Processor.validate()` likewise omits it: [6](#0-5) 

`UnDelegateResourceProcessor.validate()` only checks `supportDR()`, never `supportUnfreezeDelay()`, unlike its actuator counterpart: [7](#0-6) 

These processors are directly invoked from TVM opcode handlers in `Program.java`, reachable from any smart contract execution (i.e., any broadcast `TriggerSmartContract` transaction): [8](#0-7) [9](#0-8) 

This mirrors the referenced report's bug class exactly: a pause/feature-gate modifier (`whenNotPaused` ≈ `supportUnfreezeDelay()`) is enforced on the primary user-facing entry point but omitted on an alternate reachable entry point (`restakeGGP`/`claimAndRestake` ≈ TVM opcode `freezeBalanceV2`/`unfreezeBalanceV2`/`unDelegateResource`) that performs the same state mutation.

### Impact Explanation
While `UnfreezeDelayDays`/FreezeV2 (`ALLOW_UNFREEZE_DELAY`) is disabled by the committee (e.g., not yet activated on a given network, or intentionally kept closed), any deployed smart contract can still call the FreezeBalanceV2/UnfreezeBalanceV2/UnDelegateResource TVM opcodes to freeze/unfreeze/undelegate balances under the new resource model. This creates inconsistent global chain state (`FrozenV2`, `UnfrozenV2`, total net/energy weight accounting) that the rest of the protocol assumes is only reachable once the committee-controlled switch is active, potentially causing accounting divergence between nodes/tools that gate on `supportUnfreezeDelay()` and the on-chain state actually produced via contract calls, and could also be used to prematurely exercise or trigger unintended interactions with the new resource-delegation windowing logic before the network is fully prepared for it.

### Likelihood Explanation
Any account can deploy a trivial smart contract invoking these opcodes and issue a normal `TriggerSmartContract` transaction, requiring no special privileges, keys, or timing — this is reachable by any anonymous user via a broadcast transaction, matching the same reachability class as the original `stake()`/`withdraw()` bypass.

### Recommendation
Add the same `dynamicStore.supportUnfreezeDelay()` (and, where applicable, `supportDR()`) checks to `FreezeBalanceV2Processor.validate()`, `UnfreezeBalanceV2Processor.validate()`, `UnDelegateResourceProcessor.validate()`, and `DelegateResourceProcessor.validate()` so that the TVM-reachable code paths enforce the identical committee-controlled feature gates as their corresponding actuators, preventing inconsistent bypass of the pause/feature-switch mechanism.

### Proof of Concept
1. On a network/state where the committee has not set `UnfreezeDelayDays > 0` (i.e., `supportUnfreezeDelay()` returns `false`), confirm that broadcasting a `FreezeBalanceV2Contract` transaction fails validation with "Not support FreezeV2 transaction, need to be opened by the committee" per [1](#0-0) .
2. Deploy a smart contract that invokes the `freezeBalanceV2`/`unfreezeBalanceV2` TVM opcode (as exercised in `FreezeV2Test`) and call it via a normal broadcast `TriggerSmartContract` transaction.
3. Observe that `FreezeBalanceV2Processor.validate()`/`execute()` succeed and mutate `FrozenV2`/total weight state despite the feature switch being closed, because no `supportUnfreezeDelay()` check exists in [5](#0-4) , bypassing the same gate enforced for regular transactions.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L107-110)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support FreezeV2 transaction,"
          + " need to be opened by the committee");
    }
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

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L209-212)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support unDelegate resource transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java (L21-66)
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

    // validate arg @resourceType
    switch (param.getResourceType()) {
      case BANDWIDTH:
      case ENERGY:
        break;
      case TRON_POWER:
        if (!repo.getDynamicPropertiesStore().supportAllowNewResourceModel()) {
          throw new ContractValidateException(
              "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
        break;
      default:
        if (repo.getDynamicPropertiesStore().supportAllowNewResourceModel()) {
          throw new ContractValidateException(
              "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY、TRON_POWER]");
        } else {
          throw new ContractValidateException(
              "Unknown ResourceCode, valid ResourceCode[BANDWIDTH、ENERGY]");
        }
    }
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java (L32-41)
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
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2017-2046)
```java
  public boolean freezeBalanceV2(DataWord frozenBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner,
        frozenBalance.longValue(), null,
        "freezeBalanceV2For" + convertResourceToString(resourceType), nonce, null);

    try {
      FreezeBalanceV2Param param = new FreezeBalanceV2Param();
      param.setOwnerAddress(owner);
      param.setResourceType(parseResourceCodeV2(resourceType));
      param.setFrozenBalance(frozenBalance.sValue().longValueExact());

      FreezeBalanceV2Processor processor = new FreezeBalanceV2Processor();
      processor.validate(param, repository);
      processor.execute(param, repository);
      repository.commit();
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM FreezeBalanceV2: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM FreezeBalanceV2: frozenBalance out of long range.");
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L2048-2082)
```java
  public boolean unfreezeBalanceV2(DataWord unfreezeBalance, DataWord resourceType) {
    Repository repository = getContractState().newRepositoryChild();
    byte[] owner = getContextAddress();

    increaseNonce();
    InternalTransaction internalTx = addInternalTx(null, owner, owner,
        unfreezeBalance.longValue(), null,
        "unfreezeBalanceV2For" + convertResourceToString(resourceType), nonce, null);

    try {
      UnfreezeBalanceV2Param param = new UnfreezeBalanceV2Param();
      param.setOwnerAddress(owner);
      param.setUnfreezeBalance(unfreezeBalance.sValue().longValueExact());
      param.setResourceType(parseResourceCodeV2(resourceType));

      UnfreezeBalanceV2Processor processor = new UnfreezeBalanceV2Processor();
      processor.validate(param, repository);
      long unfreezeExpireBalance = processor.execute(param, repository);
      repository.commit();
      if (unfreezeExpireBalance > 0) {
        increaseNonce();
        addInternalTx(null, owner, owner, unfreezeExpireBalance, null,
            "withdrawExpireUnfreezeWhileUnfreezing", nonce, null);
      }
      return true;
    } catch (ContractValidateException e) {
      logger.warn("TVM UnfreezeBalanceV2: validate failure. Reason: {}", e.getMessage());
    } catch (ArithmeticException e) {
      logger.warn("TVM UnfreezeBalanceV2: balance out of long range.");
    }
    if (internalTx != null) {
      internalTx.reject();
    }
    return false;
  }
```
