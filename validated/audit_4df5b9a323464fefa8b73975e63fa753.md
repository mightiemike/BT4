### Title
Missing upper-bound validation on `origin_energy_limit` in `UpdateEnergyLimitContractActuator` allows a contract owner to set an unbounded energy-draw limit on their own deployed contract - (File: actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java)

### Summary
`UpdateEnergyLimitContractActuator.validate()` only checks that the new `origin_energy_limit` value is greater than zero, with no upper bound check, mirroring the reported `MarinateV2.sol` pattern of missing validation limits for setter inputs (e.g., `addApprovedMultiplierToken`, `setDepositLimit`).

### Finding Description
The `UpdateEnergyLimitContract` broadcastable transaction lets a contract's origin/owner address update `origin_energy_limit` for their deployed smart contract, which governs how much energy the contract owner is willing to pay on behalf of callers (in combination with `consume_user_resource_percent`) during TVM execution. [1](#0-0) 
The validation logic only enforces `newOriginEnergyLimit <= 0` throws, but performs no check against a sane upper bound (e.g., the current block energy limit or `TOTAL_ENERGY_LIMIT`/`TOTAL_CURRENT_ENERGY_LIMIT` dynamic parameters), unlike other setters in the same codebase which do bound related values, e.g. `UpdateAssetActuator` bounding `newLimit`/`newPublicLimit` against `getOneDayNetLimit()` [2](#0-1) 
and `UpdateSettingContractActuator` bounding `consume_user_resource_percent` to `[0,100]` [3](#0-2) 
This value is directly persisted into the `ContractCapsule`/`SmartContract` proto via `execute()` [4](#0-3) 
and later read back unbounded by `ContractCapsule.getOriginEnergyLimit()` [5](#0-4) 

I was not able to fully trace, within the tool budget available, the exact downstream arithmetic in `TransactionTrace`/`VMActuator`/`ReceiptCapsule` that consumes `getOriginEnergyLimit()` to compute how much energy is actually drawn from the contract owner's account versus the caller (my searches for `originEnergyLimit` in `VMActuator.java` and `ReceiptCapsule.java` returned no matches, which is inconsistent with the actuator's own energy-limit semantics and suggests the consuming logic lives under a different method/field name I could not resolve before running out of iterations).

### Impact Explanation
If the energy-split calculation elsewhere in the codebase trusts `getOriginEnergyLimit()` as an authoritative per-transaction cap without separately bounding it against the block/transaction energy limit, a contract owner could set an arbitrarily large value (up to `Long.MAX_VALUE`), potentially causing that owner's own account to be charged an excessive amount, or (if used asymmetrically in accounting/overflow-prone arithmetic) could distort energy accounting for callers of that contract. Because I could not confirm the exact consuming code path, I cannot state with certainty that this reaches unauthorized account operation or accounting corruption beyond the contract owner's own account — it is at most a self-inflicted misconfiguration risk unless a downstream overflow or shared accounting bug is found.

### Likelihood Explanation
Low-to-moderate: any contract owner can trigger this via a standard `UpdateEnergyLimitContract` transaction with no special privilege beyond being the contract's origin address, so the action itself is easily reachable. However, the actual severity depends on unverified downstream energy-accounting logic.

### Recommendation
Add an explicit upper bound check in `UpdateEnergyLimitContractActuator.validate()`, e.g. reject `newOriginEnergyLimit` values greater than the current block/transaction energy limit (`dynamicPropertiesStore` equivalent used elsewhere, such as `TOTAL_ENERGY_LIMIT`), analogous to how `UpdateAssetActuator` bounds `newLimit` against `getOneDayNetLimit()`.

### Proof of Concept
1. Deploy a smart contract as `OWNER_ADDRESS`.
2. Broadcast an `UpdateEnergyLimitContract` transaction with `origin_energy_limit = Long.MAX_VALUE` (any positive value passes `validate()` per lines 97-101). [1](#0-0) 
3. The value is stored unmodified in the contract's `SmartContract` proto and returned by `ContractCapsule.getOriginEnergyLimit()` for all future calls, with no ceiling enforced by the actuator. [5](#0-4) 

**Note on limitation:** Because I could not verify (within available iterations) how `getOriginEnergyLimit()` is consumed in `VMActuator`/`TransactionTrace`/`ReceiptCapsule` for actual energy accounting, this finding should be treated as a validated missing-input-validation weakness with **unconfirmed** downstream severity. I recommend a follow-up Devin session with full codebase access to trace the energy-split calculation and confirm whether an unbounded `origin_energy_limit` can cause consensus-relevant accounting corruption or is limited to the owner's own account.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L40-48)
```java
      UpdateEnergyLimitContract usContract = any.unpack(UpdateEnergyLimitContract.class);
      long newOriginEnergyLimit = usContract.getOriginEnergyLimit();
      byte[] contractAddress = usContract.getContractAddress().toByteArray();
      ContractCapsule deployedContract = contractStore.get(contractAddress);

      contractStore.put(contractAddress, new ContractCapsule(
          deployedContract.getInstance().toBuilder().setOriginEnergyLimit(newOriginEnergyLimit)
              .build()));
      RepositoryImpl.removeLruCache(contractAddress);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L97-101)
```java
    long newOriginEnergyLimit = contract.getOriginEnergyLimit();
    if (newOriginEnergyLimit <= 0) {
      throw new ContractValidateException(
          "origin energy limit must be > 0");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateAssetActuator.java (L159-166)
```java
    if (newLimit < 0 || newLimit >= dynamicStore.getOneDayNetLimit()) {
      throw new ContractValidateException("Invalid FreeAssetNetLimit");
    }

    if (newPublicLimit < 0 || newPublicLimit >=
        dynamicStore.getOneDayNetLimit()) {
      throw new ContractValidateException("Invalid PublicFreeAssetNetLimit");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java (L93-97)
```java
    long newPercent = contract.getConsumeUserResourcePercent();
    if (newPercent > ActuatorConstant.ONE_HUNDRED || newPercent < 0) {
      throw new ContractValidateException(
          "percent not in [0, 100]");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ContractCapsule.java (L117-123)
```java
  public long getOriginEnergyLimit() {
    long originEnergyLimit = this.smartContract.getOriginEnergyLimit();
    if (originEnergyLimit == Constant.PB_DEFAULT_ENERGY_LIMIT) {
      originEnergyLimit = Constant.CREATOR_DEFAULT_ENERGY_LIMIT;
    }
    return originEnergyLimit;
  }
```
