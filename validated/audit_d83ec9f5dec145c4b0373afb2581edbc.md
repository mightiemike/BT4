### Title
Ignored return value of `reduceAssetAmountV2`/`addAssetAmountV2` in accounting actuators can allow token balance corruption - (File: `actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java`)

### Summary
The reported Amp.sol bug is a classic "ignored return value from a balance-mutating call" pattern: `transferFrom` can fail silently (return `false` instead of reverting), and the caller proceeds as if it succeeded, corrupting internal accounting. In java-tron, the internal analog of `transferFrom` for TRC10 assets is `AccountCapsule.reduceAssetAmountV2()` / `addAssetAmountV2()`, which likewise return a `boolean` success indicator instead of throwing on failure [1](#0-0) . Several actuators reachable from broadcast transactions call these methods and discard the boolean result, unlike `TransferAssetActuator` and `Commons.adjustAssetBalanceV2`, which correctly check it and throw on failure [2](#0-1) [3](#0-2) .

### Finding Description
`AccountCapsule.reduceAssetAmountV2` only mutates state and returns `true` when `amount > 0 && currentAmount != null && amount <= currentAmount`; otherwise it silently returns `false` without changing state and without throwing [1](#0-0) . This is functionally the same "boolean success/failure signal that must be checked" pattern as ERC20 `transferFrom`.

`MarketSellAssetActuator.transferBalanceOrToken()` calls `reduceAssetAmountV2` on the seller's account to debit the sell-token quantity but never checks the returned boolean:
```
accountCapsule.reduceAssetAmountV2(sellTokenID, sellTokenQuantity, dynamicStore, assetIssueStore);
``` [4](#0-3) 

The same actuator's `addTrxOrToken()` helpers call `addAssetAmountV2` to credit the buy-token quantity to both the taker and the maker, again ignoring the return value: [5](#0-4) 

`ExchangeWithdrawActuator.execute()` similarly credits token balances via `addAssetAmountV2` twice without checking the boolean result before committing the account and exchange pool state: [6](#0-5) 

`ParticipateAssetIssueActuator.execute()` is internally inconsistent: it checks the return of `toAccount.reduceAssetAmountV2(...)` and throws on failure, but ignores the return of the preceding `ownerAccount.addAssetAmountV2(...)` call for the same logical operation: [7](#0-6) 

By contrast, `TransferAssetActuator` and `Commons.adjustAssetBalanceV2` correctly check and throw `ContractExeException`/`BalanceInsufficientException` on `false`, showing that the codebase's own convention treats an unchecked `false` return as a validated risk that must be guarded against [8](#0-7) [3](#0-2) .

`RepositoryImpl.addTokenBalance` (used by the TVM `transferToken` opcode path) has the same issue for the debit branch: it calls `reduceAssetAmountV2` without checking the boolean, relying entirely on an earlier manual `balance < -value` check rather than the authoritative return value of the mutator itself [9](#0-8) .

### Impact Explanation
If any code path reaches these debit/credit calls with a stale or inconsistent expectation of balance sufficiency (e.g. a mismatch between the state seen at `validate()` time and the state actually present at `execute()` time, or an internal accounting bug that causes `currentAmount` to be `null`/insufficient unexpectedly), the debit silently no-ops while the corresponding credit to the counterparty still proceeds (or vice versa), producing an asset-accounting divergence: tokens can be created or destroyed without a matching real balance change. This falls into "asset or accounting corruption" territory explicitly in scope for this analysis. `MarketSellAssetActuator` and `ExchangeWithdrawActuator` are both reachable directly from anonymous broadcast transactions (`MarketSellAssetContract`, `ExchangeWithdrawContract`), matching the "broadcast transaction" reachability requirement.

### Likelihood Explanation
Under the current invariants, `validate()` for these actuators (e.g. `assetBalanceEnoughV2` checks in `MarketSellAssetActuator.validate()`) is designed to guarantee that the subsequent `execute()`-time calls to `reduceAssetAmountV2`/`addAssetAmountV2` will succeed, which is why the missing checks have not manifested as an observed exploit in the reviewed code paths. I was not able to fully verify, within the available tool budget, whether the validate-then-execute sequencing for every one of these actuators is strictly atomic with respect to a single logical balance check (i.e., whether any code path—such as market order matching across multiple maker orders within one `execute()`, or multi-contract transactions—can cause the account's asset balance to change between the moment it was validated and the moment the unchecked mutator runs). This should be confirmed with deeper tracing of `MarketSellAssetActuator.matchOrder`/`matchSingleOrder`, which perform several sequential, unguarded `addAssetAmountV2`/`reduceAssetAmountV2` calls across multiple maker orders within a single `execute()` invocation without re-validating balances between each call. Given this, likelihood is assessed as **Low-to-Medium**: a real, defense-in-depth-violating pattern exists and is clearly a deviation from the codebase's own established convention (checked in `TransferAssetActuator`/`Commons.adjustAssetBalanceV2`), but no concrete unguarded state transition that bypasses `validate()`'s balance guarantee was confirmed in this session.

### Recommendation
Enforce checking of the boolean return value at every call site of `reduceAssetAmountV2` / `addAssetAmountV2` (and `RepositoryImpl.addTokenBalance`'s reduce branch), throwing `ContractExeException`/`BalanceInsufficientException` on `false`, exactly as already done in `TransferAssetActuator` and `Commons.adjustAssetBalanceV2`. Specifically:
- `MarketSellAssetActuator.transferBalanceOrToken`, `addTrxOrToken` (both overloads), and `returnSellTokenRemain`.
- `ExchangeWithdrawActuator.execute` (both `addAssetAmountV2` calls).
- `ParticipateAssetIssueActuator.execute` (the `ownerAccount.addAssetAmountV2` call).
- `UnfreezeAssetActuator.execute`.
- `RepositoryImpl.addTokenBalance` (the `reduceAssetAmountV2` branch).

### Proof of Concept
A concrete, exploitable trigger was not established within this session — the finding is a code-pattern/defense-in-depth deviation rather than a confirmed unguarded state transition. To construct a PoC, one would need to identify a path where `MarketSellAssetActuator.matchOrder`/`matchSingleOrder` or `ExchangeWithdrawActuator.execute` invoke `addAssetAmountV2`/`reduceAssetAmountV2` against a balance state that was not (or no longer) guaranteed by `validate()`, e.g. via multiple maker-order matches inside a single taker `execute()` call whose cumulative token movements are not re-validated against real-time balances before each mutation [10](#0-9) .

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

**File:** actuator/src/main/java/org/tron/core/actuator/TransferAssetActuator.java (L76-83)
```java
      if (!ownerAccountCapsule
          .reduceAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
      accountStore.put(ownerAddress, ownerAccountCapsule);

      toAccountCapsule
          .addAssetAmountV2(assetName.toByteArray(), amount, dynamicStore, assetIssueStore);
```

**File:** chainbase/src/main/java/org/tron/common/utils/Commons.java (L131-148)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L440-490)
```java
    } else if (takerBuyTokenQuantityRemain < makerOrderCapsule.getSellTokenQuantityRemain()) {
      // taker < maker
      // if the quantity of taker want to buy is smaller than the remain of maker want to sell,
      // consume the order of the taker

      takerBuyTokenQuantityReceive = takerBuyTokenQuantityRemain;
      makerBuyTokenQuantityReceive = takerOrderCapsule.getSellTokenQuantityRemain();

      takerOrderCapsule.setSellTokenQuantityRemain(0);
      MarketUtils.updateOrderState(takerOrderCapsule, State.INACTIVE, marketAccountStore);

      makerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
          makerOrderCapsule.getSellTokenQuantityRemain(), takerBuyTokenQuantityRemain));
    } else {
      // taker > maker
      takerBuyTokenQuantityReceive = makerOrderCapsule.getSellTokenQuantityRemain();

      // if the quantity of taker want to buy is bigger than the remain of maker want to sell,
      // consume the order of maker
      // makerSellTokenQuantityRemain_A/makerBuyTokenQuantityCurrent_TRX =
      //   makerSellTokenQuantity_A/makerBuyTokenQuantity_TRX
      makerBuyTokenQuantityReceive = MarketUtils
          .multiplyAndDivide(makerSellRemainQuantity, makerBuyQuantity, makerSellQuantity,
              this.disableJavaLangMath());

      MarketUtils.updateOrderState(makerOrderCapsule, State.INACTIVE, marketAccountStore);
      if (makerBuyTokenQuantityReceive == 0) {
        // the quantity is too small, return the remain of sellToken to maker
        // it would not happen here
        // for the maker, when sellQuantity < buyQuantity, it will get at least one buyToken
        // even when sellRemain = 1.
        // so if sellQuantity=200，buyQuantity=100, when sellRemain=1, it needs to be satisfied
        // the following conditions:
        // makerOrderCapsule.getSellTokenQuantityRemain() - takerBuyTokenQuantityRemain = 1
        // 200 - 200/100 * X = 1 ===> X = 199/2，and this comports with the fact that X is integer.
        makerOrderCapsule.setSellTokenQuantityReturn();
        returnSellTokenRemain(makerOrderCapsule);
        return;
      } else {
        makerOrderCapsule.setSellTokenQuantityRemain(0);
        takerOrderCapsule.setSellTokenQuantityRemain(subtractExact(
            takerOrderCapsule.getSellTokenQuantityRemain(), makerBuyTokenQuantityReceive));
      }
    }

    // save makerOrderCapsule
    orderStore.put(makerOrderCapsule.getID().toByteArray(), makerOrderCapsule);

    // add token into account
    addTrxOrToken(takerOrderCapsule, takerBuyTokenQuantityReceive, takerAccountCapsule);
    addTrxOrToken(makerOrderCapsule, makerBuyTokenQuantityReceive);
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L527-535)
```java
  private void transferBalanceOrToken(AccountCapsule accountCapsule) {
    if (Arrays.equals(sellTokenID, "_".getBytes())) {
      accountCapsule.setBalance(subtractExact(
          accountCapsule.getBalance(), sellTokenQuantity));
    } else {
      accountCapsule
          .reduceAssetAmountV2(sellTokenID, sellTokenQuantity, dynamicStore, assetIssueStore);
    }
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/MarketSellAssetActuator.java (L537-562)
```java
  // for taker
  private void addTrxOrToken(MarketOrderCapsule orderCapsule, long num,
      AccountCapsule accountCapsule) {

    byte[] buyTokenId = orderCapsule.getBuyTokenId();
    if (Arrays.equals(buyTokenId, "_".getBytes())) {
      accountCapsule.setBalance(addExact(accountCapsule.getBalance(), num));
    } else {
      accountCapsule
          .addAssetAmountV2(buyTokenId, num, dynamicStore, assetIssueStore);
    }
  }

  private void addTrxOrToken(MarketOrderCapsule orderCapsule, long num) {
    AccountCapsule accountCapsule = accountStore
        .get(orderCapsule.getOwnerAddress().toByteArray());

    byte[] buyTokenId = orderCapsule.getBuyTokenId();
    if (Arrays.equals(buyTokenId, "_".getBytes())) {
      accountCapsule.setBalance(addExact(accountCapsule.getBalance(), num));
    } else {
      accountCapsule
          .addAssetAmountV2(buyTokenId, num, dynamicStore, assetIssueStore);
    }
    accountStore.put(orderCapsule.getOwnerAddress().toByteArray(), accountCapsule);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L93-106)
```java
      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, tokenQuant));
      } else {
        accountCapsule.addAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ParticipateAssetIssueActuator.java (L77-87)
```java
      long exchangeAmount = multiplyExact(cost, assetIssueCapsule.getNum());
      exchangeAmount = floorDiv(exchangeAmount, assetIssueCapsule.getTrxNum());
      ownerAccount.addAssetAmountV2(key, exchangeAmount, dynamicStore, assetIssueStore);

      //add to to_address
      byte[] toAddress = participateAssetIssueContract.getToAddress().toByteArray();
      AccountCapsule toAccount = accountStore.get(toAddress);
      toAccount.setBalance(addExact(toAccount.getBalance(), cost));
      if (!toAccount.reduceAssetAmountV2(key, exchangeAmount, dynamicStore, assetIssueStore)) {
        throw new ContractExeException("reduceAssetAmount failed !");
      }
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
