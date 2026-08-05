### Title
Front-running griefing of `SetAccountIdContract` allows attacker to squat a victim's chosen unique AccountId - (File: `actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java`)

### Summary
`SetAccountIdActuator` lets any account bind itself to a globally-unique, case-insensitive `accountId` that can never be changed once set. Because the uniqueness check is performed only at execution time against public mempool data, and the transaction is cheap (declared fee is zero), an attacker who observes a victim's pending `SetAccountIdContract` transaction can front-run it with the same `accountId`, permanently denying that identifier to the victim — an exact analog of the reported `SpokeCommon.createAccount()` griefing bug.

### Finding Description
`SetAccountIdActuator.validate()` enforces that an `accountId` must not already be set on the account and must not already exist in the `AccountIdIndexStore`: [1](#0-0) 

The `AccountIdIndexStore` is a simple mapping from (lower-cased) `accountId` bytes to an address, with no owner-binding or reservation mechanism — first writer wins: [2](#0-1) 

The proto explicitly documents this identifier as unique and immutable once set: "Set account id if the account has no id. Account id is unique and case insensitive." [3](#0-2) 

The actuator charges zero fee (`calcFee()` returns 0), making repeated griefing attempts essentially free besides minimal bandwidth cost: [4](#0-3) 

The exact same "first writer wins" race condition described in the external report (`SpokeCommon.createAccount()`, where accountId claims are settled by transaction ordering visible in a public mempool) applies here: any pending `SetAccountIdContract` transaction broadcast to the network is visible to observers before block inclusion, so an attacker can copy the `accountId` field, submit their own transaction with a higher fee/priority, and get it included first. Once set, the victim's account can never claim that `accountId` for its own account because the `account.getAccountId() != null && !isEmpty()` check would still pass for the victim (empty), but the `accountIdIndexStore.has(accountId)` check will now fail, permanently blocking the victim from that identifier since ids cannot be reassigned: [5](#0-4) 

The existing test suite confirms the "This id has existed" failure path this griefing exploits: [6](#0-5) 

### Impact Explanation
This is a griefing vector with no profit motive required: an attacker monitoring the public mempool/tx pool for `SetAccountIdContract` transactions can permanently deny a specific human-readable, case-insensitive `accountId` to its intended owner by front-running with the same id at negligible cost. Since `accountId` cannot be changed once set (validate() blocks re-setting: "This account id already set"), this is a permanent, irreversible denial for the specific identifier the victim wanted, matching the "Griefing" impact category from the referenced report.

### Likelihood Explanation
Likelihood is moderate: `SetAccountIdContract` transactions are broadcast publicly before confirmation like any TRON transaction, and any node/attacker running mempool-monitoring infrastructure can detect and copy the `accountId` field trivially. The zero declared fee and low bandwidth cost make repeated attempts cheap, and no special privilege is required — any account can call `SetAccountId`.

### Recommendation
Consider one or more of: (1) allowing accountId reservation/commit-reveal schemes (hash-commit then reveal) to prevent copying from the mempool, (2) allowing accountId to be tied at transaction-submission time to a specific owner via a bond/deposit that's refunded on success to raise attacker cost, or (3) documenting this as accepted behavior consistent with other "first come first serve" identifier claims in the protocol (e.g., asset names) if it is considered out of scope, since this mirrors pre-existing design tradeoffs elsewhere in the codebase (e.g., `AssetIssueContract` name uniqueness) and TRON currently treats accountId squatting as a known/accepted characteristic rather than a critical vulnerability.

### Proof of Concept
1. Victim broadcasts `SetAccountIdContract{accountId: "victimname", ownerAddress: victim}`.
2. Attacker observes this pending transaction in the public tx pool, and broadcasts `SetAccountIdContract{accountId: "victimname", ownerAddress: attacker}` with equal or higher priority/fee.
3. Attacker's transaction is included first; `accountIdIndexStore.put()` binds `"victimname"` to attacker's address.
4. Victim's transaction executes and fails validation at `accountIdIndexStore.has(accountId)` → `ContractValidateException("This id has existed")`, as demonstrated by the existing unit test `nameAlreadyUsed`.
5. Victim can never obtain `"victimname"` as their accountId again, since it is permanently owned by the attacker's account. [7](#0-6)

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L45-53)
```java
    byte[] ownerAddress = setAccountIdContract.getOwnerAddress().toByteArray();
    AccountCapsule account = accountStore.get(ownerAddress);

    account.setAccountId(setAccountIdContract.getAccountId().toByteArray());
    accountStore.put(ownerAddress, account);
    accountIdIndexStore.put(account);
    ret.setStatus(fee, code.SUCESS);

    return true;
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L87-96)
```java
    AccountCapsule account = accountStore.get(ownerAddress);
    if (account == null) {
      throw new ContractValidateException("Account has not existed");
    }
    if (account.getAccountId() != null && !account.getAccountId().isEmpty()) {
      throw new ContractValidateException("This account id already set");
    }
    if (accountIdIndexStore.has(accountId)) {
      throw new ContractValidateException("This id has existed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L106-109)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L23-32)
```java
  private static byte[] getLowerCaseAccountId(byte[] bsAccountId) {
    return ByteString
        .copyFromUtf8(ByteString.copyFrom(bsAccountId).toStringUtf8().toLowerCase(Locale.ROOT))
        .toByteArray();
  }

  public void put(AccountCapsule accountCapsule) {
    byte[] lowerCaseAccountId = getLowerCaseAccountId(accountCapsule.getAccountId().toByteArray());
    super.put(lowerCaseAccountId, new BytesCapsule(accountCapsule.getAddress().toByteArray()));
  }
```

**File:** protocol/src/main/protos/core/contract/account_contract.proto (L38-42)
```text
// Set account id if the account has no id. Account id is unique and case insensitive.
message SetAccountIdContract {
  bytes account_id = 1;
  bytes owner_address = 2;
}
```

**File:** framework/src/test/java/org/tron/core/actuator/SetAccountIdActuatorTest.java (L173-217)
```java
  @Test
  public void nameAlreadyUsed() {
    TransactionResultCapsule ret = new TransactionResultCapsule();
    SetAccountIdActuator actuator = new SetAccountIdActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(ACCOUNT_NAME, OWNER_ADDRESS));
    SetAccountIdActuator actuator1 = new SetAccountIdActuator();
    actuator1.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(ACCOUNT_NAME, OWNER_ADDRESS_1));
    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.assertEquals(ret.getInstance().getRet(), code.SUCESS);
      AccountCapsule accountCapsule = dbManager.getAccountStore()
          .get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(ACCOUNT_NAME, accountCapsule.getAccountId().toStringUtf8());
      Assert.assertTrue(true);
    } catch (ContractValidateException e) {
      logger.info(e.getMessage());
      Assert.assertFalse(e instanceof ContractValidateException);
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }

    AccountCapsule ownerCapsule =
        new AccountCapsule(
            ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS_1)),
            ByteString.EMPTY,
            AccountType.Normal);
    dbManager.getAccountStore().put(ownerCapsule.getAddress().toByteArray(), ownerCapsule);

    try {
      actuator1.validate();
      actuator1.execute(ret);
      Assert.assertFalse(true);
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("This id has existed", e.getMessage());
      AccountCapsule accountCapsule = dbManager.getAccountStore()
          .get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(ACCOUNT_NAME, accountCapsule.getAccountId().toStringUtf8());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```
