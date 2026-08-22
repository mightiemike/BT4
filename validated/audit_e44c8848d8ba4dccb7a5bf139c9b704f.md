### Title
Front-Running of `SetAccountIdContract` via Global `AccountIdIndexStore` Uniqueness Check Enables Griefing/DoS of Legitimate Account-ID Registration - ([File: actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java])

### Summary
`SetAccountIdActuator` lets any account permanently bind a human-readable `accountId` to its address, enforced via a global uniqueness index (`AccountIdIndexStore`). Because the uniqueness check is a simple "not yet claimed" check performed independently in `validate()`/`execute()` with no binding to the submitter beyond ordinary transaction execution ordering, an attacker who observes a pending `SetAccountIdContract` transaction in the mempool can submit their own transaction claiming the exact same `accountId` (case-insensitively) with higher fee/priority, causing it to be packed first. When the victim's original transaction is then processed, it fails validation, and the victim's one-time opportunity to register that desired `accountId` is denied — directly analogous to the `WebAuthValidator` front-running issue where a unique-slot check ("has already registered domain+credentialId / accountId") can be raced by an observer of the mempool.

### Finding Description
`SetAccountIdActuator.validate()` performs two checks that are both racy against mempool observation: [1](#0-0) 

- `account.getAccountId()` must be empty (an account can set its ID only **once**, permanently — see `TransactionUtil.validAccountId` bounds and test `twiceUpdateAccount` confirming an id, once set, can never be changed): [2](#0-1) 
- `accountIdIndexStore.has(accountId)` enforces the `accountId` is globally unique across *all* accounts on the chain (case-insensitively, per `getLowerCaseAccountId`): [3](#0-2) 

The `accountId` value is entirely attacker-chosen, human readable data (`TransactionUtil.validAccountId` just bounds length 8-32 and character range) — there is no cryptographic commitment or association tying a specific pending `accountId` claim to the specific submitting account before it lands on-chain: [4](#0-3) 

Because Tron's mempool/pending transactions are visible to network participants before block inclusion (the same "monitor the mempool, then front-run with the same unique key" precondition as the reported `WebAuthValidator` bug class), an attacker can:
1. Observe a pending `SetAccountIdContract` transaction for a desired `accountId` from victim `V`.
2. Broadcast their own `SetAccountIdContract` with the identical `accountId` (any case variant, since matching is lower-cased) from attacker address `A`, with a higher energy/bandwidth priority so it lands first.
3. `A`'s transaction succeeds in `AccountIdIndexStore.put`, claiming the slot: [5](#0-4) 
4. `V`'s original transaction then fails validation with `"This id has existed"`, confirmed by the existing test `nameAlreadyUsed`: [6](#0-5) 

Because an account may only ever set its `accountId` once, and the victim's chosen human-readable identifier is now permanently squatted by the attacker's account, the victim cannot ever bind that specific `accountId` string to their address on this chain — this is the same "occupy the intended slot before the legitimate registrant" pattern described in the report, translated to java-tron's account-identity feature.

### Impact Explanation
This is a griefing/Denial-of-Service on a specific chain state-transition (permanent account-identity binding): the victim's transaction is wasted (bandwidth/fee consumed for validation attempt / failed broadcast) and the victim is permanently prevented from ever associating their intended `accountId` with their own account, since both the per-account "set once" restriction and the global uniqueness index are enforced with no way to reclaim or dispute a squatted identifier. Any third party monitoring the mempool for `SetAccountIdContract` transactions (which are anonymous, broadcastable by anyone) can carry out this attack against any target with no privileged access required.

### Likelihood Explanation
Likelihood is limited by the same operational factor the Matter Labs team cited for the original report: it requires visibility into pending/not-yet-included transactions. On a fully public mempool this is trivially and cheaply exploitable by any actor able to submit their own transactions with a higher fee/priority, since `accountId` values are plaintext in the transaction and the collision-check logic is entirely deterministic and race-friendly.

### Recommendation
Introduce an owner-binding mechanism for `accountId` registration, e.g. a commit-reveal scheme that hashes `accountId` together with the committing `ownerAddress` before broadcast, then reveals in the actual `SetAccountIdContract`, so an attacker replaying an observed `accountId` cannot claim it for a different address. Alternatively, allow the uniqueness check to be scoped or resolvable (e.g., permit release/reassignment of a contested slot, or require the id-claim to be tied cryptographically to the specific owner address it was originally intended for) to eliminate the front-runnable window.

### Proof of Concept
1. Victim `V` broadcasts `SetAccountIdContract{ownerAddress = V, accountId = "myhandle"}`.
2. Attacker observes this in the mempool and immediately broadcasts `SetAccountIdContract{ownerAddress = A, accountId = "myhandle"}` with a higher fee to prioritize inclusion.
3. Block producer includes attacker's transaction first: `AccountIdIndexStore.put` binds `"myhandle" -> A`, per [7](#0-6) .
4. Victim's transaction is then validated and rejected with `ContractValidateException("This id has existed")`, per [8](#0-7) , matching the behavior already exercised in `SetAccountIdActuatorTest.nameAlreadyUsed`.
5. Because `account.getAccountId()` can only ever be set once per account (`"This account id already set"` check), `V` can never obtain `"myhandle"` for their own address.

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

**File:** framework/src/test/java/org/tron/core/actuator/SetAccountIdActuatorTest.java (L136-171)
```java
  public void twiceUpdateAccount() {
    TransactionResultCapsule ret = new TransactionResultCapsule();
    SetAccountIdActuator actuator = new SetAccountIdActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(ACCOUNT_NAME, OWNER_ADDRESS));
    SetAccountIdActuator actuator1 = new SetAccountIdActuator();
    actuator1.setChainBaseManager(dbManager.getChainBaseManager())
        .setAny(getContract(ACCOUNT_NAME_1, OWNER_ADDRESS));
    try {
      actuator.validate();
      actuator.execute(ret);
      Assert.assertEquals(ret.getInstance().getRet(), code.SUCESS);
      AccountCapsule accountCapsule = dbManager.getAccountStore()
          .get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(ACCOUNT_NAME, accountCapsule.getAccountId().toStringUtf8());
      Assert.assertTrue(true);
    } catch (ContractValidateException e) {
      Assert.assertFalse(e instanceof ContractValidateException);
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }

    try {
      actuator1.validate();
      actuator1.execute(ret);
      Assert.assertFalse(true);
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("This account id already set", e.getMessage());
      AccountCapsule accountCapsule = dbManager.getAccountStore()
          .get(ByteArray.fromHexString(OWNER_ADDRESS));
      Assert.assertEquals(ACCOUNT_NAME, accountCapsule.getAccountId().toStringUtf8());
    } catch (ContractExeException e) {
      Assert.assertFalse(e instanceof ContractExeException);
    }
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/SetAccountIdActuatorTest.java (L204-216)
```java
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

**File:** actuator/src/main/java/org/tron/core/utils/TransactionUtil.java (L85-87)
```java
  public static boolean validAccountId(byte[] accountId) {
    return validReadableBytes(accountId, MAX_ACCOUNT_ID_LEN) && accountId.length >= MIN_ACCOUNT_ID_LEN;
  }
```
