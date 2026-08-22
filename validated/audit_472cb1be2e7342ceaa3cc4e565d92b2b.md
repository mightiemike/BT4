### Title
Permissionless `ExchangeCreateContract` allows front-running attacker to seed an AMM pool with an arbitrary skewed price, enabling cheap mispriced asset extraction - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java])

### Summary
`ExchangeCreateActuator` lets any account with a valid address and the small creation fee broadcast an `ExchangeCreateContract` to create a new bancor-style TRX/token (or token/token) liquidity pool at any ratio it chooses. There is no allowlist of eligible tokens, no minimum-liquidity requirement, and no cooldown before the pool becomes tradable via `ExchangeTransactionActuator`. This mirrors the reported Vader/DAO finding where an attacker front-runs the intended pool creation for a new asset and seeds it with a heavily skewed ratio, then immediately profits by trading against the mispriced pool before anyone can react.

### Finding Description
`ExchangeCreateActuator.doValidate()` only checks: the owner account exists and can pay the fee, the two token IDs differ, `firstTokenBalance`/`secondTokenBalance` are `> 0`, both are below `dynamicStore.getExchangeBalanceLimit()`, and the owner actually holds the funds it commits. [1](#0-0) 

Nothing restricts which token pairs may be paired, nor enforces any minimum absolute liquidity or ratio sanity — an attacker can set, e.g., `firstTokenBalance = 1` and `secondTokenBalance = <balanceLimit>`, creating an exchange whose price is off by many orders of magnitude from the "fair" market price of the underlying asset. [2](#0-1) 

Once created, the pool ID is assigned deterministically (`getLatestExchangeNum()+1`) and it becomes tradable instantly through `ExchangeTransactionActuator`, which uses the Bancor-relay formula in `ExchangeProcessor`/`SafeExchangeProcessor` to compute swap output strictly from the pool's current (attacker-chosen) balances — with no external price oracle or sanity check on the resulting price. [3](#0-2) [4](#0-3) 

Because pool creation and the exchange ID assignment happen in a single anonymous, unprivileged, broadcast transaction, an attacker monitoring the mempool for a legitimate `ExchangeCreateContract` for a newly popular token (or simply racing to create the pool first, since anyone can create a pool for any token pair at any time) can front-run it with a favorable skewed ratio, then immediately issue an `ExchangeTransactionContract` to drain value from users/other participants who trust that exchange ID, or to buy the token supply cheaply before its "real" pool exists.

### Impact Explanation
This is asset/accounting corruption reachable purely via broadcast transactions from any funded account:
- An attacker can lock in an arbitrary, attacker-favorable exchange rate for a token pair, then extract value from any subsequent trader who interacts with that exchange (e.g., via `ExchangeTransactionActuator`, `ExchangeInjectActuator`, `ExchangeWithdrawActuator`) under the false assumption the pool reflects a fair price.
- There is no cool-down or minimum-liquidity gate, so the same block/transaction ordering used in the front-run in the referenced report is directly reproducible here: whoever submits the `ExchangeCreateContract` first controls the pool's initial price, and can immediately trade against it.
- Because `dynamicStore.getExchangeBalanceLimit()` only bounds the maximum balance, not the minimum or the ratio between the two balances, the price can be skewed to essentially any multiple within that limit.

### Likelihood Explanation
High likelihood: no privileged role, key leak, or malicious peer is required — only funds to pay the exchange-create fee and (for TRX-token pairs) 1 unit of the token side. Any Full Node/gRPC/HTTP client can submit the `ExchangeCreateContract` and immediately follow with an `ExchangeTransactionContract`, and transaction ordering (mempool visibility / same-block placement) is enough to front-run any legitimate pool creation for a given token pair.

### Recommendation
Add safeguards to `ExchangeCreateActuator.doValidate()`/`execute()`:
- Require a minimum absolute liquidity for both `firstTokenBalance` and `secondTokenBalance` (not just `> 0`), scaled to the token's precision, to prevent 1-wei-style seeding.
- Consider restricting exchange creation to token pairs that meet a minimum recognized total supply/liquidity, or introduce a cool-down/challenge period during which trades against a freshly created pool are rate-limited or disallowed, mirroring the report's suggested mitigation of a minimum-liquidity + cooldown gate before permissionless usage is allowed.

### Proof of Concept
1. Attacker observes (or anticipates) that a popular new asset `X` will get an exchange pool against TRX.
2. Attacker submits `ExchangeCreateContract` with `firstTokenId = X`, `firstTokenBalance = 1`, `secondTokenId = TRX ("_")`, `secondTokenBalance = 1_000_000_000000L` (bounded only by `getExchangeBalanceLimit()`) — validated and executed per [5](#0-4) .
3. Attacker immediately submits an `ExchangeTransactionContract` selling TRX for `X` (or vice-versa) against the newly created `exchangeId`, receiving a wildly favorable `anotherTokenQuant` computed by `ExchangeCapsule.transaction` per [6](#0-5) .
4. Legitimate liquidity providers or users who later interact with this exchange ID trade against the corrupted price, and there is no mechanism in `Wallet.java`/actuators to revoke or reset a created exchange.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L43-76)
```java
    long fee = calcFee();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    try {
      final ExchangeCreateContract exchangeCreateContract = this.any
          .unpack(ExchangeCreateContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeCreateContract.getOwnerAddress().toByteArray());

      byte[] firstTokenID = exchangeCreateContract.getFirstTokenId().toByteArray();
      byte[] secondTokenID = exchangeCreateContract.getSecondTokenId().toByteArray();
      long firstTokenBalance = exchangeCreateContract.getFirstTokenBalance();
      long secondTokenBalance = exchangeCreateContract.getSecondTokenBalance();

      long newBalance = subtractExact(accountCapsule.getBalance(), fee);

      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, firstTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(firstTokenID, firstTokenBalance, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, secondTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(secondTokenID, secondTokenBalance, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L183-208)
```java
    byte[] firstTokenID = contract.getFirstTokenId().toByteArray();
    byte[] secondTokenID = contract.getSecondTokenId().toByteArray();
    long firstTokenBalance = contract.getFirstTokenBalance();
    long secondTokenBalance = contract.getSecondTokenBalance();

    if (dynamicStore.getAllowSameTokenName() == 1) {
      if (!Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES) && !isNumber(firstTokenID)) {
        throw new ContractValidateException("first token id is not a valid number");
      }
      if (!Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES) && !isNumber(secondTokenID)) {
        throw new ContractValidateException("second token id is not a valid number");
      }
    }

    if (Arrays.equals(firstTokenID, secondTokenID)) {
      throw new ContractValidateException("cannot exchange same tokens");
    }

    if (firstTokenBalance <= 0 || secondTokenBalance <= 0) {
      throw new ContractValidateException("token balance must greater than zero");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-158)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

    long buyTokenQuant = 0;
    long firstTokenBalance = this.exchange.getFirstTokenBalance();
    long secondTokenBalance = this.exchange.getSecondTokenBalance();
    long newFirstTokenBalance;
    long newSecondTokenBalance;

    if (this.exchange.getFirstTokenId().equals(ByteString.copyFrom(sellTokenID))) {
      buyTokenQuant = processor.exchange(firstTokenBalance,
          secondTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(firstTokenBalance, sellTokenQuant)
          : firstTokenBalance + sellTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(secondTokenBalance, buyTokenQuant)
          : secondTokenBalance - buyTokenQuant;

    } else {
      buyTokenQuant = processor.exchange(secondTokenBalance,
          firstTokenBalance,
          sellTokenQuant);
      newFirstTokenBalance = hardenedCalc
          ? StrictMathWrapper.subtractExact(firstTokenBalance, buyTokenQuant)
          : firstTokenBalance - buyTokenQuant;
      newSecondTokenBalance = hardenedCalc
          ? StrictMathWrapper.addExact(secondTokenBalance, sellTokenQuant)
          : secondTokenBalance + sellTokenQuant;

    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L41-45)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-99)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(addExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .addAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }

      accountStore.put(accountCapsule.createDbKey(), accountCapsule);

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
```
