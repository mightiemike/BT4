## #Analog Found

### Title
Missing "old TVM version" recipient validation in `TransferAssetActuator` allows TRC10 tokens to be locked in legacy smart contracts - (File: `actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java`)

### Summary
`TransferActuator` (native TRX transfers) validates that a recipient is not a smart contract compiled with the legacy TVM version ("contractVersion == 1") whenever `AllowTvmCompatibleEvm` is enabled, rejecting the transfer outright. `TransferAssetActuator` (TRC10 token transfers) only replicates the `ForbidTransferToContract` check but omits this second, symmetric check, so TRC10 tokens can still be sent directly to legacy version-1 contract addresses that are unable to properly account for or move the received TRC10 balance out again.

### Finding Description
In `TransferActuator.validate()`, two independent recipient restrictions are enforced when the destination account is of `AccountType.Contract`: [1](#0-0) 

1. `ForbidTransferToContract` — blocks any transfer to a contract account.
2. `AllowTvmCompatibleEvm` + `contractCapsule.getContractVersion() == 1` — blocks transfers to contracts deployed under the old TVM version, explicitly directing callers to use `TriggerSmartContract` instead.

`TransferAssetActuator.validate()` (the TRC10 analog of `TransferActuator`) only implements the first restriction: [2](#0-1) 

There is no equivalent check for `AllowTvmCompatibleEvm`/contract version 1 in the asset-transfer path, and `execute()` performs the balance credit to the recipient (`toAccountCapsule.addAssetAmountV2(...)`) unconditionally once validation passes: [3](#0-2) 

This mirrors the reported bug class exactly: a restriction meant to prevent sending value to a recipient that cannot properly handle/return it (analogous to a "blacklisted" recipient) is enforced for one transfer type (TRX) but omitted for the structurally identical sibling transfer type (TRC10 assets), letting funds land in an address from which they cannot be normally recovered.

### Impact Explanation
Old (version-1) TVM contracts predate `AllowTvmCompatibleEvm`-era TRC10 handling semantics; the `TransferActuator` check exists precisely because such contracts are not expected to correctly track/forward TRC10-style value sent outside of `TriggerSmartContract` invocation. Because `TransferAssetActuator` skips this same check, any user (anonymous broadcast transaction, no privileged actor required) can send `TransferAssetContract` transactions of TRC10 tokens to a legacy version-1 contract address, causing those tokens to become effectively unrecoverable/stuck in the contract's balance, since the contract code was never written to expect or externally forward such value. This is a straightforward asset-locking / accounting-corruption class impact, reachable purely through a normal broadcast transaction.

### Likelihood Explanation
High likelihood of reachability: `TransferAssetContract` is a standard, unauthenticated transaction type processed by any full node; no special permissions, precompile access, or contract deployment is required from the attacker/victim beyond simply knowing (or being tricked into using) a legacy contract address as the `to_address`. The only precondition is that `AllowTvmCompatibleEvm` is active on the network (already a live committee-controlled proposal) and that a version-1 contract exists — both are ordinary network states, not privileged conditions.

### Recommendation
Add the same `AllowTvmCompatibleEvm` + `ContractCapsule.getContractVersion() == 1` check to `TransferAssetActuator.validate()` that already exists in `TransferActuator.validate()`, throwing a `ContractValidateException` directing the caller to use `TriggerSmartContract` for TRC10 transfers to such contracts, keeping both transfer actuators' recipient validation symmetric.

### Proof of Concept
1. Deploy (or identify) a smart contract with `contractVersion == 1` (pre-`AllowTvmCompatibleEvm` compiled contract) at address `C`.
2. Ensure `AllowTvmCompatibleEvm` is enabled via committee proposal (network already supports it, as used by `TransferActuator`).
3. Broadcast a `TransferAssetContract` from any funded account, setting `to_address = C` and a valid `asset_name`/`amount`.
4. Observe that `TransferAssetActuator.validate()` only checks `ForbidTransferToContract` (line 172-175) and does not reject based on contract version; `execute()` credits the TRC10 balance to `C`'s `AssetV2` map.
5. Because `C`'s bytecode predates version-1 compatibility handling for such transfers, it has no way to move or acknowledge the TRC10 balance, permanently locking the transferred tokens — reproducing the "transfer to unusable recipient" bug class from the external report, but for TRC10 assets instead of the ERC20-style `transfer()`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L132-156)
```java
      //after ForbidTransferToContract proposal, send trx to smartContract by actuator is not allowed.
      if (dynamicStore.getForbidTransferToContract() == 1
          && toAccount != null
          && toAccount.getType() == AccountType.Contract) {

        throw new ContractValidateException("Cannot transfer TRX to a smartContract.");

      }

      // after AllowTvmCompatibleEvm proposal, send trx to smartContract which version is one
      // by actuator is not allowed.
      if (dynamicStore.getAllowTvmCompatibleEvm() == 1
          && toAccount != null
          && toAccount.getType() == AccountType.Contract) {

        ContractCapsule contractCapsule = chainBaseManager.getContractStore().get(toAddress);
        if (contractCapsule == null) { //  this can not happen
          throw new ContractValidateException(
              "Account type is Contract, but it is not exist in contract store.");
        } else if (contractCapsule.getContractVersion() == 1) {
          throw new ContractValidateException(
              "Cannot transfer TRX to a smartContract which version is one. "
                  + "Instead please use TriggerSmartContract ");
        }
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L60-84)
```java
      byte[] ownerAddress = transferAssetContract.getOwnerAddress().toByteArray();
      byte[] toAddress = transferAssetContract.getToAddress().toByteArray();
      AccountCapsule toAccountCapsule = accountStore.get(toAddress);
      if (toAccountCapsule == null) {
        boolean withDefaultPermission =
            dynamicStore.getAllowMultiSign() == 1;
        toAccountCapsule = new AccountCapsule(ByteString.copyFrom(toAddress), AccountType.Normal,
            dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);
        accountStore.put(toAddress, toAccountCapsule);

        fee = fee + dynamicStore.getCreateNewAccountFeeInSystemContract();
      }
      ByteString assetName = transferAssetContract.getAssetName();
      long amount = transferAssetContract.getAmount();

      AccountCapsule ownerAccountCapsule = accountStore.get(ownerAddress);
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
      accountStore.put(ownerAddress, ownerAccountCapsule);

      toAccountCapsule
          .addAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore);
      accountStore.put(toAddress, toAccountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L169-176)
```java
    AccountCapsule toAccount = accountStore.get(toAddress);
    if (toAccount != null) {
      //after ForbidTransferToContract proposal, send trx to smartContract by actuator is not allowed.
      if (dynamicStore.getForbidTransferToContract() == 1
          && toAccount.getType() == AccountType.Contract) {
        throw new ContractValidateException("Cannot transfer asset to smartContract.");
      }

```
