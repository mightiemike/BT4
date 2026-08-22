### Title
Unchecked `addAssetAmountV2`/`reduceAssetAmountV2` boolean result in TRC10 balance mutation paths mirrors "unchecked transfer result" bug class - ([File: actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java], [File: actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java])

### Summary
`AccountCapsule.addAssetAmountV2` / `reduceAssetAmountV2` return a `boolean` success indicator, analogous to an ERC20 `transfer`/`transferFrom` return value. In several call sites the boolean is checked and enforced (e.g. `TransferAssetActuator.validate/execute` checks `reduceAssetAmountV2` and throws on `false`), but the corresponding "credit" call `addAssetAmountV2` is invoked without checking its return value, and the same unchecked pattern repeats in `RepositoryImpl.addTokenBalance`, which is the path used by TVM TRC10 `transferToken`/precompiled-call handling.

### Finding Description
`AccountCapsule.addAssetAmountV2` is declared to return `boolean` but its current implementation unconditionally does `return true;` at the end of the method: [1](#0-0) 

`TransferAssetActuator.execute` checks the return value of `reduceAssetAmountV2` (the debit) and throws `ContractExeException` on failure, but calls `addAssetAmountV2` (the credit to `toAccountCapsule`) without checking its boolean result at all: [2](#0-1) 

The same asymmetric pattern exists in `RepositoryImpl.addTokenBalance`, which is the shared entry point used by the TVM engine when processing TRC10 balance changes triggered by contract calls (`Program.callToAddress`/`callToPrecompiledAddress`, `MUtil.transferToken`, `suicide`/`suicide2` token inheritance): [3](#0-2) 

Elsewhere, `Commons.adjustAssetBalanceV2` *does* check both directions and throws `BalanceInsufficientException` on failure: [4](#0-3) 

This inconsistency — some call sites treat the boolean as authoritative and revert on `false`, others silently ignore it — is structurally the same defect class as the "unchecked `transfer`/`transferFrom` result" finding: a state-changing operation that can signal failure via a boolean return is not universally checked, so if/when the "always return true" implementation of `addAssetAmountV2` is changed (e.g., to add an overflow guard mirroring `reduceAssetAmountV2`'s insufficient-balance guard), the unchecked call sites would silently no-op on failure while the paired debit operation (already checked and enforced) still executes, corrupting the token accounting invariant (total supply/sum of balances).

### Impact Explanation
If `addAssetAmountV2` is ever extended to return `false` on overflow or another validity failure (a very plausible future change, since its sibling `reduceAssetAmountV2` already returns `false` on insufficient balance and overflow-style guards are a well established pattern in this codebase), the unchecked call sites in `TransferAssetActuator.execute` and `RepositoryImpl.addTokenBalance` would let the debit succeed while the credit silently fails — burning TRC10 tokens from the sender without crediting the receiver. This is an asset/accounting corruption bug class, reachable from a plain `TransferAssetContract` broadcast transaction or from a TVM `transferToken` call inside any triggered smart contract.

### Likelihood Explanation
Currently `addAssetAmountV2`'s implementation always returns `true`, so the concrete failure mode is **not exploitable today** — this is a latent code-hygiene/defense-in-depth gap rather than a live, triggerable vulnerability under the current method body. Likelihood of actual exploitation is Low given the present implementation, but the risk is real for any future maintenance change to `addAssetAmountV2` (e.g., adding an overflow check) since the calling code does not enforce the invariant.

### Recommendation
Make `addAssetAmountV2`'s return value authoritative and check it at every call site the same way `reduceAssetAmountV2` is checked, in particular:
- `TransferAssetActuator.execute` (actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java, lines 82-84): throw `ContractExeException` if `addAssetAmountV2` returns `false`.
- `RepositoryImpl.addTokenBalance` (actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java, lines 863-870): throw on `false` from either branch, consistent with the negative-value branch's `RuntimeException` for insufficient balance.

### Proof of Concept
Not applicable under the current implementation — `addAssetAmountV2` unconditionally returns `true`, so no runnable PoC produces divergent behavior today. The finding is a defense-in-depth/consistency gap: demonstrate by temporarily modifying `addAssetAmountV2` to return `false` on an `addExact` overflow (mirroring `reduceAssetAmountV2`'s insufficient-balance check) and observing that `TransferAssetActuator.execute` and `RepositoryImpl.addTokenBalance` proceed to commit the debit and `ret.setStatus(fee, code.SUCESS)` / continue execution without reverting, while the credit silently did not happen.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L733-764)
```java
  public boolean addAssetAmountV2(byte[] key, long amount,
      DynamicPropertiesStore dynamicPropertiesStore, AssetIssueStore assetIssueStore) {
    importAsset(key);
    boolean disableJavaLangMath = dynamicPropertiesStore.disableJavaLangMath();
    //key is token name
    if (dynamicPropertiesStore.getAllowSameTokenName() == 0) {
      Map<String, Long> assetMap = this.account.getAssetMap();
      AssetIssueCapsule assetIssueCapsule = assetIssueStore.get(key);
      String tokenID = assetIssueCapsule.getId();
      String nameKey = ByteArray.toStr(key);
      Long currentAmount = assetMap.get(nameKey);
      if (currentAmount == null) {
        currentAmount = 0L;
      }
      this.account = this.account.toBuilder()
          .putAsset(nameKey, addExact(currentAmount, amount, disableJavaLangMath))
          .putAssetV2(tokenID, addExact(currentAmount, amount, disableJavaLangMath))
          .build();
    }
    //key is token id
    if (dynamicPropertiesStore.getAllowSameTokenName() == 1) {
      String tokenIDStr = ByteArray.toStr(key);
      Map<String, Long> assetMapV2 = this.account.getAssetV2Map();
      Long currentAmount = assetMapV2.get(tokenIDStr);
      if (currentAmount == null) {
        currentAmount = 0L;
      }
      this.account = this.account.toBuilder()
          .putAssetV2(tokenIDStr, addExact(currentAmount, amount, disableJavaLangMath))
          .build();
    }
    return true;
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L75-84)
```java
      AccountCapsule ownerAccountCapsule = accountStore.get(ownerAddress);
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
      accountStore.put(ownerAddress, ownerAccountCapsule);

      toAccountCapsule
          .addAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore);
      accountStore.put(toAddress, toAccountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/vm/repository/RepositoryImpl.java (L846-875)
```java
  @Override
  public long addTokenBalance(byte[] address, byte[] tokenId, long value) {
    byte[] tokenIdWithoutLeadingZero = ByteUtil.stripLeadingZeroes(tokenId);
    AccountCapsule accountCapsule = getAccount(address);
    if (accountCapsule == null) {
      accountCapsule = createAccount(address, Protocol.AccountType.Normal);
    }
    long balance = accountCapsule.getAssetV2(new String(tokenIdWithoutLeadingZero));
    if (value == 0) {
      return balance;
    }

    if (value < 0 && balance < -value) {
      throw new RuntimeException(
          StringUtil.createReadableString(accountCapsule.createDbKey())
              + " insufficient balance");
    }
    if (value >= 0) {
      accountCapsule.addAssetAmountV2(tokenIdWithoutLeadingZero, value, getDynamicPropertiesStore(),
          getAssetIssueStore());
    } else {
      accountCapsule
          .reduceAssetAmountV2(tokenIdWithoutLeadingZero, -value, getDynamicPropertiesStore(),
              getAssetIssueStore());
    }
    Key key = Key.create(address);
    accountCache.put(key, Value.create(accountCapsule,
         accountCache.get(key).getType().addType(Type.DIRTY)));
    return accountCapsule.getAssetV2(new String(tokenIdWithoutLeadingZero));
  }
```

**File:** chainbase/src/main/java/org/tron/common/utils/Commons.java (L131-150)
```java
  public static void adjustAssetBalanceV2(AccountCapsule account, String AssetID, long amount,
      AccountStore accountStore, AssetIssueStore assetIssueStore,
      DynamicPropertiesStore dynamicPropertiesStore)
      throws BalanceInsufficientException {
    if (amount < 0) {
      if (!account.reduceAssetAmountV2(AssetID.getBytes(), -amount, dynamicPropertiesStore,
          assetIssueStore)) {
        throw new BalanceInsufficientException(
            String.format("reduceAssetAmount failed! account: %s",
                    StringUtil.encode58Check(account.createDbKey())));
      }
    } else if (amount > 0 &&
        !account.addAssetAmountV2(AssetID.getBytes(), amount, dynamicPropertiesStore,
            assetIssueStore)) {
      throw new BalanceInsufficientException(
          String.format("addAssetAmount failed! account: %s",
                  StringUtil.encode58Check(account.createDbKey())));
    }
    accountStore.put(account.getAddress().toByteArray(), account);
  }
```
