### Title
FreezeV2 feature-flag gate is bypassed via the `freezeBalanceV2` TVM precompiled contract - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/FreezeBalanceV2Processor.java`)

### Summary
The `FreezeBalanceV2Contract` transaction path enforces a committee-controlled feature gate (`supportUnfreezeDelay()`) before allowing FreezeV2 operations, but the equivalent internal logic reachable from a smart contract via the TVM native/precompiled contract does not perform this check, allowing the FreezeV2 flow to be exercised even while the feature has not been enabled by the committee.

### Finding Description
`FreezeBalanceV2Actuator.validate()` (the entry point for the broadcast `FreezeBalanceV2Contract` transaction) requires the dynamic property flag to be enabled before any FreezeV2 balance operation is processed: [1](#0-0) 

This mirrors the pattern in the external report, where an outer, directly-reachable entry point enforces a pausable/feature gate. However, the same freeze logic is also reachable through `FreezeBalanceV2Processor`, which backs the `freezeBalanceV2` TVM native/precompiled contract invoked from inside smart-contract execution: [2](#0-1) 

`FreezeBalanceV2Processor.validate()` checks address validity, account existence, balance sufficiency and resource-type validity (including `supportAllowNewResourceModel()` for `TRON_POWER`), but it never calls `supportUnfreezeDelay()` — the same check that gates the direct transaction path. This is the same bug class described in the report: a gate enforced on the primary entry point (`deregisterOperator` / `FreezeBalanceV2Actuator`) is missing on an alternate internal code path that performs the equivalent state transition (`_deregisterOperator` reached via `registerOperatorWithChurn` / the freeze logic reached via `FreezeBalanceV2Processor`).

The sibling `UnfreezeBalanceV2Processor.validate()` and `WithdrawExpireUnfreezeProcessor.validate()`, by contrast, do check `supportUnfreezeDelay()`: [3](#0-2) 

This asymmetry confirms the check was intended for the entire FreezeV2 feature set but is omitted specifically in `FreezeBalanceV2Processor`.

`FreezeBalanceV2Processor` is wired into the TVM's precompiled-contract dispatch/native-contract execution path (`PrecompiledContracts.java`, `Program.java`, `OperationRegistry.java`), meaning it is reachable from any smart contract via a `TriggerSmartContract` transaction — i.e., from an anonymous, unprivileged contract call, without going through `FreezeBalanceV2Actuator.validate()`.

### Impact Explanation
If the committee has not yet enabled FreezeV2 (`supportUnfreezeDelay()` returns false, i.e. the corresponding chain proposal has not passed), users should be unable to create new-style frozen balances (`FrozenV2`) since the feature is deliberately staged behind a proposal. By calling the `freezeBalanceV2` precompiled/native contract from within a smart contract, this restriction is bypassed, letting accounts create `FrozenV2` balances (and corresponding total network/energy weight state) before the feature is officially active on the network. This can produce inconsistent global state (partial FreezeV2 adoption ahead of the committee-controlled rollout) and diverges intended vs. actual on-chain behavior — a form of unauthorized state mutation / accounting inconsistency reachable from an ordinary contract call.

### Likelihood Explanation
Exploitability requires only deploying/calling a contract that invokes the native `freezeBalanceV2` operation while `supportUnfreezeDelay()` is false — a state that exists on any network before the corresponding committee proposal is activated (e.g. during initial rollout windows, private/test networks, or any deployment where the proposal has intentionally not yet been enabled). No privileged role or special conditions are needed beyond ordinary contract execution, making this straightforward to trigger by any user.

### Recommendation
Add the same `supportUnfreezeDelay()` gate check to `FreezeBalanceV2Processor.validate()` that already exists in `FreezeBalanceV2Actuator.validate()` and in the sibling processors (`UnfreezeBalanceV2Processor`, `WithdrawExpireUnfreezeProcessor`), throwing `ContractValidateException` when the feature is not yet supported, so the native-contract path cannot be used to bypass the feature-flag/pause gate enforced on the transaction path.

### Proof of Concept
1. On a chain/test network where the FreezeV2 proposal has not been activated (`getDynamicPropertiesStore().supportUnfreezeDelay()` returns `false`).
2. Deploy a smart contract that calls the `freezeBalanceV2` native/precompiled contract (as exercised in `framework/src/test/java/org/tron/common/runtime/vm/FreezeV2Test.java` and `PrecompiledContractsTest.java`).
3. Observe that `FreezeBalanceV2Processor.validate()` succeeds and `execute()` mutates account frozen-V2 balances and total resource weights, even though a direct `FreezeBalanceV2Contract` broadcast transaction would be rejected by `FreezeBalanceV2Actuator.validate()` with "Not support FreezeV2 transaction, need to be opened by the committee".

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/FreezeBalanceV2Actuator.java (L107-110)
```java
    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support FreezeV2 transaction,"
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

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/UnfreezeBalanceV2Processor.java (L33-37)
```java
  public void validate(UnfreezeBalanceV2Param param, Repository repo)
      throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }
```
