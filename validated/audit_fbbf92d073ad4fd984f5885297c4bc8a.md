### Title
Unbounded stale-entry accumulation in AccountIndexStore via repeated cheap account renames when AllowUpdateAccountName=1 - (File: actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java)

### Summary
`UpdateAccountActuator.execute()` calls `AccountIndexStore.put(account)` on every successful rename but never removes the index entry for the account's previous name, and `AccountIndexStore` exposes no delete/remove method at all. When `AllowUpdateAccountName==1`, `validate()` skips the "name already exists" check entirely, so an attacker can toggle a single funded account between two (or more) names in a loop, permanently growing `AccountIndexStore` by one orphaned `name -> address` row per rename at fixed, size-based bandwidth cost.

### Finding Description
In `execute()`: [1](#0-0) 
only the new name is written via `chainBaseManager.getAccountIndexStore().put(account)`; the previous name key is never deleted. `AccountIndexStore` itself has no delete API — only `put`, `get`, and `has`: [2](#0-1) 

`validate()` only rejects a rename when the target name already exists in `AccountIndexStore` **if** `AllowUpdateAccountName==0`: [3](#0-2) 
When the dynamic property is set to `1` (as stated in the precondition), both the "name already existed on this account" and the "name existed in index" checks are bypassed, so the same account can freely alternate between name A and name B (or any set of names) indefinitely. A grep across the codebase confirms `AccountIndexStore` is referenced only from `UpdateAccountActuator` (for `put`/`has`) and its own definition — there is no other caller that ever removes a stale `name` key when an account is renamed.

Cost for this transaction (`calcFee()` returns `0`) is only bandwidth, which in TRON's model is priced by serialized transaction size, not by the amount of persistent state it creates. Since the transaction bytes are essentially the same size on every iteration (same address, same-length names), each loop iteration costs the same fixed bandwidth regardless of the fact that it leaves behind a permanent extra row in `AccountIndexStore`.

### Impact Explanation
Every rename permanently retains the old name's index entry (`name -> address`) in `AccountIndexStore`, which is backed by revoking DB / RocksDB and persists across blocks. Repeating `setAny(A) -> validate -> execute -> setAny(B) -> validate -> execute` in a loop lets a single funded account inflate the account-name-index key space arbitrarily, at flat per-transaction bandwidth cost that does not scale with the storage growth it causes. Over time and at scale (many attacker-controlled short names, or many accounts running this loop), this degrades disk usage and I/O on all full/witness nodes that must store and serve this index.

### Likelihood Explanation
Preconditions are minimal and attacker-controlled: one funded account (only enough TRX/bandwidth to submit `AccountUpdateContract` transactions) and the chain-wide dynamic parameter `AllowUpdateAccountName==1`. Given that parameter is set, the exploit requires no special privilege, no contract interaction, and no race condition — it is a straightforward repeatable API call sequence (`validate()`/`execute()` on `AccountUpdateContract`), fully reachable through the normal transaction-broadcast path (gRPC/HTTP `UpdateAccountServlet` / `Wallet`). It is trivially automatable and repeatable at whatever rate bandwidth/energy allows.

### Recommendation
On rename, remove the previous name's entry from `AccountIndexStore` before/while writing the new one. Concretely, add a `delete(byte[] key)` (or `delete(ByteString name)`) method to `AccountIndexStore`, and in `UpdateAccountActuator.execute()`, before renaming, if `account.getAccountName()` is non-empty, delete `chainBaseManager.getAccountIndexStore()`'s entry for the old name prior to `put`-ing the new one. Alternatively/additionally, price `AccountUpdateContract` execution to account for state growth (e.g., charge a "create" fee for new index rows, similar to account-creation fees), so repeated renames are not effectively free relative to the persistent storage they create.

### Proof of Concept
```java
// framework/src/test/java/org/tron/core/actuator/UpdateAccountActuatorStaleIndexTest.java
@Test
public void repeatedRenameLeavesStaleIndexEntries() throws Exception {
  dbManager.getDynamicPropertiesStore().saveAllowUpdateAccountName(1);

  byte[] owner = ...; // funded test account address
  AccountCapsule account = dbManager.getAccountStore().get(owner);

  byte[] nameA = "nameA".getBytes();
  byte[] nameB = "nameB".getBytes();

  for (int i = 0; i < 5; i++) {
    // rename to A
    setAccountUpdateContract(owner, nameA);
    actuator.validate();
    actuator.execute(ret);
    Assert.assertTrue(dbManager.getAccountIndexStore().has(nameA)); // new entry added

    // rename to B
    setAccountUpdateContract(owner, nameB);
    actuator.validate();
    actuator.execute(ret);
    Assert.assertTrue(dbManager.getAccountIndexStore().has(nameB)); // new entry added

    // BUG: stale entry for nameA is still present even though the account's
    // current name is now nameB.
    Assert.assertTrue(dbManager.getAccountIndexStore().has(nameA));
  }
  // AccountIndexStore now permanently holds orphaned rows for nameA and nameB
  // despite the account only ever having one "current" name, demonstrating
  // unbounded index growth at fixed per-tx bandwidth cost.
}
```

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java (L42-49)
```java
    byte[] ownerAddress = accountUpdateContract.getOwnerAddress().toByteArray();
    AccountCapsule account = chainBaseManager.getAccountStore().get(ownerAddress);

    account.setAccountName(accountUpdateContract.getAccountName().toByteArray());
    chainBaseManager.getAccountStore().put(ownerAddress, account);
    chainBaseManager.getAccountIndexStore().put(account);

    ret.setStatus(fee, code.SUCESS);
```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java (L89-97)
```java
    if (account.getAccountName() != null && !account.getAccountName().isEmpty()
        && chainBaseManager.getDynamicPropertiesStore().getAllowUpdateAccountName() == 0) {
      throw new ContractValidateException("This account name is already existed");
    }

    if (chainBaseManager.getAccountIndexStore().has(accountName)
        && chainBaseManager.getDynamicPropertiesStore().getAllowUpdateAccountName() == 0) {
      throw new ContractValidateException("This name is existed");
    }
```

**File:** chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java (L21-47)
```java
  public void put(AccountCapsule accountCapsule) {
    put(accountCapsule.getAccountName().toByteArray(),
        new BytesCapsule(accountCapsule.getAddress().toByteArray()));
  }

  public byte[] get(ByteString name) {
    BytesCapsule bytesCapsule = get(name.toByteArray());
    if (Objects.nonNull(bytesCapsule)) {
      return bytesCapsule.getData();
    }
    return null;
  }

  @Override
  public BytesCapsule get(byte[] key) {
    byte[] value = revokingDB.getUnchecked(key);
    if (ArrayUtils.isEmpty(value)) {
      return null;
    }
    return new BytesCapsule(value);
  }

  @Override
  public boolean has(byte[] key) {
    byte[] value = revokingDB.getUnchecked(key);
    return !ArrayUtils.isEmpty(value);
  }
```
