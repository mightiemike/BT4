### Title
Case-insensitive AccountId front-running enables permanent squatting/DoS of a victim's intended human-readable identity - (File: `chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java`)

### Summary
`AccountIdIndexStore` normalizes all accountId keys to lowercase before storing or checking existence, while `SetAccountIdActuator.validate()` only checks the raw (non-normalized) `accountId` bytes for byte-equality against the account's own stored id. An unprivileged attacker who knows the exact string a victim intends to register (e.g. from public communication, ENS-like naming conventions, or observed off-chain intent) can submit a case-variant of that string first, permanently blocking the victim's exact-cased registration.

### Finding Description
`AccountIdIndexStore.getLowerCaseAccountId` lowercases the accountId bytes for both `put`, `get`, and `has`: <cite repo="Thankgoddavid56/java-tron--001" path="chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java" start="23="27" /> [1](#0-0) 

`SetAccountIdActuator.validate()` calls `accountIdIndexStore.has(accountId)` with the raw bytes provided by the caller, and this call internally lowercases the key for lookup: [2](#0-1) 

`execute()` stores the account's own `accountId` field with the exact case the caller submitted (not lowercased) on the `AccountCapsule`, but pushes the lowercase-normalized index entry into `AccountIdIndexStore`: [3](#0-2) 

Because `SetAccountIdContract` can only be submitted once per account (blocked afterwards by `"This account id already set"` in `validate()`): [4](#0-3) , an attacker who front-runs the victim with any case-variant of the target string permanently consumes the lowercase-normalized slot in the global index. The victim's later `SetAccountIdContract("myname")` will fail at `accountIdIndexStore.has(accountId)` with `"This id has existed"`, and since the victim's own account can never resubmit (one-shot), the victim's intended casing is unrecoverable.

The existing test suite even demonstrates the collision mechanism directly, only using the identical-case scenario (`nameAlreadyUsed`), but the underlying `has`/`put` logic in `AccountIdIndexStore` is genuinely case-insensitive so a different-case string triggers the same collision path.

### Impact Explanation
This is a permanent denial of a first-come identity resource for the victim: the human-readable `accountId` (used e.g. for KYC/off-chain identity mapping) can never be set to the victim's intended casing once an attacker has squatted a case-variant. This matches a "unauthorized account operations / griefing DoS" impact class — no funds are stolen, but the victim's ability to claim a specific identity string they legitimately intended to use is permanently and unilaterally blocked by an unprivileged third party who merely knows the target string.

### Likelihood Explanation
- Attacker only needs an ordinary funded/existing TRON account that has not yet set an `accountId` — no privileged role required.
- Attacker must know the exact byte string the victim intends to use and front-run the victim's transaction (mempool visibility of pending `SetAccountIdContract` transactions, or off-chain knowledge of the victim's planned identity string, makes this practical).
- Cost to the attacker is a single, cheap `SetAccountIdContract` transaction (fee = 0 per `calcFee()`): [5](#0-4) .
- Fully repeatable against any target string/victim, requiring only the front-running precondition described in the prompt.

### Recommendation
Make the collision-detection consistent with what is actually stored on the account, or make the whole feature case-sensitive end-to-end: either (a) store and compare `accountId` case-sensitively (drop the lowercasing in `AccountIdIndexStore`), or (b) if case-insensitive uniqueness is intentional, clearly document/enforce it and normalize the *displayed/stored* `AccountCapsule.accountId` value at `execute()` time to lowercase too, so what a user submits and what ends up canonically registered/checked are consistent, removing the surprise squatting vector.

### Proof of Concept
```java
@Test
public void caseInsensitiveAccountIdSquatting() throws Exception {
  // Attacker account, not yet holding an accountId
  AccountCapsule attackerCapsule = new AccountCapsule(
      ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS_1)),
      ByteString.EMPTY, AccountType.Normal);
  dbManager.getAccountStore().put(attackerCapsule.createDbKey(), attackerCapsule);

  TransactionResultCapsule ret = new TransactionResultCapsule();

  // Attacker front-runs with a different-case variant of the target string
  SetAccountIdActuator attackerActuator = new SetAccountIdActuator();
  attackerActuator.setChainBaseManager(dbManager.getChainBaseManager())
      .setAny(getContract("MyName01", OWNER_ADDRESS_1)); // must satisfy 8-32 byte length rule
  attackerActuator.validate();
  attackerActuator.execute(ret);

  // Victim later tries to set the *exact* casing they originally intended
  SetAccountIdActuator victimActuator = new SetAccountIdActuator();
  victimActuator.setChainBaseManager(dbManager.getChainBaseManager())
      .setAny(getContract("myname01", OWNER_ADDRESS));

  try {
    victimActuator.validate();
    Assert.fail("Expected ContractValidateException due to case-insensitive collision");
  } catch (ContractValidateException e) {
    Assert.assertEquals("This id has existed", e.getMessage());
  }
}
```
Expected: the victim's `validate()` throws `ContractValidateException("This id has existed")` even though the raw byte strings `"MyName01"` and `"myname01"` are distinct, and the victim's own account can never retry (`SetAccountIdContract` is one-shot per account).

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L52-57)
```java
  @Override
  public boolean has(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    return !ArrayUtils.isEmpty(value);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L48-50)
```java
    account.setAccountId(setAccountIdContract.getAccountId().toByteArray());
    accountStore.put(ownerAddress, account);
    accountIdIndexStore.put(account);
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L91-93)
```java
    if (account.getAccountId() != null && !account.getAccountId().isEmpty()) {
      throw new ContractValidateException("This account id already set");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L94-96)
```java
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
