### Title
Mempool Front-Running of `CreateSmartContract` Deployment via Predictable Contract Address Squatting - (File: `actuator/src/main/java/org/tron/core/actuator/VMActuator.java`)

### Summary
`VMActuator.create()` derives the new contract's address deterministically from the transaction ID and owner address, then aborts the entire deployment if an account already occupies that address [1](#0-0) . Because the address is computed only from `sha3omit12(txId || ownerAddress)` [2](#0-1) , once a signed `CreateSmartContract` transaction is visible in the mempool (before block inclusion), any observer can compute the exact target address and race it. `CreateAccountActuator` allows any funded actor to create an account at an arbitrary address of their choosing, with no requirement that the caller control or predict that address for any legitimate reason [3](#0-2) . This lets an attacker "squat" the precomputed address before the legitimate deployment lands, causing the deployment to fail.

### Finding Description
This is the same bug class as the external report: a contract-creation flow computes a deterministic target address and then checks "existing address" state elsewhere without accounting for front-running of that predictable address by an unrelated, cheaper transaction.

1. When a user broadcasts a `CreateSmartContract` transaction, `WalletUtil.generateContractAddress(trx)` computes the deploy address as `sha3omit12(txId || ownerAddress)` [2](#0-1) . Once the transaction is signed, `txId` is fixed, so the resulting address is fully determined and can be computed by anyone who observes the pending transaction (e.g., in the P2P mempool prior to block inclusion).
2. `VMActuator.create()` then checks whether an account already exists at that computed address, and if so throws `ContractValidateException`, aborting the whole deployment [1](#0-0) .
3. `CreateAccountActuator.validate()`/`execute()` place no restriction on which address the caller can register an account at — the caller only needs to pay `calcFee()` from their own balance; there is no proof-of-ownership or relationship to `accountAddress` required [4](#0-3) .

Combining these two reachable, unprivileged actuators: an attacker who observes a pending `CreateSmartContract` transaction can precompute its future contract address and submit a cheap `AccountCreateContract` transaction targeting that exact address before the victim's transaction is confirmed. When the victim's transaction is processed, `VMActuator.create()`'s existing-account check trips and the deployment fails.

### Impact Explanation
This directly mirrors the report's root cause (predictable CREATE-derived address combined with an external state check that reverts on pre-existing address, exploitable by a front-runner) applied to java-tron's native contract-creation path. Impact is a repeatable, unprivileged deployment-DoS: any address planted this way blocks that specific deployment attempt, forcing the victim to resubmit (burning bandwidth/fee each time), and a persistent attacker monitoring the mempool can grief targeted deployers indefinitely. It is weaker than the original finding in one respect — because the derived address also depends on the transaction hash (which changes per resubmission), the block is not permanent for the exact same content, but the attack is trivially repeatable each time the victim resubmits with the same content characteristics observable pre-confirmation, so it functions as an ongoing griefing/DoS vector against affected deployers rather than a one-time permanent doss.

### Likelihood Explanation
Likelihood is moderate-to-high: it only requires (a) visibility into pending transactions before confirmation (any node on the P2P network can observe broadcast transactions), (b) enough TRX to pay the `AccountCreateContract` fee, and (c) the ability to precompute `sha3omit12(txId || ownerAddress)`, which is public math with no cryptographic secrets involved. No privileged role, leaked key, or special node access is required.

### Recommendation
Do not allow a bare account-creation (`CreateAccountActuator`) to permanently block a `CreateSmartContract` deployment at a colliding address. Options include: treating a pre-existing plain `AccountType.Normal` account at the target address as compatible with contract deployment (upgrading it in place, as is already done for CREATE2 collisions and in `HistoryBlockHashUtil.deploy()`'s foreign-account branch [5](#0-4) ) instead of unconditionally rejecting deployment in `VMActuator.create()` [1](#0-0) ; only reject when actual contract code/state already exists at the address.

### Proof of Concept
1. Victim signs and broadcasts a `CreateSmartContract` transaction; the transaction propagates through the P2P network prior to block inclusion.
2. Attacker observes the pending transaction, extracts `txId` and `ownerAddress`, and computes `contractAddress = sha3omit12(txId || ownerAddress)` using the same formula as `WalletUtil.generateContractAddress` [2](#0-1) .
3. Attacker broadcasts an `AccountCreateContract` transaction with `accountAddress = contractAddress`, paying the account-creation fee from their own account [6](#0-5) ; this transaction is not required to be related to the victim in any way and will succeed as long as it lands in an earlier or the same block ahead of the victim's transaction.
4. When the victim's `CreateSmartContract` transaction executes, `VMActuator.create()` finds `rootRepository.getAccount(contractAddress) != null` and throws `ContractValidateException("Trying to create a contract with existing contract address...")`, failing the deployment [1](#0-0) .

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

**File:** actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java (L41-48)
```java
      AccountCreateContract accountCreateContract = any.unpack(AccountCreateContract.class);
      boolean withDefaultPermission =
          dynamicStore.getAllowMultiSign() == 1;
      AccountCapsule accountCapsule = new AccountCapsule(accountCreateContract,
          dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);

      accountStore
          .put(accountCreateContract.getAccountAddress().toByteArray(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java (L91-121)
```java
    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);
    if (accountCapsule == null) {
      String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);
      throw new ContractValidateException(
          ActuatorConstant.ACCOUNT_EXCEPTION_STR
              + readableOwnerAddress + NOT_EXIST_STR);
    }

    final long fee = calcFee();
    if (accountCapsule.getBalance() < fee) {
      throw new ContractValidateException(
          "Validate CreateAccountActuator error, insufficient fee.");
    }

    byte[] accountAddress = contract.getAccountAddress().toByteArray();
    if (!DecodeUtil.addressValid(accountAddress)) {
      throw new ContractValidateException("Invalid account address");
    }

//    if (contract.getType() == null) {
//      throw new ContractValidateException("Type is null");
//    }

    if (accountStore.has(accountAddress)) {
      throw new ContractValidateException("Account has existed");
    }
```

**File:** framework/src/main/java/org/tron/core/db/HistoryBlockHashUtil.java (L113-121)
```java
    AccountCapsule account = manager.getAccountStore().get(HISTORY_STORAGE_ADDRESS);
    boolean accountExisting = account != null;
    if (!accountExisting) {
      account = new AccountCapsule(HISTORY_STORAGE_ACCOUNT);
    } else {
      account.updateAccountType(Protocol.AccountType.Contract);
      account.clearDelegatedResource();
    }
    manager.getAccountStore().put(HISTORY_STORAGE_ADDRESS, account);
```
