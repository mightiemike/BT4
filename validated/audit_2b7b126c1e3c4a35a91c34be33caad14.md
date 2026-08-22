### Title
Front-runnable deterministic contract address allows griefing DoS against pending `CreateSmartContract` deployments - (`actuator/src/main/java/org/tron/core/actuator/VMActuator.java`)

### Summary
`VMActuator.create()` deterministically derives the address of a to-be-deployed contract from the transaction's own id and the deployer's owner address, and aborts the entire deployment if an account already occupies that address. Because the address can be computed by anyone who observes the unconfirmed (but already signed/broadcast) `CreateSmartContract` transaction in the mempool, an attacker can front-run it with a cheap `AccountCreateContract` transaction targeting the exact same address, permanently causing the victim's deployment transaction to fail validation. This mirrors the reported `WeightedIndex`/`createV2Pool` bug class: a deterministic, pre-computable target address whose prior existence makes contract creation always revert.

### Finding Description
When a `CreateSmartContract` transaction is executed, the new contract's address is computed as: [1](#0-0) 

i.e. `sha3(txRawDataHash || ownerAddress)`. The transaction id (`txRawDataHash`) is fully determined by the raw transaction data (owner address, bytecode, fee limit, ref block, expiration, timestamp) and does **not** depend on the signature. Once a user signs and broadcasts a `CreateSmartContract` transaction, any observer of the mempool/gRPC broadcast can read the raw transaction, recompute its transaction id locally, and therefore compute the exact future contract address before it is confirmed on-chain.

`VMActuator.create()` then checks whether an account already exists at that computed address, and if so, unconditionally fails the whole deployment: [2](#0-1) 

An attacker can race the victim's pending transaction with a lightweight `AccountCreateContract` transaction that creates a normal account at the predicted contract address: [3](#0-2) 

Because the account-creation is comparatively cheap and reachable from any anonymous account via broadcastTransaction, if it lands in an earlier block than the victim's `CreateSmartContract`, the victim's transaction will deterministically hit `ContractValidateException("Trying to create a contract with existing contract address...")` in `VMActuator.create()` and fail — the transaction's hash (and thus the target address) cannot be changed without the victim resigning and rebroadcasting a brand-new transaction, so the attacker can repeat the griefing indefinitely.

This differs from the standard `CREATE`/`CREATE2` opcode collision handling inside VM execution (`Program.createContractImpl`), which merely fails the inner sub-call and lets the outer transaction continue; here the failure is at the top-level actuator, aborting the entire user transaction.

### Impact Explanation
This is a denial-of-service against a specific, targeted transaction/user: an attacker can indefinitely prevent a chosen account from deploying a smart contract via the standard `CreateSmartContract` transaction type, since the resulting address is deterministic and derivable purely from public mempool data before confirmation. This does not compromise consensus or funds but is a reliable griefing/DoS vector reachable purely through anonymous broadcast transactions.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the attacker to observe pending transactions before confirmation (mempool visibility) and win a block-inclusion race with a cheap transaction, which is realistic on any live network with public mempool propagation and modest attacker fee/priority tuning.

### Recommendation
Do not treat "target address already has an account" as an unconditional validation failure for top-level contract deployment. Options include: allowing deployment to proceed and upgrade a non-contract account in place (mirroring the `Constantinople`/CREATE2 collision handling already present in `Program.createContractImpl`, i.e. check `isContractExist` rather than mere account existence), or incorporating an unpredictable/private component (e.g., a user-supplied nonce/salt not derivable before broadcast) into the address derivation so it cannot be front-run.

### Proof of Concept
1. Victim signs and broadcasts a `CreateSmartContract` transaction `T` (contract address = `sha3(txid(T) || owner)`).
2. Attacker observes `T` in the mempool, computes `txid(T)` from its raw data, and derives the same target address via `WalletUtil.generateContractAddress`.
3. Attacker broadcasts an `AccountCreateContract` transaction creating a normal account at that address, with sufficient fee/priority to be confirmed before `T`.
4. When `T` executes, `VMActuator.create()` finds `rootRepository.getAccount(contractAddress) != null` and throws `ContractValidateException`, permanently failing the victim's deployment for that transaction hash.

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

**File:** actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java (L119-121)
```java
    if (accountStore.has(accountAddress)) {
      throw new ContractValidateException("Account has existed");
    }
```
