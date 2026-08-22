### Title
Permanent loss of energy fee-sharing control due to immutable `origin_address` binding with no reassignment path - (File: `actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java`)

### Summary
Java-tron binds a smart contract's `SmartContract.origin_address` permanently to the deploying account at creation time. All subsequent fee/energy-sharing configuration for that contract — `consume_user_resource_percent` via `UpdateSettingContract` and `origin_energy_limit` via `UpdateEnergyLimitContract` — is gated by an exact match against this fixed `origin_address`. There is no actuator or mechanism to reassign/re-register this origin binding to a new account. This mirrors the Canto Turnstile bug class: once the controlling identity (there, an NFT owner; here, the `origin_address` account/key) is lost, transferred, or compromised, the protocol-level fee/resource-sharing configuration for the contract becomes permanently unmanageable.

### Finding Description
`SmartContract.origin_address` is set once when a contract is deployed and stored immutably in `ContractStore` [1](#0-0) .

`UpdateSettingContractActuator.validate()` strictly requires the transaction's `owner_address` to equal the contract's stored `origin_address` before allowing a change to `consume_user_resource_percent`: [2](#0-1) 

This `origin_address` also drives energy-fee accounting in `VMActuator.getTotalEnergyLimitWithFixRatio`/`getTotalEnergyLimitWithFloatRatio`, where the "creator" (origin) account pays/receives energy usage share based on `consumeUserResourcePercent` and `originEnergyLimit` [3](#0-2) , and in `TransactionTrace.pay()`/`ReceiptCapsule.payEnergyBill`, which resolve the `originAccount` directly from `contractCapsule.getOriginAddress()` [4](#0-3) .

There is no protocol-level actuator (searched across all `*Actuator*.java` files and the API/HTTP surfaces) that permits changing `origin_address` after deployment. If the origin account's private key is lost or compromised, or if the deploying entity wants to hand off control of the contract's resource-fee configuration to a new operator, there is no way to migrate this binding — the account tied to `origin_address` remains permanently authoritative for `UpdateSettingContract`/`UpdateEnergyLimitContract`, and permanently on the hook (or permanently unable) to receive/adjust the energy-fee share, exactly analogous to the Turnstile NFT registration losing all future control once the NFT changes hands with no re-register function.

### Impact Explanation
If the `origin_address` private key is lost, or the deploying account is later found to be compromised, the contract's energy fee-sharing configuration (`consume_user_resource_percent`, `origin_energy_limit`) becomes permanently frozen at its last-configured values — no legitimate operator can ever adjust it again via `UpdateSettingContract`/`UpdateEnergyLimitContract`. This is a real (though lower-severity, matching the acknowledged "known limitation" severity of the original finding) accounting/configuration availability issue reachable purely via normal broadcast transactions, with no privileged actor or off-chain compromise required to trigger the *impact* (only the root cause — key loss/compromise — is external).

### Likelihood Explanation
Likelihood is moderate: this requires the specific circumstance of losing access to (or wanting to transfer control away from) the deploying/`origin` account, which is a realistic operational scenario for long-lived production contracts (key rotation, organizational changes, compromised deployer keys). Given java-tron's widespread use for long-running smart contracts, this scenario is plausible over time, matching the original report's characterization as a real but currently accepted limitation.

### Recommendation
Consider adding a governed re-assignment mechanism for `origin_address` — e.g., a new actuator (`UpdateOriginAddressContract` or similar) that allows the current `origin_address` holder to transfer origin rights to a new address (subject to the same ownership check used in `UpdateSettingContractActuator`), so contract resource-fee configuration is not permanently orphaned if the original deployer key becomes inaccessible.

### Proof of Concept
1. Deploy a smart contract with `CreateSmartContract`, setting `origin_address = A` and some `consume_user_resource_percent`.
2. Lose access to (or compromise) account `A`'s private key.
3. Attempt to call `UpdateSettingContract` or `UpdateEnergyLimitContract` from any other address `B` to adjust the contract's fee-sharing settings.
4. `UpdateSettingContractActuator.validate()` rejects the transaction with `"Account[B] is not the owner of the contract"` [5](#0-4)  because no logic anywhere in the codebase permits changing `origin_address` after deployment, permanently locking the contract's energy-fee configuration to the now-inaccessible `A`.

### Citations

**File:** protocol/src/main/protos/core/contract/smart_contract.proto (L48-59)
```text
  bytes origin_address = 1;
  bytes contract_address = 2;
  ABI abi = 3;
  bytes bytecode = 4;
  int64 call_value = 5;
  int64 consume_user_resource_percent = 6;
  string name = 7;
  int64 origin_energy_limit = 8;
  bytes code_hash = 9;
  bytes trx_hash = 10;
  int32 version = 11;
}
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateSettingContractActuator.java (L99-113)
```java
    byte[] contractAddress = contract.getContractAddress().toByteArray();
    ContractCapsule deployedContract = contractStore.get(contractAddress);

    if (deployedContract == null) {
      throw new ContractValidateException(
          "Contract does not exist");
    }

    byte[] deployedContractOwnerAddress = deployedContract.getInstance().getOriginAddress()
        .toByteArray();

    if (!Arrays.equals(ownerAddress, deployedContractOwnerAddress)) {
      throw new ContractValidateException(
          ACCOUNT_EXCEPTION_STR + readableOwnerAddress + "] is not the owner of the contract");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L724-757)
```java
    long creatorEnergyLimit = 0;
    ContractCapsule contractCapsule = rootRepository
        .getContract(contract.getContractAddress().toByteArray());
    long consumeUserResourcePercent = contractCapsule.getConsumeUserResourcePercent(
        VMConfig.disableJavaLangMath());

    long originEnergyLimit = contractCapsule.getOriginEnergyLimit();
    if (originEnergyLimit < 0) {
      throw new ContractValidateException("originEnergyLimit can't be < 0");
    }

    long originEnergyLeft = 0;
    if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
      originEnergyLeft = rootRepository.getAccountLeftEnergyFromFreeze(creator);
      if (VMConfig.allowTvmFreeze() || VMConfig.allowTvmFreezeV2()) {
        receipt.setOriginEnergyLeft(originEnergyLeft);
      }
    }
    if (consumeUserResourcePercent <= 0) {
      creatorEnergyLimit = min(originEnergyLeft, originEnergyLimit,
          VMConfig.disableJavaLangMath());
    } else {
      if (consumeUserResourcePercent < VMConstant.ONE_HUNDRED) {
        // creatorEnergyLimit =
        // min(callerEnergyLimit * (100 - percent) / percent,
        //   creatorLeftFrozenEnergy, originEnergyLimit)

        creatorEnergyLimit = min(
            BigInteger.valueOf(callerEnergyLimit)
                .multiply(BigInteger.valueOf(VMConstant.ONE_HUNDRED - consumeUserResourcePercent))
                .divide(BigInteger.valueOf(consumeUserResourcePercent)).longValueExact(),
            min(originEnergyLeft, originEnergyLimit, VMConfig.disableJavaLangMath()),
            VMConfig.disableJavaLangMath());
      }
```

**File:** chainbase/src/main/java/org/tron/core/db/TransactionTrace.java (L239-252)
```java
      case TRX_CONTRACT_CALL_TYPE:
        TriggerSmartContract callContract = ContractCapsule
            .getTriggerContractFromTransaction(trx.getInstance());
        ContractCapsule contractCapsule =
            contractStore.get(callContract.getContractAddress().toByteArray());

        callerAccount = callContract.getOwnerAddress().toByteArray();
        originAccount = contractCapsule.getOriginAddress();
        boolean disableJavaLangMath = dynamicPropertiesStore.disableJavaLangMath();
        percent = max(Constant.ONE_HUNDRED - contractCapsule.getConsumeUserResourcePercent(
            disableJavaLangMath), 0, disableJavaLangMath);
        percent = min(percent, Constant.ONE_HUNDRED,
            disableJavaLangMath);
        originEnergyLimit = contractCapsule.getOriginEnergyLimit();
```
