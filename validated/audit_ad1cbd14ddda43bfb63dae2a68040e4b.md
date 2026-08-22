### Title
Missing required-field validation in account registration allows creating accounts with empty name/type - ([File: actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java])

### Summary
`CreateAccountActuator.validate()` deliberately skips validation of the `accountName` and `type` fields of an `AccountCreateContract`, allowing any anonymous broadcast transaction to register a new on-chain account without providing these core identifying details. This mirrors the reported "appchain can be registered without core details" bug class: a registration-style actuator that omits enforcement of fields that other, similar actuators in the same codebase do enforce.

### Finding Description
`CreateAccountActuator` is the actuator invoked for `AccountCreateContract`, reachable directly from any broadcast transaction on the network (no privileged role required — any funded account can send this contract type). Its `validate()` method contains explicitly commented-out checks for the account name and account type fields: [1](#0-0) 

```java
final AccountCreateContract contract;
try {
  contract = this.any.unpack(AccountCreateContract.class);
} catch (InvalidProtocolBufferException e) {
  logger.debug(e.getMessage(), e);
  throw new ContractValidateException(e.getMessage());
}
//    if (contract.getAccountName().isEmpty()) {
//      throw new ContractValidateException("AccountName is null");
//    }
byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
...
//    if (contract.getType() == null) {
//      throw new ContractValidateException("Type is null");
//    }
```

There is no call to `TransactionUtil.validAccountName(...)` (used elsewhere) and no uniqueness check against `AccountIndexStore` at creation time. By contrast, the sibling actuator `UpdateAccountActuator` does enforce these constraints when an account name is later set: [2](#0-1) 

and other registration-style actuators (`WitnessCreateActuator`, `AssetIssueActuator`, `ExchangeCreateActuator`, `ProposalCreateActuator`) all validate their respective mandatory fields (URLs, token IDs, parameters, etc.) before persisting state: [3](#0-2) 

This inconsistency means `CreateAccountActuator.execute()` will happily build and persist an `AccountCapsule` with an empty name and an unset/default `type` field directly from attacker-controlled input: [4](#0-3) 

### Impact Explanation
Because account creation bypasses the same field checks that are enforced when updating an account, an attacker can flood the chain with accounts that have empty names and no explicit account type, and no uniqueness/index consistency is established at creation. This creates persisted, inconsistent state (accounts lacking the identifying data that downstream code, indexing, and off-chain tooling built on `AccountIndexStore`/account name assume exist) and permanently occupies address slots with malformed records, since account creation is otherwise irreversible on-chain. This does not rise to a full consensus-break or key-disclosure bug, but it is a concrete, exploitable state/accounting-integrity defect in a state-transition actuator reachable by any broadcast transaction.

### Likelihood Explanation
Very high. Any account holding enough balance to pay the account-creation fee can broadcast an `AccountCreateContract` with an empty `account_name` and default `type`, and the transaction will be accepted and executed with no additional privilege required.

### Recommendation
Re-enable and enforce the previously commented-out checks in `CreateAccountActuator.validate()`:
- Require `contract.getAccountName()` to be non-empty and pass `TransactionUtil.validAccountName(...)`, consistent with `UpdateAccountActuator`.
- Require `contract.getType()` to be a valid, expected `AccountType` value rather than allowing the default/unset value.

### Proof of Concept
1. Construct an `AccountCreateContract` with a valid `owner_address` (funded account) and a valid new `account_address`, but leave `account_name` empty (`ByteString.EMPTY`) and omit `type`.
2. Broadcast the transaction via any public full node RPC (`BroadcastTransaction`).
3. `CreateAccountActuator.validate()` passes because the name/type checks are commented out; `execute()` persists an `AccountCapsule` with empty name/default type to `AccountStore`, as demonstrated by the actuator's own unit test suite never exercising an empty-name rejection path (unlike `UpdateAccountActuatorTest.invalidName()`, which explicitly asserts `"Invalid accountName"` is thrown for `UpdateAccountActuator`): [5](#0-4)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java (L40-49)
```java
    try {
      AccountCreateContract accountCreateContract = any.unpack(AccountCreateContract.class);
      boolean withDefaultPermission =
          dynamicStore.getAllowMultiSign() == 1;
      AccountCapsule accountCapsule = new AccountCapsule(accountCreateContract,
          dynamicStore.getLatestBlockHeaderTimestamp(), withDefaultPermission, dynamicStore);

      accountStore
          .put(accountCreateContract.getAccountAddress().toByteArray(), accountCapsule);

```

**File:** actuator/src/main/java/org/tron/core/actuator/CreateAccountActuator.java (L81-117)
```java
    final AccountCreateContract contract;
    try {
      contract = this.any.unpack(AccountCreateContract.class);
    } catch (InvalidProtocolBufferException e) {
      logger.debug(e.getMessage(), e);
      throw new ContractValidateException(e.getMessage());
    }
//    if (contract.getAccountName().isEmpty()) {
//      throw new ContractValidateException("AccountName is null");
//    }
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java (L75-97)
```java
    byte[] ownerAddress = accountUpdateContract.getOwnerAddress().toByteArray();
    byte[] accountName = accountUpdateContract.getAccountName().toByteArray();
    if (!TransactionUtil.validAccountName(accountName)) {
      throw new ContractValidateException("Invalid accountName");
    }
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }

    AccountCapsule account = chainBaseManager.getAccountStore().get(ownerAddress);
    if (account == null) {
      throw new ContractValidateException("Account does not exist");
    }

    if (account.getAccountName() != null && !account.getAccountName().isEmpty()
        && chainBaseManager.getDynamicPropertiesStore().getAllowUpdateAccountName() == 0) {
      throw new ContractValidateException("This account name is already existed");
    }

    if (chainBaseManager.getAccountIndexStore().has(accountName)
        && chainBaseManager.getDynamicPropertiesStore().getAllowUpdateAccountName() == 0) {
      throw new ContractValidateException("This name is existed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/WitnessCreateActuator.java (L76-92)
```java
    byte[] ownerAddress = contract.getOwnerAddress().toByteArray();
    String readableOwnerAddress = StringUtil.createReadableString(ownerAddress);

    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid address");
    }

    if (!TransactionUtil.validUrl(contract.getUrl().toByteArray())) {
      throw new ContractValidateException("Invalid url");
    }

    AccountCapsule accountCapsule = accountStore.get(ownerAddress);

    if (accountCapsule == null) {
      throw new ContractValidateException("account[" + readableOwnerAddress
          + ActuatorConstant.NOT_EXIST_STR);
    }
```

**File:** framework/src/test/java/org/tron/core/actuator/CreateAccountActuatorTest.java (L57-63)
```java
  private Any getContract(String ownerAddress, String accountAddress) {
    return Any.pack(
        AccountCreateContract.newBuilder()
            .setAccountAddress(ByteString.copyFrom(ByteArray.fromHexString(accountAddress)))
            .setOwnerAddress(ByteString.copyFrom(ByteArray.fromHexString(ownerAddress)))
            .build());
  }
```
