### Title
Front-Running of `SetAccountIdContract` Allows Denial-of-Service Against Legitimate Account-ID Claims - (File: actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java)

### Summary
`SetAccountIdActuator` lets any account permanently bind a chosen `accountId` string to itself, but the uniqueness check is a simple "not yet taken" guard with no reservation, fee-scaling, or ownership pre-commitment. This mirrors the `enrollCourier()` front-running bug class: any observer of a pending `SetAccountIdContract` transaction in the mempool can submit a competing transaction claiming the same `accountId` first, permanently denying the original submitter their desired identifier.

### Finding Description
`SetAccountIdActuator.validate()` rejects the request only if the account already has an id set or if the target `accountId` is already indexed in `AccountIdIndexStore`: [1](#0-0) 

Because `accountId` values are free-form, user-chosen strings (validated only for length/character constraints via `TransactionUtil.validAccountId`), and because assignment is irreversible once set — "This account id already set" / "This id has existed" — the first transaction to be included in a block wins the id permanently, exactly like the `couriers[id].cut == 0` gate in the reported Solidity bug. [2](#0-1) 

Any anonymous party can observe a broadcast `SetAccountIdContract` transaction (via the P2P layer or the `/wallet/broadcasttransaction` and related HTTP/gRPC endpoints exposed by `SetAccountIdServlet`) before it is confirmed, and race it with their own transaction carrying the same `accountId` and a higher fee/bandwidth priority. [3](#0-2) 

Because id assignment is executed unconditionally once validation passes, whichever transaction lands first in the block wins, and the legitimate user's later transaction fails validation with `ContractValidateException("This id has existed")`, as the actuator unit tests already demonstrate for the analogous "already set" / "already used" races. [4](#0-3) 

### Impact Explanation
This is a reachable, unprivileged Denial-of-Service: any address broadcasting a `SetAccountIdContract` transaction can have its desired `accountId` squatted by a front-runner, permanently and irrevocably (the id can never be reassigned once taken, and an account can only ever set its id once), denying the legitimate user any future ability to bind that identifier to their account. This matches the "Denial of Service" impact class described in the source report (genuine users prevented from claiming their desired identifier due to attacker front-running).

### Likelihood Explanation
Likelihood is moderate: exploitation requires the attacker to observe a pending, not-yet-confirmed `SetAccountIdContract` transaction (via mempool/API visibility) and successfully get their own competing transaction included first. This is feasible for any actor monitoring broadcast transactions, similar to standard front-running conditions, though it requires targeted intent since there is no direct monetary gain for the attacker — only griefing/denial value.

### Recommendation
Avoid a race-to-claim design for `accountId`. Options include: requiring the requester to commit to the id in a prior transaction (commit-reveal), charging an escalating fee for `SetAccountIdContract` to discourage indiscriminate squatting, or allowing reassignment/dispute resolution rather than making the binding irrevocable on first claim.

### Proof of Concept
1. Alice broadcasts a `SetAccountIdContract` transaction attempting to bind `accountId = "alice123"` to her address.
2. Mallory observes this pending transaction (via node's transaction pool/API) before it is included in a block.
3. Mallory crafts and broadcasts her own `SetAccountIdContract` transaction with the same `accountId = "alice123"`, using higher fee/bandwidth to increase inclusion priority.
4. Mallory's transaction is included first; `AccountIdIndexStore` now maps `"alice123"` to Mallory's address.
5. Alice's original transaction is later processed and fails validation in `SetAccountIdActuator.validate()` with `ContractValidateException("This id has existed")`, permanently denying her the chosen id. [5](#0-4)

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

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L87-99)
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
  }
```

**File:** framework/src/main/java/org/tron/core/services/http/SetAccountIdServlet.java (L26-42)
```java
  protected void doPost(HttpServletRequest request, HttpServletResponse response) {
    try {
      String contract = request.getReader().lines()
          .collect(Collectors.joining(System.lineSeparator()));
      Util.checkBodySize(contract);
      boolean visible = Util.getVisiblePost(contract);
      SetAccountIdContract.Builder build = SetAccountIdContract.newBuilder();
      JsonFormat.merge(contract, build, visible);
      Protocol.Transaction tx = wallet.createTransactionCapsule(build.build(),
          Protocol.Transaction.Contract.ContractType.SetAccountIdContract).getInstance();
      JSONObject jsonObject = JSONObject.parseObject(contract);
      tx = Util.setTransactionPermissionId(jsonObject, tx);
      response.getWriter().println(Util.printCreateTransaction(tx, visible));
    } catch (Exception e) {
      Util.processError(e, response);
    }
  }
```

**File:** framework/src/test/java/org/tron/core/actuator/SetAccountIdActuatorTest.java (L173-216)
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
```
