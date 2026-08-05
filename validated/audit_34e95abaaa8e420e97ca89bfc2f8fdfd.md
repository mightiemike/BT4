## Analysis

The Solvent bug pattern is: an admin-controlled boolean flag (`is_staking_enabled`, stored in `bucket_state`) is meant to gate a user action, but one of the two symmetric code paths (stake / unstake) never checks it, so the disabled feature remains reachable.

The equivalent pattern in java-tron is found in the resource-delegation feature. The committee-controlled proposal `UNFREEZE_DELAY_DAYS` (exposed as `DynamicPropertiesStore.supportUnfreezeDelay()`) is required by the "normal" transaction actuators before delegate/undelegate operations are permitted, but the TVM-native equivalents of the same operations skip this check entirely.

### Title
Missing `supportUnfreezeDelay()` Gate in TVM-Native Delegate/UnDelegate Resource Processors - (File: `actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java`, `actuator/src/main/java/org/tron/core/vm/nativecontract/UnDelegateResourceProcessor.java`)

### Summary
`DelegateResourceActuator` and `UnDelegateResourceActuator` (the normal transaction path) both require two independent committee-enabled feature flags before permitting resource delegation: `dynamicStore.supportDR()` and `dynamicStore.supportUnfreezeDelay()`. [1](#0-0) [2](#0-1) 

The equivalent TVM-native code paths — reachable via the `DELEGATERESOURCE`/`UNDELEGATERESOURCE` opcodes exposed to smart contracts — only check `supportDR()` and never call `supportUnfreezeDelay()`. [3](#0-2) [4](#0-3) 

### Finding Description
`supportUnfreezeDelay()` reflects the `UNFREEZE_DELAY_DAYS` governance parameter, which is the gate that actually activates the Freeze V2 resource model (freeze/unfreeze/delegate/undelegate) on-chain; when this proposal is processed, the committee explicitly whitelists `DelegateResourceContract`, `UnDelegateResourceContract`, `FreezeBalanceV2Contract`, `UnfreezeBalanceV2Contract`, and `WithdrawExpireUnfreezeContract` as permitted system contract types. [5](#0-4) 

Every other Freeze V2 actuator (`FreezeBalanceV2Actuator`, `UnfreezeBalanceV2Actuator`, `WithdrawExpireUnfreezeActuator`, `DelegateResourceActuator`, `UnDelegateResourceActuator`) consistently enforces `supportUnfreezeDelay()` in `validate()`. [6](#0-5) [7](#0-6) [8](#0-7) 

However, the `DELEGATERESOURCE`/`UNDELEGATERESOURCE` TVM opcodes are gated only by the separate `allowTvmFreezeV2` proposal (`VMConfig::allowTvmFreezeV2`) in the jump table, which is an independent chain-parameter from `UNFREEZE_DELAY_DAYS`. [9](#0-8) 

Because `DelegateResourceProcessor.validate()`/`UnDelegateResourceProcessor.validate()` never call `supportUnfreezeDelay()`, a smart contract can invoke these opcodes and successfully delegate/undelegate frozen-V2 resource balances even in a state where the committee has enabled `allowDelegateResource`/`allowTvmFreezeV2` but has not yet (or has deliberately not) approved `UNFREEZE_DELAY_DAYS` — a state where the ordinary user-facing `DelegateResourceContract`/`UnDelegateResourceContract` transactions are rejected both by the actuator's explicit check and by the system-contract permission whitelist set in `ProposalService`.

### Impact Explanation
This is a state/accounting divergence between two enforcement paths for the same governance-gated feature. It allows unprivileged smart-contract callers to perform resource-delegation state mutations (adjusting `DelegatedResourceCapsule` balances, `TotalNetWeight`/`TotalEnergyWeight`, and account frozen-V2 balances) that the administrator has not fully authorized network-wide, bypassing the intended two-flag activation sequence for the Freeze V2 feature set. This directly parallels the reported bug class: an admin toggle intended to gate an action is honored on one path and silently ignored on another.

### Likelihood Explanation
Exploitability depends on the committee approving `allowDelegateResource`/`allowTvmFreezeV2` while `UNFREEZE_DELAY_DAYS` remains at its default (0). Since these are independent on-chain proposals with no protocol-level ordering constraint tying `allowTvmFreezeV2`/`allowDelegateResource` activation to `UNFREEZE_DELAY_DAYS` activation, this misconfigured-but-plausible governance sequence is realistically reachable, and once reached, any deployed contract can trigger the divergent opcode path.

### Recommendation
Add `if (!dynamicStore.supportUnfreezeDelay()) { throw new ContractValidateException(...); }` to `DelegateResourceProcessor.validate()` and `UnDelegateResourceProcessor.validate()`, mirroring the check already present in `DelegateResourceActuator.validate()` and `UnDelegateResourceActuator.validate()`, so both enforcement paths for the same governance flag stay in sync.

### Proof of Concept
1. Committee approves the `ALLOW_DELEGATE_RESOURCE` proposal (`supportDR()` becomes true) and the `ALLOW_TVM_FREEZE_V2` proposal (enabling the `DELEGATERESOURCE`/`UNDELEGATERESOURCE` opcodes), but does **not** approve `UNFREEZE_DELAY_DAYS` (`supportUnfreezeDelay()` stays false).
2. A user submits a normal `DelegateResourceContract` transaction — rejected by `DelegateResourceActuator.validate()` at the `supportUnfreezeDelay()` check. [10](#0-9) 
3. The same user instead deploys/calls a smart contract that executes the `DELEGATERESOURCE` opcode. `DelegateResourceProcessor.validate()` only checks `supportDR()`, which is true, so the delegation succeeds despite the feature not being fully enabled by governance. [11](#0-10)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/DelegateResourceActuator.java (L118-125)
```java
    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }

    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support Delegate resource transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UnDelegateResourceActuator.java (L205-212)
```java
    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
    }

    if (!dynamicStore.supportUnfreezeDelay()) {
      throw new ContractValidateException("Not support unDelegate resource transaction,"
          + " need to be opened by the committee");
    }
```

**File:** actuator/src/main/java/org/tron/core/vm/nativecontract/DelegateResourceProcessor.java (L33-42)
```java
  public void validate(DelegateResourceParam param, Repository repo) throws ContractValidateException {
    if (repo == null) {
      throw new ContractValidateException(STORE_NOT_EXIST);
    }

    byte[] ownerAddress = param.getOwnerAddress();
    DynamicPropertiesStore dynamicStore = repo.getDynamicPropertiesStore();
    if (!dynamicStore.supportDR()) {
      throw new ContractValidateException("No support for resource delegate");
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

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L303-317)
```java
        case UNFREEZE_DELAY_DAYS: {
          DynamicPropertiesStore dynamicStore = manager.getDynamicPropertiesStore();
          dynamicStore.saveUnfreezeDelayDays(entry.getValue());
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.FreezeBalanceV2Contract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.UnfreezeBalanceV2Contract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.WithdrawExpireUnfreezeContract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.DelegateResourceContract_VALUE);
          dynamicStore.addSystemContractAndSetPermission(
              ContractType.UnDelegateResourceContract_VALUE);
          break;
        }
```

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

**File:** actuator/src/main/java/org/tron/core/vm/OperationRegistry.java (L641-655)
```java
  public static void appendDelegateOperations(JumpTable table) {
    BooleanSupplier proposal = VMConfig::allowTvmFreezeV2;

    table.set(new Operation(
        Op.DELEGATERESOURCE, 3, 1,
        EnergyCost::getDelegateResourceCost,
        OperationActions::delegateResourceAction,
        proposal));

    table.set(new Operation(
        Op.UNDELEGATERESOURCE, 3, 1,
        EnergyCost::getUnDelegateResourceCost,
        OperationActions::unDelegateResourceAction,
        proposal));
  }
```
