## Finding: `AccountCapsule.reduceAssetAmountV2()` return value unchecked in `ExchangeInjectActuator.execute()`

### Title
Unchecked boolean return value of `reduceAssetAmountV2()` in `ExchangeInjectActuator` allows exchange pool balance to be credited without provable token deduction - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
`ExchangeInjectActuator.execute()` calls `AccountCapsule.reduceAssetAmountV2()` twice — once for the injected token and once for the proportionally-computed "another token" — but discards the boolean return value in both cases, unlike the equivalent pattern used elsewhere in the codebase.

### Finding Description
`reduceAssetAmountV2()` is designed to report success/failure of a balance deduction: it returns `false` when the account has no entry for the asset or the current amount is less than the amount requested, and only mutates state and returns `true` when the deduction is actually valid. [1](#0-0) 

Callers are expected to check this return value and abort the operation on failure. This is exactly the pattern used in `TransferAssetActuator.execute()`: [2](#0-1) 

and in `ParticipateAssetIssueActuator.execute()`: [3](#0-2) 

and in the shared helper `Commons.adjustAssetBalanceV2()`: [4](#0-3) 

However, `ExchangeInjectActuator.execute()` ignores the return value for both calls: [5](#0-4) 

If `reduceAssetAmountV2()` returns `false` for either the primary token or the derived "another token" (e.g., because the owner account lacks a populated asset entry for that token, or the computed `anotherTokenQuant` exceeds the owner's actual balance of that asset at execution time), the actuator does not detect the failure. It proceeds to update `exchangeCapsule.setBalance(...)` with the full injected amounts, persists the account (unchanged for that asset), commits the exchange capsule with inflated `firstTokenBalance`/`secondTokenBalance`, and marks the transaction `SUCESS`.

### Impact Explanation
This directly parallels the reported bug class (auraPool.withdrawAndUnwrap()'s unchecked boolean silently masking a failed withdrawal): a state-mutating operation's success/failure signal is dropped, letting the actuator report success and update dependent accounting (the exchange pool's token reserves) even though the underlying asset deduction from the owner's account did not occur for one of the two tokens. Because subsequent trades (`ExchangeTransactionActuator`, `ExchangeWithdrawActuator`) price off `firstTokenBalance`/`secondTokenBalance`, an inflated pool balance not backed by an actual deduction corrupts the AMM-style constant-product accounting used by TRON's on-chain exchange, which can be exploited to extract value from other traders/liquidity in the exchange.

### Likelihood Explanation
Exploitability depends on whether `validate()` (beyond the portion inspected) independently guarantees sufficient balance for both the injected token and the derived `anotherTokenQuant` at the moment of `execute()`. I was only able to confirm the validate checks through line 230 (balance for `tokenID`/fee and quant positivity); I could not fully confirm within the available tool budget whether it also strictly guarantees `anotherTokenQuant` is covered by the owner's actual holdings of `anotherTokenID` for every code path (e.g., race with other pending balance-affecting actuators in the same block, or asset entries not yet imported via `importAsset`). This introduces genuine uncertainty about real-world reachability, so likelihood is assessed as low-to-medium pending further verification of the full `validate()` method and any TOCTOU windows between validation and execution.

### Recommendation
Check the boolean return values of both `reduceAssetAmountV2()` calls in `ExchangeInjectActuator.execute()` (lines 91 and 98) and throw `ContractExeException` on failure, mirroring the pattern already used in `TransferAssetActuator`, `ParticipateAssetIssueActuator`, and `Commons.adjustAssetBalanceV2`, so the exchange pool balances are never updated unless the corresponding account deductions actually succeeded.

### Proof of Concept
Not independently constructed/verified — a concrete PoC would require confirming a state (e.g., stale/absent asset map entry, or a preceding transaction in the same block that first reduces the owner's `anotherTokenID` balance) under which `reduceAssetAmountV2()` returns `false` for the `anotherTokenQuant` deduction while `validate()` had earlier judged the injection valid. This should be validated with a full re-read of `ExchangeInjectActuator.doValidate()` beyond line 230 and reproduction via a unit/integration test simulating a race or stale-balance condition.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java (L780-813)
```java
  public boolean reduceAssetAmountV2(byte[] key, long amount,
                                     DynamicPropertiesStore dynamicPropertiesStore, AssetIssueStore assetIssueStore) {
    importAsset(key);
    //key is token name
    boolean disableJavaLangMath = dynamicPropertiesStore.disableJavaLangMath();
    if (dynamicPropertiesStore.getAllowSameTokenName() == 0) {
      Map<String, Long> assetMap = this.account.getAssetMap();
      AssetIssueCapsule assetIssueCapsule = assetIssueStore.get(key);
      String tokenID = assetIssueCapsule.getId();
      String nameKey = ByteArray.toStr(key);
      Long currentAmount = assetMap.get(nameKey);
      if (amount > 0 && null != currentAmount && amount <= currentAmount) {
        this.account = this.account.toBuilder()
                .putAsset(nameKey, subtractExact(currentAmount, amount, disableJavaLangMath))
                .putAssetV2(tokenID, subtractExact(currentAmount, amount, disableJavaLangMath))
                .build();
        return true;
      }
    }
    //key is token id
    if (dynamicPropertiesStore.getAllowSameTokenName() == 1) {
      String tokenID = ByteArray.toStr(key);
      Map<String, Long> assetMapV2 = this.account.getAssetV2Map();
      Long currentAmount = assetMapV2.get(tokenID);
      if (amount > 0 && null != currentAmount && amount <= currentAmount) {
        this.account = this.account.toBuilder()
                .putAssetV2(tokenID, subtractExact(currentAmount, amount, disableJavaLangMath))
                .build();
        return true;
      }
    }

    return false;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L76-79)
```java
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L85-87)
```java
      if (!toAccount.reduceAssetAmountV2(key, exchangeAmount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L88-103)
```java
      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .reduceAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }
      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);
```
