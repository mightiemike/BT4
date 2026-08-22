### Title
Front-runnable contract-address pre-creation causes permanent DoS of `CreateSmartContract` deployments - ([File: actuator/src/main/java/org/tron/core/actuator/VMActuator.java])

### Summary
`VMActuator.create()` computes the new contract's address deterministically from the pending transaction and rejects deployment if an account already occupies that address. An attacker who observes a broadcast (pending, not-yet-confirmed) `CreateSmartContract` transaction can compute the same address and race a trivial `TransferContract` to that address, which silently creates a `Normal` account there. When the original deployment transaction is later processed, it fails, and the deployer's fee/bandwidth is still consumed with no contract deployed. This is the same bug class as the reported `pump-science` `lock_pool` issue: a permissionless, signature-free account-creation path at a deterministically derivable address blocks a subsequent legitimate creation.

### Finding Description
The contract address for a `CreateSmartContract` transaction is computed as `sha3omit12(txRawDataHash || ownerAddress)`: [1](#0-0) 

This exact computation is repeated in `VMActuator.create()`, which then rejects the whole transaction if that address is already occupied: [2](#0-1) 

The `txRawDataHash` is simply the hash of the `CreateSmartContract` transaction's `raw_data` (essentially its transaction ID) — it is fully determined the moment the transaction is signed/broadcast, i.e., before it is confirmed in a block. Any node or client observing the transaction in the pending pool (via the broadcast/gRPC/HTTP APIs, or normal P2P propagation once it enters a node's pending pool) can therefore compute the future contract address in advance, exactly analogous to how the `lock_escrow` address in the reported bug is derivable from `pool`/`owner` seeds before the real transaction executes.

Any unprivileged account can then create an account at that exact address with no special privilege and no signature from the eventual contract owner, simply by sending it TRX via an ordinary `TransferContract`: [3](#0-2) 

or an asset transfer with the same implicit-creation behavior: [4](#0-3) 

Once the address exists as a `Normal` account, the later `CreateSmartContract` execution hits the existence check in `VMActuator.create()` and throws `ContractValidateException("Trying to create a contract with existing contract address: ...")`, aborting the deployment.

This mirrors the reported root cause precisely: a deterministic, signature-free, permissionless account-creation primitive (Solana `create_lock_escrow` PDA vs. Tron's implicit account creation in `TransferContract`/`TransferAssetContract`) can be raced ahead of the legitimate creation flow (`lock_pool`'s escrow creation vs. `VMActuator.create()`'s contract deployment), causing the legitimate flow to fail.

### Impact Explanation
A malicious actor monitoring the transaction pool can selectively block specific contract deployments by any address, at the cost of a trivial TRX transfer (potentially near-zero amount, plus standard transfer fee). The victim's `CreateSmartContract` transaction still gets included in a block and its fee/bandwidth/energy is consumed, but the deployment fails — a denial of service against contract creators, and a griefing vector that can be used, e.g., to block a competitor's contract launch at a predictable time. This affects the actuator/contract-creation state-transition path reachable directly from any broadcast transaction, matching the "unauthorized account operation / DoS via protocol implementation" acceptance bar.

### Likelihood Explanation
Exploitation requires only: (1) observing a pending `CreateSmartContract` transaction (visible in mempool/broadcast APIs before confirmation), (2) recomputing the deterministic address using the same formula as `WalletUtil.generateContractAddress`, and (3) broadcasting a cheap `TransferContract`/`TransferAssetContract` to that address with sufficient fee/priority to land in an earlier or same block. All of this uses standard, unprivileged wallet operations — no special access, keys, or node privilege is needed. The main constraint is winning the block-inclusion race, which is a common front-running pattern already assumed feasible in blockchain threat models.

### Recommendation
- In `VMActuator.create()`, distinguish "address occupied by a pre-existing Normal/empty account with no code" from "address already hosts a real contract." Allow contract creation to proceed and take over an existing non-contract account (similar to the Constantinople-style handling already implemented for the internal `CREATE`/`CREATE2` path in `Program.createContractImpl`, which checks `isContractExist` rather than merely account existence) instead of unconditionally rejecting: [5](#0-4) 
- Alternatively/additionally, only reject top-level `CreateSmartContract` deployment when the target address already has deployed code/contract metadata (`ContractStore` entry), not merely an `AccountStore` entry, aligning the outer-transaction path with the internal VM `CREATE` path's existing collision-tolerant logic.

### Proof of Concept
1. Attacker (or anyone) monitors the network/mempool for a pending `CreateSmartContract` transaction from victim address `V`.
2. Attacker computes `contractAddress = sha3omit12(sha256(rawData) || V)` exactly as done in `WalletUtil.generateContractAddress` [1](#0-0) .
3. Attacker broadcasts a `TransferContract` sending a minimal amount of TRX to `contractAddress`, which is processed by `TransferActuator.execute()` and implicitly creates a `Normal` `AccountCapsule` there [3](#0-2) .
4. If this transfer is confirmed before or in the same block as the victim's deployment, when `VMActuator.create()` runs for the victim's transaction, `rootRepository.getAccount(contractAddress) != null` is true, and it throws `ContractValidateException("Trying to create a contract with existing contract address: ...")` [2](#0-1) , causing the deployment transaction to fail while consuming the victim's fee.

### Citations

**File:** chainbase/src/main/java/org/tron/common/utils/WalletUtil.java (L39-52)
```java
  public static byte[] generateContractAddress(Transaction trx) {

    CreateSmartContract contract = ContractCapsule.getSmartContractFromTransaction(trx);
    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    TransactionCapsule trxCap = new TransactionCapsule(trx);
    byte[] txRawDataHash = trxCap.getTransactionId().getBytes();

    byte[] combined = new byte[txRawDataHash.length + ownerAddress.length];
    System.arraycopy(txRawDataHash, 0, combined, 0, txRawDataHash.length);
    System.arraycopy(ownerAddress, 0, combined, txRawDataHash.length, ownerAddress.length);

    return Hash.sha3omit12(combined);

  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/VMActuator.java (L355-361)
```java
    byte[] contractAddress = WalletUtil.generateContractAddress(trx);
    // insure the new contract address haven't exist
    if (rootRepository.getAccount(contractAddress) != null) {
      throw new ContractValidateException(
          "Trying to create a contract with existing contract address: " + StringUtil
              .encode58Check(contractAddress));
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferActuator.java (L48-58)
```java
      // if account with to_address does not exist, create it first.
      AccountCapsule toAccount = accountStore.get(toAddress);
      if (toAccount == null) {
        boolean withDefaultPermission =
            dynamicStore.getAllowMultiSign() == 1;
        toAccount = new AccountCapsule(ByteString.copyFrom(toAddress), AccountType.Normal,
            dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);
        accountStore.put(toAddress, toAccount);

        fee = fee + dynamicStore.getCreateNewAccountFeeInSystemContract();
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L62-71)
```java
      AccountCapsule toAccountCapsule = accountStore.get(toAddress);
      if (toAccountCapsule == null) {
        boolean withDefaultPermission =
            dynamicStore.getAllowMultiSign() == 1;
        toAccountCapsule = new AccountCapsule(ByteString.copyFrom(toAddress), AccountType.Normal,
            dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);
        accountStore.put(toAddress, toAccountCapsule);

        fee = fee + dynamicStore.getCreateNewAccountFeeInSystemContract();
      }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L827-843)
```java
    AccountCapsule existingAccount = getContractState().getAccount(newAddress);
    boolean contractAlreadyExists = existingAccount != null;

    if (VMConfig.allowTvmConstantinople()) {
      contractAlreadyExists =
          contractAlreadyExists && isContractExist(existingAccount, getContractState());
    }
    Repository deposit = getContractState().newRepositoryChild();
    if (VMConfig.allowTvmConstantinople()) {
      if (existingAccount == null) {
        deposit.createAccount(newAddress, "CreatedByContract",
            AccountType.Contract);
      } else if (!contractAlreadyExists) {
        existingAccount.updateAccountType(AccountType.Contract);
        existingAccount.clearDelegatedResource();
        deposit.updateAccount(newAddress, existingAccount);
      }
```
