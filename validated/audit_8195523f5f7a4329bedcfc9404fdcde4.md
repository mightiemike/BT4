### Title
Case-insensitive AccountId collision allows front-running/squatting of account identifiers - (File: chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java)

### Summary
`AccountIdIndexStore.getLowerCaseAccountId()` normalizes the accountId to lower case for the uniqueness index key, while `SetAccountIdActuator.execute()` stores the attacker-supplied original-case bytes into `AccountCapsule.setAccountId()`. An attacker can register a mixed-case variant of a name (e.g. `'Victim'`) before the legitimate owner registers any case variant of that same name (e.g. `'victim'`), permanently blocking the legitimate owner from ever claiming it.

### Finding Description
`AccountIdIndexStore.put()` calls `getLowerCaseAccountId()` and writes the lower-cased id as the index key, but stores the raw address as value; the account's displayed `accountId` field (set via `AccountCapsule.setAccountId()` in `SetAccountIdActuator.execute()` at actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java lines 45-50) retains the original case supplied by the caller. [1](#0-0) 

`SetAccountIdActuator.validate()` checks uniqueness via `accountIdIndexStore.has(accountId)`, which internally also lower-cases the key before lookup: [2](#0-1) [3](#0-2) 

Exploit flow:
1. Attacker (any funded, unprivileged account) broadcasts `SetAccountIdContract` with `accountId = 'Victim'`. `has('Victim')` lower-cases to `'victim'`, finds no existing entry, so `validate()` passes and `execute()` stores `'Victim'` (mixed case) tied to the attacker's address, while the index is written under key `'victim'`.
2. The true intended owner later broadcasts `SetAccountIdContract` with `accountId = 'victim'`. `validate()` calls `has('victim')` → lower-cases to `'victim'` → finds the attacker's entry → throws `"This id has existed"`, permanently blocking any case variation of that identifier for the legitimate party.

This is confirmed by the existing test pattern `SetAccountIdActuatorTest.nameAlreadyUsed` (same-case collision) combined with the lower-casing behavior in `AccountIdIndexStore`, and no test exists validating cross-case uniqueness enforcement at registration time versus what is actually stored/displayed on-chain. [4](#0-3) 

None of the existing checks (`TransactionUtil.validAccountId`, `DecodeUtil.addressValid`, account-existence check, "already set" check) address case-sensitivity; they validate byte length/charset and owner-address format only, not case-collision semantics. [5](#0-4) 

### Impact Explanation
Impact is limited to identifier/naming squatting: an attacker can permanently deny a specific case-variant "brand" name to its intended legitimate holder by front-running with a different casing, since case is preserved for display/lookup by `AccountCapsule.getAccountId()` but folded for the uniqueness constraint. This does not lead to theft of funds, key disclosure, node crash, or consensus divergence — `accountId` is a self-set nickname field with no associated authorization or fund-custody semantics found elsewhere in the codebase (e.g., no signature/authority checks are keyed off it). This corresponds to a low-severity "unauthorized account operation" / identifier-squatting class, not an asset-loss or availability bounty class.

### Likelihood Explanation
Trivial to execute: any funded account can broadcast a `SetAccountIdContract` transaction (near-zero fee, `calcFee()` returns 0) with an arbitrary-case string satisfying `TransactionUtil.validAccountId` (8-32 bytes, no spaces/non-ASCII). The only precondition is broadcasting before the legitimate party registers any case variant — a standard front-running race, requiring no privileged role, no signature forgery, and no non-default configuration.

### Recommendation
Normalize (e.g., lower-case) the `accountId` before storing it in `AccountCapsule.setAccountId()` in `SetAccountIdActuator.execute()`, so the stored/displayed value is consistent with the uniqueness key used by `AccountIdIndexStore`; alternatively, preserve original casing but store/index using a canonicalized form consistently in both the account record and the index, and document that accountId uniqueness is case-insensitive so front-running of any case-variant is an expected, canonical outcome rather than a bypass.

### Proof of Concept
```java
// Mirrors SetAccountIdActuatorTest.nameAlreadyUsed but with mixed-case front-run
SetAccountIdActuator attackerActuator = new SetAccountIdActuator();
attackerActuator.setChainBaseManager(dbManager.getChainBaseManager())
    .setAny(getContract("Victim", ATTACKER_ADDRESS)); // mixed case
attackerActuator.validate(); // passes: has("Victim") -> has("victim") -> not found
attackerActuator.execute(ret); // stores accountId="Victim" for ATTACKER_ADDRESS

SetAccountIdActuator victimActuator = new SetAccountIdActuator();
victimActuator.setChainBaseManager(dbManager.getChainBaseManager())
    .setAny(getContract("victim", VICTIM_ADDRESS)); // legitimate lower-case attempt
try {
  victimActuator.validate();
  Assert.fail("expected ContractValidateException");
} catch (ContractValidateException e) {
  Assert.assertEquals("This id has existed", e.getMessage()); // victim permanently blocked
}
```

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L52-57)
```java
  @Override
  public boolean has(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    return !ArrayUtils.isEmpty(value);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L78-96)
```java
    byte[] ownerAddress = setAccountIdContract.getOwnerAddress().toByteArray();
    byte[] accountId = setAccountIdContract.getAccountId().toByteArray();
    if (!TransactionUtil.validAccountId(accountId)) {
      throw new ContractValidateException("Invalid accountId");
    }
    if (!DecodeUtil.addressValid(ownerAddress)) {
      throw new ContractValidateException("Invalid ownerAddress");
    }

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
