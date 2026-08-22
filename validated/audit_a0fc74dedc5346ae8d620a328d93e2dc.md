### Title
Missing Upper-Bound Validation on `origin_energy_limit` in `UpdateEnergyLimitContractActuator` - ([File: actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java])

### Summary
`UpdateEnergyLimitContractActuator` lets any ordinary account that is the recorded owner of a deployed smart contract update that contract's `origin_energy_limit` via a normal broadcast `UpdateEnergyLimitContract` transaction. The validation only rejects values `<= 0`; there is no upper bound, unlike the analogous `UpdateSettingContractActuator`, which explicitly clamps `consume_user_resource_percent` to `[0,100]`.

### Finding Description
In `validate()`, the only check applied to the new value is: [1](#0-0) 
No maximum is enforced, and the value is persisted directly into the contract's proto state in `execute()`: [2](#0-1) 
This is the same bug class as the reported `setFee` issue (an owner-controlled numeric parameter that governs fee/resource accounting with no min/max range), but reachable here through an ordinary, unprivileged smart-contract deployer rather than a network-level privileged/governance actor — the actuator only checks that the caller is the contract's `origin_address`, a role any user obtains simply by deploying a contract: [3](#0-2) 
By contrast, the sibling actuator `UpdateSettingContractActuator`, which sets a conceptually similar contract-owner-controlled resource parameter, does bound its value: [4](#0-3) 
This asymmetry indicates the missing bound in `UpdateEnergyLimitContractActuator` is an oversight rather than an intentional design choice.

`origin_energy_limit` caps how much energy a contract's origin account will subsidize on behalf of callers during TVM execution and energy metering (used in `VMActuator.java` and `ReceiptCapsule.java` for energy limit and fee computation). Because the field is a `long` with no upper bound check, a contract owner can set it to `Long.MAX_VALUE` or another extreme value.

### Impact Explanation
Setting an unbounded `origin_energy_limit` allows a contract owner to advertise (and have the network accept) an energy subsidy limit far beyond what is economically or computationally reasonable. Downstream energy accounting in `VMActuator`/`ReceiptCapsule` uses this origin limit when apportioning energy between caller and contract-origin account; an extreme, unchecked value increases the risk of unexpected/incorrect energy-cost apportionment during contract calls, potentially letting callers consume excessive energy "covered" by the contract's origin account or otherwise distorting resource/fee accounting for a state transition that is executed by any unprivileged caller of that contract. This falls in the accepted category of "resource and reward accounting" / "TVM execution and energy metering" corruption reachable via ordinary broadcast transactions (deploy + call the contract).

### Likelihood Explanation
Likelihood is high for the trigger condition itself: any account that deployed (or otherwise recorded as the origin of) a smart contract can send a single `UpdateEnergyLimitContract` transaction with an arbitrarily large value at zero extra cost (`calcFee()` returns 0), with no governance or committee approval required, unlike the acknowledged original finding which required a trusted contract owner. The actual severity of the accounting distortion depends on how the energy-metering code in `VMActuator`/`ReceiptCapsule` consumes this field, which was not fully traced in this analysis; I could not confirm the precise arithmetic effect (e.g., whether it can cause an overflow, a fee-bypass, or merely an economically irrational but harmless-to-consensus setting).

### Recommendation
Add an explicit upper bound to `origin_energy_limit` validation in `UpdateEnergyLimitContractActuator.validate()`, e.g., clamp it to a defined maximum (mirroring the existing `[0, 100]` style guard in `UpdateSettingContractActuator`), and reject values that would make later `long` arithmetic in `VMActuator`/`ReceiptCapsule` involving this field susceptible to overflow.

### Proof of Concept
1. Deploy a smart contract as account `A` (origin address = `A`).
2. Broadcast an `UpdateEnergyLimitContract` transaction from `A` with `origin_energy_limit = Long.MAX_VALUE`.
3. `UpdateEnergyLimitContractActuator.validate()` only checks `newOriginEnergyLimit <= 0`, so the transaction passes: [1](#0-0) 
4. `execute()` persists the unbounded value into the contract's proto state: [5](#0-4) 
5. Subsequent calls into this contract by any caller will be metered against this unbounded origin energy limit inside energy-metering logic (`VMActuator.java`, `ReceiptCapsule.java`), whose exact arithmetic effect on fee/energy apportionment was not fully verified in this analysis and would need direct tracing/testing to confirm exploitability beyond the missing-bounds defect itself.

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

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateEnergyLimitContractActuator.java (L111-118)
```java
    byte[] deployedContractOwnerAddress = deployedContract.getInstance().getOriginAddress()
        .toByteArray();

    if (!Arrays.equals(ownerAddress, deployedContractOwnerAddress)) {
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + "] is not the owner of the contract");
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
