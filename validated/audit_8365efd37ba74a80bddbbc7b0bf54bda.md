### Title
FreezeBalanceProcessor (TVM `freeze` opcode) bypasses the `supportUnfreezeDelay` guard enforced by `FreezeBalanceActuator` - ([File: actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java])

### Summary
`FreezeBalanceActuator.validate()` (the entry point used by ordinary `FreezeBalanceContract` broadcast transactions) explicitly rejects freeze-v1 requests once FreezeBalance V2 is active, but the equivalent `FreezeBalanceProcessor.validate()` used by the TVM `freeze()` opcode (invoked from smart contracts) never performs this check, letting contract callers keep using the deprecated freeze-v1 accounting path after the network has switched to v2.

### Finding Description
`FreezeBalanceActuator.validate()` contains a guard that is only present on the transaction-level entry point: [1](#0-0) 
This mirrors the exact bug pattern described in the report: a "guard modifier" (`whenNotPaused` in the original report, here the `supportUnfreezeDelay()` check) is applied to the outward-facing wrapper (`deposit`/`withdraw` ≈ `FreezeBalanceActuator`) but the shared underlying state-mutating logic (`modifyCollateralAndDebt` ≈ `FreezeBalanceProcessor`) is also independently reachable and lacks the same guard.

The `FreezeBalanceProcessor.validate()` method used by the TVM native `freeze()` opcode performs balance/type/receiver checks but never checks `supportUnfreezeDelay()`: [2](#0-1) 

This processor is invoked directly from `Program.freeze()`, which any smart contract can call via the corresponding TVM opcode: [3](#0-2) 

Because `Program.freeze()` builds `FreezeBalanceParam` itself and calls `FreezeBalanceProcessor.validate()`/`execute()` directly — never going through `FreezeBalanceActuator` — the `supportUnfreezeDelay()` restriction that blocks legacy freeze-v1 usage once v2 is enabled is silently skipped for the contract-call code path.

### Impact Explanation
Once governance/committee enables FreezeBalance V2 (`supportUnfreezeDelay()` returns true) with the intent of deprecating/closing the old freeze-v1 resource model, ordinary users are blocked from creating new v1 frozen balances via `FreezeBalanceActuator`. However, any smart contract can still call the TVM `freeze` opcode to create new v1 frozen balances and delegated resources, continuing to grow/maintain the legacy resource-accounting structures (`FrozenList`, delegated resource records, total bandwidth/energy weight) that the network intended to have fully retired. This causes divergence between the intended post-upgrade resource model and actual on-chain state, undermines the correctness of the migration/feature-flag invariant, and can distort bandwidth/energy weight accounting network-wide (any TRX holder can reach this via a trivial contract).

### Likelihood Explanation
High reachability: any account can deploy or call a contract that invokes the `freeze` opcode (`Program.freeze`), and the missing check has no other gating condition beyond `supportUnfreezeDelay()` being enabled by the chain's dynamic properties (a normal, expected chain-upgrade state, not a rare/privileged event). No special permissions are required by the caller.

### Recommendation
Add the same `dynamicStore.supportUnfreezeDelay()` check (mirroring `FreezeBalanceActuator.validate()`) to `FreezeBalanceProcessor.validate()`, throwing `ContractValidateException("freeze v2 is open, old freeze is closed")` when the flag is enabled, so both entry points enforce the deprecation of freeze-v1 consistently.

### Proof of Concept
1. Assume the network has activated FreezeBalance V2 (`supportUnfreezeDelay()` == true), so `FreezeBalanceActuator.validate()` rejects `FreezeBalanceContract` transactions with "freeze v2 is open, old freeze is closed".
2. An attacker deploys/uses any contract that invokes the TVM `freeze(receiver, amount, resourceType)` opcode, which routes to `Program.freeze()`. [4](#0-3) 
3. `FreezeBalanceProcessor.validate()` performs no `supportUnfreezeDelay()` check, so the freeze succeeds and mutates account/frozen state despite v1 freeze being nominally "closed" at the transaction level. [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceActuator.java (L271-274)
```java
    if (dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException(
              "freeze v2 is open, old freeze is closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceProcessor.java (L21-71)
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
