### Title
Account-id squatting via mempool front-running of `SetAccountIdContract` permanently blocks victim's legitimate account-id claim - ([File: SetAccountIdActuator.java])

### Summary
`SetAccountIdActuator.validate()` enforces global, case-insensitive uniqueness of `accountId` via `AccountIdIndexStore.has()`, but the reservation is granted to whichever transaction gets included in a block first, not to whoever originated it. An unprivileged attacker who observes a victim's pending `SetAccountIdContract` transaction (e.g., via `GetTransactionFromPendingServlet`/`/wallet/gettransactionfrompending`) can broadcast their own `SetAccountIdContract` with the same `accountId` and, if it is included first, permanently squat the id, causing the victim's transaction to fail validation with `"This id has existed"`.

### Finding Description
The account-id namespace is implemented as a single shared key-value index (`AccountIdIndexStore`), keyed by the lower-cased account id, with no association to any specific requester prior to on-chain inclusion: [1](#0-0) 

`SetAccountIdActuator.validate()` checks only `accountIdIndexStore.has(accountId)` (case-insensitively) and the caller's own account state, not any prior claim/reservation tied to a specific address or a specific earlier-broadcast transaction: [2](#0-1) 

Since `accountId`, once set on an `AccountCapsule`, can never be cleared or reset (there is no unset/rename actuator for account id, unlike `AccountUpdateContract`'s account *name*, which has an explicit `AllowUpdateAccountName` override), a successful squat is permanent for the victim's intended id: [3](#0-2) 

The attacker's reconnaissance path is straightforward: `GetTransactionFromPendingServlet` (`/wallet/gettransactionfrompending`) and the equivalent gRPC `GetTransactionFromPending` RPC expose full pending-transaction contents, including the plaintext `accountId` and `ownerAddress` of any unconfirmed `SetAccountIdContract`: [4](#0-3) [5](#0-4) 

Nothing in the actuator's `validate()`/`execute()` path, nor `AccountIdIndexStore`, ties the eventual winner of the id to who broadcast it earliest chronologically off-chain — only inclusion order in the block matters. This is confirmed directly by the existing unit test `nameAlreadyUsed`, which demonstrates that whichever actuator executes first claims the id and the second one fails with `"This id has existed"`, regardless of intent or origin: [6](#0-5) 

There is no per-account "already set" check that would stop a second, different account from claiming the same id — the "already set" guard (line 91-93) only detects if the *same* account previously succeeded, not that the id was intended for someone else.

### Impact Explanation
This is a griefing/DoS on a one-time-settable, human-readable identity field: the victim's `SetAccountIdContract` will permanently fail with `ContractValidateException("This id has existed")`, and since account ids cannot be reset, the victim can never use that account id from any account. This maps to a **DoS via the TRON protocol implementation** impact class (griefing of a state-exclusive, irreversible on-chain operation), not asset loss or key leak. It does not cause chain-wide consensus divergence or fund loss.

### Likelihood Explanation
- Preconditions are minimal and match "unprivileged attacker": any funded account can send a `SetAccountIdContract` (fee is 0, per `calcFee()` returning 0), so cost is essentially just bandwidth/energy for a normal transaction.
- Attacker needs visibility into the victim's intended `accountId` before it's confirmed — achievable either through the public `/wallet/gettransactionfrompending` endpoint/gRPC equivalent (no auth required) while the victim's tx sits in the pool, or through off-chain announcement (e.g., victim advertises the id they plan to claim).
- Winning the race requires only that the attacker's transaction be included in an earlier block (or earlier in the same block) than the victim's — there is no special privilege needed, just normal broadcast/propagation, making it fully reproducible by any actor with mempool visibility.
- This is a systemic race-condition risk inherent to any "first claim wins" global-namespace design without a per-address commit/reveal or reservation scheme, and is highlighted by the note in the source: `//todo : need Compatibility test`.

### Recommendation
- Introduce a commit-reveal or reservation scheme for `SetAccountIdContract` (e.g., a pre-registration hash commitment followed by a reveal after a delay) to prevent front-running of a not-yet-visible target id, similar to well-known ENS/domain-registration mitigations.
- Alternatively/additionally, tie a short-lived reservation of an `accountId` to the specific `ownerAddress` at the moment of transaction signing/broadcast (e.g., via a bond or including the intended id inside a hash that can't be copied without invalidating a different address's proof), so that observing a pending transaction does not let a third party copy and win the same claim.
- At minimum, document this as an inherent race in the account-id feature and, if account-id squatting is a supported concern, add safeguards at the wallet/CLI level to submit `SetAccountIdContract` privately or bundle it atomically with the qualifying condition that established the "victim intent."

### Proof of Concept
Extend `SetAccountIdActuatorTest.nameAlreadyUsed` to model victim-vs-attacker ordering explicitly:
```java
@Test
public void frontRunAccountIdSquat() {
  // Victim intends to claim ACCOUNT_NAME for OWNER_ADDRESS,
  // but the attacker (OWNER_ADDRESS_1) observes it in the mempool
  // (e.g., via GET /wallet/gettransactionfrompending) and race-broadcasts
  // an identical SetAccountIdContract that gets included first.

  AccountCapsule attackerCapsule = new AccountCapsule(
      ByteString.copyFrom(ByteArray.fromHexString(OWNER_ADDRESS_1)),
      ByteString.EMPTY, AccountType.Normal);
  dbManager.getAccountStore().put(attackerCapsule.getAddress().toByteArray(), attackerCapsule);

  TransactionResultCapsule ret = new TransactionResultCapsule();

  // Attacker's front-run transaction, included FIRST despite victim broadcasting earlier off-chain.
  SetAccountIdActuator attackerActuator = new SetAccountIdActuator();
  attackerActuator.setChainBaseManager(dbManager.getChainBaseManager())
      .setAny(getContract(ACCOUNT_NAME, OWNER_ADDRESS_1));
  attackerActuator.validate();      // succeeds
  attackerActuator.execute(ret);    // squats ACCOUNT_NAME

  // Victim's legitimate, chronologically-earlier-submitted transaction now fails permanently.
  SetAccountIdActuator victimActuator = new SetAccountIdActuator();
  victimActuator.setChainBaseManager(dbManager.getChainBaseManager())
      .setAny(getContract(ACCOUNT_NAME, OWNER_ADDRESS));
  try {
    victimActuator.validate();
    Assert.fail("expected ContractValidateException");
  } catch (ContractValidateException e) {
    Assert.assertEquals("This id has existed", e.getMessage());
  }
}
```
This directly follows the pattern already validated by the existing `nameAlreadyUsed` test at [6](#0-5) , confirming that inclusion order — not chronological broadcast order or requester identity — determines the permanent winner of the account id.

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

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L87-98)
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

    return true;
```

**File:** framework/src/main/java/org/tron/core/services/http/GetTransactionFromPendingServlet.java (L21-34)
```java
  protected void doGet(HttpServletRequest request, HttpServletResponse response) {
    try {
      boolean visible = Util.getVisible(request);
      String input = request.getParameter("value");
      TransactionCapsule reply = manager.getTxFromPending(input);
      if (reply != null) {
        response.getWriter().println(Util.printTransaction(reply.getInstance(), visible));
      } else {
        response.getWriter().println("{}");
      }
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L2522-2541)
```java
  public TransactionCapsule getTxFromPending(String txId) {
    AtomicReference<TransactionCapsule> transactionCapsule = new AtomicReference<>();
    Sha256Hash txHash = Sha256Hash.wrap(ByteArray.fromHexString(txId));
    pendingTransactions.forEach(tx -> {
      if (tx.getTransactionId().equals(txHash)) {
        transactionCapsule.set(tx);
        return;
      }
    });
    if (transactionCapsule.get() != null) {
      return transactionCapsule.get();
    }
    rePushTransactions.forEach(tx -> {
      if (tx.getTransactionId().equals(txHash)) {
        transactionCapsule.set(tx);
        return;
      }
    });
    return transactionCapsule.get();
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
