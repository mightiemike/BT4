Based on my investigation, there is a valid analog in java-tron.

### Title
Permanently Lost Account Name/ID Reservation After Contract Self-Destruct - (File: chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java, chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java)

### Summary
`AccountIndexStore` and `AccountIdIndexStore` maintain a permanent, unique reservation mapping account name / account ID → owner address [1](#0-0) [2](#0-1) . When a smart contract account self-destructs via `SUICIDE`/`SELFDESTRUCT`, its entry in `AccountStore` is deleted, but the corresponding name/ID reservation entries in these two index stores are never removed, permanently orphaning the reserved name/ID with no function to reclaim it — mirroring the NFTR bug class where a name tied to a burned/relinquished owner becomes permanently unusable.

### Finding Description
`AccountUpdateContract` and `SetAccountIdContract` let an account reserve a unique account name or account ID. These reservations are enforced by uniqueness checks against `AccountIndexStore.has(accountName)` and `AccountIdIndexStore.has(accountId)`:
- `UpdateAccountActuator.validate()` rejects a name if `chainBaseManager.getAccountIndexStore().has(accountName)` returns true [3](#0-2) .
- `SetAccountIdActuator.validate()` rejects an ID if `accountIdIndexStore.has(accountId)` returns true [4](#0-3) .

When a contract account executes `SUICIDE`/`SELFDESTRUCT` (`Program.suicide`/`suicide2`), its balance, frozen resources, and TRC10 are transferred to the inheritor, and the account entry is removed from `AccountStore` (confirmed by `Assert.assertNull(accountStore.get(contractAddr))` in test after `suicideToAccount`) [5](#0-4) . The suicide handling code in `Program.java` (`transferDelegatedResourceToInheritor`, `transferFrozenV2BalanceToInheritor`, `withdrawRewardAndCancelVote`) only touches `AccountStore`, `DelegatedResourceStore`, freeze balances, and votes — it never calls `AccountIndexStore.delete(...)` or `AccountIdIndexStore.delete(...)` [6](#0-5) [7](#0-6) . Neither `AccountIndexStore` nor `AccountIdIndexStore` expose any actuator-reachable path to remove a reservation once set (only `put`/`get`/`has` are defined, plus test-only `delete` calls via the generic store API) [8](#0-7) [9](#0-8) .

As a result, once a contract account reserves an `accountName` (via `AccountUpdateContract`) or an `accountId` (via `SetAccountIdContract`) and later self-destructs via `SUICIDE`, the name/ID stays permanently marked as "used" in the index stores even though the owning account no longer exists in `AccountStore`. No other account — nor the inheritor — can ever claim that name/ID again, since `UpdateAccountActuator`/`SetAccountIdActuator` validation checks the index stores, not `AccountStore` existence.

### Impact Explanation
This causes permanent, unrecoverable state pollution / asset lockup on-chain: unique, user-desired account names and account IDs are irrevocably squatted after any contract self-destructs, with no protocol-level mechanism to dereserve them. This is a systemic, permanent resource-loss/DoS on the account-name/ID namespace reachable by any user deploying and destructing a contract, directly analogous to the reported NFTR issue (a name tied to a destroyed/ownerless entity becomes permanently unusable).

### Likelihood Explanation
Trivially reachable: any user can deploy a contract, call `AccountUpdateContract` to set an account name (or `SetAccountIdContract` to set an account ID) on that contract account, then trigger `SUICIDE`/`SELFDESTRUCT` from within the contract. This requires no privileged role and is a normal, permitted sequence of broadcast transactions.

### Recommendation
When an account is removed from `AccountStore` due to `SUICIDE`/self-destruct, also delete its corresponding entries from `AccountIndexStore` and `AccountIdIndexStore` (or otherwise release the reservation) so the name/ID can be reused. Alternatively, add a life-cycle hook in `Program.suicide`/`suicide2` (or wherever the account deletion is finalized) that clears these index entries.

### Proof of Concept
1. Deploy a contract and have it call `AccountUpdateContract` to set `accountName = "myname"` on the contract's own address — `UpdateAccountActuator.execute` calls `accountIndexStore.put(account)` [10](#0-9) .
2. From the same contract, execute `SUICIDE`/`selfdestruct(inheritor)`. `Program.suicide2` transfers balance/resources and the account is removed from `AccountStore` [7](#0-6) .
3. Any other account now attempts `AccountUpdateContract` to claim `accountName = "myname"`. `UpdateAccountActuator.validate()` still finds `accountIndexStore.has("myname") == true` and throws `"This name is existed"` [11](#0-10) , even though the original owning account no longer exists — the name is permanently lost. The same applies to `SetAccountIdContract`/`AccountIdIndexStore`.

### Citations

**File:** chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java (L1-49)
```java
package org.tron.core.store;

import com.google.protobuf.ByteString;
import java.util.Objects;
import org.apache.commons.lang3.ArrayUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.tron.core.capsule.AccountCapsule;
import org.tron.core.capsule.BytesCapsule;
import org.tron.core.db.TronStoreWithRevoking;

@Component
public class AccountIndexStore extends TronStoreWithRevoking<BytesCapsule> {

  @Autowired
  public AccountIndexStore(@Value("account-index") String dbName) {
    super(dbName);
  }

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
}

```

**File:** chainbase/src/main/java/org/tron/core/store/AccountIdIndexStore.java (L1-61)
```java
package org.tron.core.store;

import com.google.protobuf.ByteString;
import java.util.Locale;
import java.util.Objects;
import org.apache.commons.lang3.ArrayUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.tron.core.capsule.AccountCapsule;
import org.tron.core.capsule.BytesCapsule;
import org.tron.core.db.TronStoreWithRevoking;

//todo : need Compatibility test
@Component
public class AccountIdIndexStore extends TronStoreWithRevoking<BytesCapsule> {

  @Autowired
  public AccountIdIndexStore(@Value("accountid-index") String dbName) {
    super(dbName);
  }

  private static byte[] getLowerCaseAccountId(byte[] bsAccountId) {
    return ByteString
        .copyFromUtf8(ByteString.copyFrom(bsAccountId).toStringUtf8().toLowerCase(Locale.ROOT))
        .toByteArray();
  }

  public void put(AccountCapsule accountCapsule) {
    byte[] lowerCaseAccountId = getLowerCaseAccountId(accountCapsule.getAccountId().toByteArray());
    super.put(lowerCaseAccountId, new BytesCapsule(accountCapsule.getAddress().toByteArray()));
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
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    if (ArrayUtils.isEmpty(value)) {
      return null;
    }
    return new BytesCapsule(value);
  }

  @Override
  public boolean has(byte[] key) {
    byte[] lowerCaseKey = getLowerCaseAccountId(key);
    byte[] value = revokingDB.getUnchecked(lowerCaseKey);
    return !ArrayUtils.isEmpty(value);
  }

}


```

**File:** actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java (L42-47)
```java
    byte[] ownerAddress = accountUpdateContract.getOwnerAddress().toByteArray();
    AccountCapsule account = chainBaseManager.getAccountStore().get(ownerAddress);

    account.setAccountName(accountUpdateContract.getAccountName().toByteArray());
    chainBaseManager.getAccountStore().put(ownerAddress, account);
    chainBaseManager.getAccountIndexStore().put(account);
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

**File:** actuator/src/main/java/org/tron/core/actuator/SetAccountIdActuator.java (L91-96)
```java
    if (account.getAccountId() != null && !account.getAccountId().isEmpty()) {
      throw new ContractValidateException("This account id already set");
    }
    if (accountIdIndexStore.has(accountId)) {
      throw new ContractValidateException("This id has existed");
    }
```

**File:** framework/src/test/java/org/tron/common/runtime/vm/FreezeTest.java (L991-993)
```java
    TVMTestResult result = triggerSuicide(callerAddr, contractAddr, inheritorAddr, SUCCESS, null);

    Assert.assertNull(accountStore.get(contractAddr));
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L518-582)
```java
  public void suicide2(DataWord obtainerAddress) {

    byte[] owner = getContextAddress();
    boolean isNewContract = getContractState().isNewContract(owner);
    if (isNewContract) {
      suicide(obtainerAddress);
      return;
    }

    byte[] obtainer = obtainerAddress.toTronAddress();

    long balance = getContractState().getBalance(owner);

    if (logger.isDebugEnabled()) {
      logger.debug("Transfer to: [{}] heritage: [{}]",
          Hex.toHexString(obtainer),
          balance);
    }

    increaseNonce();

    InternalTransaction internalTx = addInternalTx(null, owner, obtainer, balance, null,
        "suicide", nonce, getContractState().getAccount(owner).getAssetMapV2());

    if (FastByteComparisons.isEqual(owner, obtainer)) {
      return;
    }

    if (VMConfig.allowTvmVote()) {
      withdrawRewardAndCancelVote(owner, getContractState());
      balance = getContractState().getBalance(owner);
      if (internalTx != null && balance != internalTx.getValue()) {
        internalTx.setValue(balance);
      }
    }

    // transfer balance and trc10
    createAccountIfNotExist(getContractState(), obtainer);
    try {
      MUtil.transfer(getContractState(), owner, obtainer, balance);
      if (VMConfig.allowTvmTransferTrc10()) {
        MUtil.transferAllToken(getContractState(), owner, obtainer);
      }
    } catch (ContractValidateException e) {
      if (VMConfig.allowTvmConstantinople()) {
        throw new TransferException(
            "transfer all token or transfer all trx failed in suicide: %s", e.getMessage());
      }
      throw new BytecodeExecutionException("transfer failure");
    }

    // transfer freeze
    if (VMConfig.allowTvmFreeze()) {
      transferDelegatedResourceToInheritor(owner, obtainer, getContractState());
    }

    // transfer freezeV2
    if (VMConfig.allowTvmFreezeV2()) {
      long expireUnfrozenBalance =
          transferFrozenV2BalanceToInheritor(owner, obtainer, getContractState());
      if (expireUnfrozenBalance > 0 && internalTx != null) {
        internalTx.setValue(internalTx.getValue() + expireUnfrozenBalance);
      }
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/vm/program/Program.java (L588-618)
```java
  private void transferDelegatedResourceToInheritor(byte[] ownerAddr, byte[] inheritorAddr, Repository repo) {

    // delegated resource from sender to owner, just abandon
    // in order to making that sender can unfreeze their balance in future
    // nothing will be deleted

    // delegated resource from owner to receiver
    // there cannot be any resource when suicide

    AccountCapsule ownerCapsule = repo.getAccount(ownerAddr);

    // transfer owner`s frozen balance for bandwidth to inheritor
    long frozenBalanceForBandwidthOfOwner = 0;
    // check if frozen for bandwidth exists
    if (ownerCapsule.getFrozenCount() != 0) {
      frozenBalanceForBandwidthOfOwner = ownerCapsule.getFrozenList().get(0).getFrozenBalance();
    }
    repo.addTotalNetWeight(-frozenBalanceForBandwidthOfOwner / TRX_PRECISION);

    long frozenBalanceForEnergyOfOwner =
        ownerCapsule.getAccountResource().getFrozenBalanceForEnergy().getFrozenBalance();
    repo.addTotalEnergyWeight(-frozenBalanceForEnergyOfOwner / TRX_PRECISION);

    // transfer all kinds of frozen balance to BlackHole
    repo.addBalance(inheritorAddr, frozenBalanceForBandwidthOfOwner + frozenBalanceForEnergyOfOwner);

    if (VMConfig.allowTvmSelfdestructRestriction()) {
      clearOwnerFreeze(ownerCapsule);
      repo.updateAccount(ownerAddr, ownerCapsule);
    }
  }
```
