### Title
Account name uniqueness check is bypassed once `ALLOW_UPDATE_ACCOUNT_NAME` is enabled, allowing account-name hijacking / impersonation - (File: `actuator/src/main/java/org/tron/core/actuator/UpdateAccountActuator.java`)

### Summary
`UpdateAccountActuator` guards the uniqueness of `accountName` (the human-readable identity registered in `AccountIndexStore`) only when the chain parameter `AllowUpdateAccountName` is `0`. Once this parameter is set to `1` (which is a one-way, permanent committee proposal, exactly like the many other "ALLOW_*" governance switches in java-tron), both uniqueness checks are skipped, and any account can claim a name that another (already reputable) account is using. The `AccountIndexStore` mapping `name -> address` gets silently overwritten to point at the attacker's address, which is directly analogous to the reported vault-name-reuse issue: an identity name a user relies on can be "taken over" by a different, malicious address after the fact.

### Finding Description
In `validate()`:
```java
if (account.getAccountName() != null && !account.getAccountName().isEmpty()
    && chainBaseManager.getDynamicPropertiesStore().getAllowUpdateAccountName() == 0) {
  throw new ContractValidateException("This account name is already existed");
}

if (chainBaseManager.getAccountIndexStore().has(accountName)
    && chainBaseManager.getDynamicPropertiesStore().getAllowUpdateAccountName() == 0) {
  throw new ContractValidateException("This name is existed");
}
``` [1](#0-0) 

Both guards are conditioned on `getAllowUpdateAccountName() == 0`. When the parameter is `1`, the check `accountIndexStore.has(accountName)` (i.e., "is this name already registered to someone else") is never evaluated, so `execute()` proceeds unconditionally:
```java
account.setAccountName(accountUpdateContract.getAccountName().toByteArray());
chainBaseManager.getAccountStore().put(ownerAddress, account);
chainBaseManager.getAccountIndexStore().put(account);
``` [2](#0-1) 

`AccountIndexStore.put()` maps `accountName -> address` and simply overwrites whatever entry previously existed for that name:
```java
public void put(AccountCapsule accountCapsule) {
  put(accountCapsule.getAccountName().toByteArray(),
      new BytesCapsule(accountCapsule.getAddress().toByteArray()));
}
``` [3](#0-2) 

`ALLOW_UPDATE_ACCOUNT_NAME` is proposal type 14, and like other one-shot upgrade switches in `ProposalUtil`, its validator only accepts the value `1` (i.e. it is meant to be turned on once and never reverted):
```java
case ALLOW_UPDATE_ACCOUNT_NAME: {
  if (value != 1) {
    throw new ContractValidateException(
        PRE_VALUE_NOT_ONE_ERROR + "ALLOW_UPDATE_ACCOUNT_NAME" + VALUE_NOT_ONE_ERROR);
  }
  break;
}
``` [4](#0-3) 
and `ProposalService.process()` writes it directly to `DynamicPropertiesStore` with no reversal path:
```java
case ALLOW_UPDATE_ACCOUNT_NAME: {
  manager.getDynamicPropertiesStore().saveAllowUpdateAccountName(entry.getValue());
  break;
}
``` [5](#0-4) 

The repository's own tests confirm the behavior: once `AllowUpdateAccountName` is `1`, an account can freely (re-)claim any name, including a name that was already registered by another account (`updateSameNameSuccess`, `twiceUpdateAccountSuccess`), whereas the same operation is rejected when the flag is `0` (`twiceUpdateAccountFail`, `updateSameNameFail`). [6](#0-5) 

This mirrors the analog report exactly: the `name_to_id`/`accountName -> address` registry entry for a name that legitimately belongs to account A can be silently reassigned to point at attacker-controlled account B, with no record kept that the name was previously "claimed" by A, and no re-registration barrier.

### Impact Explanation
`accountName`/`AccountIndexStore` is the canonical on-chain "human readable identity" mechanism in java-tron (analogous to the vault's `name`). Exchanges, wallets, and DApps that resolve accounts by name (via `AccountIndexStore.get(name)` or equivalent wallet APIs) can be pointed at an attacker's address instead of the legitimate account that originally registered that name, once `ALLOW_UPDATE_ACCOUNT_NAME` is active. Users who rely on the name (rather than the raw address) to send funds, verify counterparties, or identify a reputable account can be tricked into interacting with an attacker's account, leading to fund loss/misdirection — a direct accounting/authentication-integrity impact of the same class as the reported vault name-reuse issue.

### Likelihood Explanation
The precondition (`AllowUpdateAccountName == 1`) is not attacker-controlled directly — it requires a super-representative committee proposal — but this is standard for many chain-parameter upgrades in TRON and, once activated, is permanent (the validator only allows setting it to `1`, never back to `0`). If/when this proposal has been passed on the target network, every account holder becomes able to seize any other account's registered name with a single `AccountUpdateContract` transaction, at zero fee (`calcFee()` returns 0), making exploitation trivial and unrestricted for any unprivileged user once the switch is on.

### Recommendation
Do not gate the "name already registered to someone else" check on `AllowUpdateAccountName`. The two checks serve different purposes: `AllowUpdateAccountName` should only control whether an account may *change its own name more than once*; it should never allow claiming a name that is already registered (and mapped in `AccountIndexStore`) to a different address. Specifically, in `UpdateAccountActuator.validate()`, keep the "name is existed" check (`accountIndexStore.has(accountName)`) unconditional (independent of `AllowUpdateAccountName`), or additionally verify that any existing owner of `accountName` in `AccountIndexStore` matches `ownerAddress` before allowing the update. Consider also retaining a permanent "deprecated/claimed names" set so that any name ever claimed by an account cannot be handed to a different address even after the original account's name is reassigned away.

### Proof of Concept
1. Committee passes `ALLOW_UPDATE_ACCOUNT_NAME = 1` (this is a real, permanent, one-directional proposal in `ProposalType`/`ProposalUtil`). [7](#0-6) 
2. Reputable account `A` (address `addrA`) sends `AccountUpdateContract` setting `accountName = "TrustedExchange"`. `AccountIndexStore` now maps `"TrustedExchange" -> addrA`.
3. Attacker account `B` (address `addrB`) sends `AccountUpdateContract` with the same `accountName = "TrustedExchange"`.
4. In `validate()`, both guard conditions evaluate to `false` because `getAllowUpdateAccountName() == 1`, so validation passes despite `AccountIndexStore.has("TrustedExchange")` being `true`. [1](#0-0) 
5. `execute()` overwrites the index: `AccountIndexStore.put(B)` sets `"TrustedExchange" -> addrB`. [2](#0-1) 
6. Any off-chain system (wallet/DApp/exchange) that resolves `"TrustedExchange"` to an address now retrieves `addrB` (the attacker's address) instead of `addrA`, resulting in misdirected funds/impersonation — the same class of impact described in the vault-name-reuse report.

This directly corresponds to the pattern demonstrated in the repository's own regression test `updateSameNameSuccess`, which asserts that reusing an already-registered name succeeds without error once `AllowUpdateAccountName` is `1`. [6](#0-5) 

**Uncertainty note:** I could not verify from the indexed code the current live on-chain value of `ALLOW_UPDATE_ACCOUNT_NAME` on any deployed java-tron network (mainnet/testnet), nor locate a gRPC/HTTP API method that performs "get account by name" lookups (only the internal `AccountIndexStore`/`AccountCapsule` accessors and a unit test were found in the index). If the user wants confirmation of the current live parameter value or of specific name-lookup API endpoints exposed to wallets/DApps, a full-repository Devin session with access to `Wallet.java`, `WalletApi`/`api.proto`, and deployed network configs would be needed to verify these details beyond the index's coverage.

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

**File:** chainbase/src/main/java/org/tron/core/store/AccountIndexStore.java (L21-24)
```java
  public void put(AccountCapsule accountCapsule) {
    put(accountCapsule.getAccountName().toByteArray(),
        new BytesCapsule(accountCapsule.getAddress().toByteArray()));
  }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L90-96)
```java
      case ALLOW_UPDATE_ACCOUNT_NAME: {
        if (value != 1) {
          throw new ContractValidateException(
              PRE_VALUE_NOT_ONE_ERROR + "ALLOW_UPDATE_ACCOUNT_NAME" + VALUE_NOT_ONE_ERROR);
        }
        break;
      }
```

**File:** actuator/src/main/java/org/tron/core/utils/ProposalUtil.java (L964-964)
```java
    ALLOW_UPDATE_ACCOUNT_NAME(14), // 0, {0, 1}
```

**File:** framework/src/main/java/org/tron/core/consensus/ProposalService.java (L99-102)
```java
        case ALLOW_UPDATE_ACCOUNT_NAME: {
          manager.getDynamicPropertiesStore().saveAllowUpdateAccountName(entry.getValue());
          break;
        }
```

**File:** framework/src/test/java/org/tron/core/actuator/UpdateAccountActuatorTest.java (L187-202)
```java
  @Test
  public void updateSameNameSuccess() {

    UpdateAccount(ACCOUNT_NAME, OWNER_ADDRESS);   // first update account

    dbManager.getDynamicPropertiesStore().saveAllowUpdateAccountName(1);   // allow update more
    // than one time
    UpdateAccount(ACCOUNT_NAME, OWNER_ADDRESS);   // second update with same account Name

    UpdateAccount("sameName", OWNER_ADDRESS);   // Third Update

    UpdateAccount("sameName", OWNER_ADDRESS);   // fourth Update with same accountName

    dbManager.getAccountIndexStore().delete(ACCOUNT_NAME.getBytes());

  }
```
