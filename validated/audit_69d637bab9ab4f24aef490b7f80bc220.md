### Title
Contract deployment can be permanently griefed via front-run funding of the deterministic contract address - (File: actuator/src/main/java/org/tron/core/actuator/VMActuator.java)

### Summary
`VMActuator.create()` computes the address for a new smart contract deterministically and then blocks deployment if any account already exists at that address. Because the derivation of the address depends only on transaction data that is visible in the network before the transaction is confirmed (or is even fully deterministic once the raw transaction is known), any unprivileged party observing a pending `CreateSmartContract` transaction can pre-fund (or otherwise create an account at) the target address, causing the legitimate deployer's transaction to fail. This is the same bug class as the reported Controller.sol issue: a core state-changing function is gated on an account/balance property of an address that an outside, unprivileged actor can manipulate by sending value to it.

### Finding Description
In `create()`:
```java
byte[] contractAddress = WalletUtil.generateContractAddress(trx);
// insure the new contract address haven't exist
if (rootRepository.getAccount(contractAddress) != null) {
  throw new ContractValidateException(
      "Trying to create a contract with existing contract address: " + StringUtil
          .encode58Check(contractAddress));
}
``` [1](#0-0) 

`WalletUtil.generateContractAddress(trx)` derives the address from the transaction's raw-data hash and the owner address:
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
``` [2](#0-1) 

Since this address is fully computable by anyone who observes the broadcast `CreateSmartContract` transaction while it is pending in the mempool (before it is packed into a block), an attacker can broadcast a competing, ordinary `TransferContract` (or `TransferAssetContract`) sending a trivial amount of TRX to that computed address so that `rootRepository.getAccount(contractAddress)` returns non-null once processed. If the attacker's transfer transaction is confirmed in an earlier block/position than the deployer's transaction, the deployer's `CreateSmartContract` execution reaches the `contractAddress` existence check and throws `ContractValidateException`, aborting the deployment (the fee/energy already spent up to that point is still consumed; execution never installs the contract). This mirrors the `Controller.sol` bug class: a security-critical branch is gated on the state (existence/balance) of an address that an unrelated, unprivileged party can mutate by simply sending funds to it, letting that party interfere with a core function (contract creation) belonging to someone else.

Note: because the target address is bound to the specific raw-data hash of the deployer's transaction (which embeds `ref_block`, `expiration`, `timestamp`, `fee_limit`, etc.), each failed/retried deployment attempt produces a new hash and hence a new target address, so the attacker must observe and re-grief every retry. It is a repeatable griefing/DoS vector rather than a single irrecoverable block, but it is fully reachable from an anonymous broadcast transaction and requires no special privilege.

### Impact Explanation
An attacker monitoring the P2P/mempool for pending contract-creation transactions can selectively deny/delay deployment of specific smart contracts by any user, by paying only a negligible TRX transfer fee each time. This is a denial-of-service against the protocol's contract-creation path reachable purely through the normal broadcast-transaction flow, with no privileged access required.

### Likelihood Explanation
Exploitation requires the attacker to observe a pending `CreateSmartContract` transaction (visible in the node's transaction pool prior to inclusion) and to win a minor race to have their funding transaction processed first. This is feasible for a motivated attacker who runs a node/relay and monitors incoming transactions, similar to well-known front-running/griefing patterns on other chains; likelihood is moderate and depends on network conditions/latency rather than any cryptographic or permission barrier.

### Recommendation
Do not gate the deployment purely on `getAccount(contractAddress) != null`. Instead, mirror the mitigation already applied to the internal `CREATE`/`CREATE2` opcode path in `Program.createContractImpl`, which distinguishes "an account that merely received TRX" from "an actual deployed contract" (using `isContractExist`, i.e. checking for a `ContractCapsule` at the address rather than mere account existence) before rejecting the deployment:
```java
AccountCapsule existingAccount = getContractState().getAccount(newAddress);
boolean contractAlreadyExists = existingAccount != null;
if (VMConfig.allowTvmConstantinople()) {
  contractAlreadyExists =
      contractAlreadyExists && isContractExist(existingAccount, getContractState());
}
``` [3](#0-2) 
The top-level `VMActuator.create()` check should use the same "is it actually a contract" test (`getContract(contractAddress) != null`) rather than "does any account exist at this address", so that a pre-funded (but code-less) address does not block legitimate deployment.

### Proof of Concept
1. Attacker runs a node/relay and watches the transaction pool for `CreateSmartContract` transactions.
2. On observing one, attacker computes `contractAddress = sha3omit12(txRawDataHash || ownerAddress)` exactly as `WalletUtil.generateContractAddress` does.
3. Attacker broadcasts a `TransferContract` sending 1 sun to `contractAddress` with a higher fee/priority so it is packed into an earlier block.
4. When the victim's `CreateSmartContract` transaction executes, `rootRepository.getAccount(contractAddress) != null` is now true, and `VMActuator.create()` throws `ContractValidateException("Trying to create a contract with existing contract address: ...")`, aborting the deployment. [1](#0-0)

### Citations

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

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L827-833)
```java
    AccountCapsule existingAccount = getContractState().getAccount(newAddress);
    boolean contractAlreadyExists = existingAccount != null;

    if (VMConfig.allowTvmConstantinople()) {
      contractAlreadyExists =
          contractAlreadyExists && isContractExist(existingAccount, getContractState());
    }
```
