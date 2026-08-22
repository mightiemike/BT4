Confirmed: `ExchangeInjectContract` and `ExchangeWithdrawContract` have no `expected`/minimum output field, unlike `ExchangeTransactionContract` which explicitly has `expected` for slippage control [1](#0-0) .

### Title
Missing slippage control in ExchangeInject/ExchangeWithdraw enables sandwich attacks on TRX/TRC10 liquidity pools - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
TRON's built-in bancor-style token exchange lets any account inject or withdraw liquidity from a first/second token pair pool. Unlike the trade function (`ExchangeTransactionContract`), which carries an `expected` field enforced at validation time, `ExchangeInjectContract` and `ExchangeWithdrawContract` carry no minimum/maximum output guard, so the counter-token amount computed at execution time from the then-current pool ratio can silently differ from what the user expected when they signed the transaction.

### Finding Description
`ExchangeInjectActuator.execute` computes the paired token amount to be pulled from the user's balance purely from the pool ratio present at execution time: `anotherTokenQuant = floorDiv(multiplyExact(secondTokenBalance, tokenQuant), firstTokenBalance)` (or the symmetric branch) [2](#0-1) . There is no field in `ExchangeInjectContract` letting the signer cap how much of the paired token they're willing to give up, and `doValidate()` never checks the computed `anotherTokenQuant` against any user-supplied bound [3](#0-2) .

Symmetrically, `ExchangeWithdrawActuator.execute` computes the paired token the user receives from the current ratio (`anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant).divide(bigFirstTokenBalance)...`) with no minimum-received check anywhere in `doValidate()` [4](#0-3) [5](#0-4) .

Contrast this with `ExchangeTransactionActuator`, which the same codebase equips with an explicit `expected` parameter and validates `anotherTokenQuant < tokenExpected` before executing the trade [6](#0-5) . The designers clearly recognized the need for slippage protection for `ExchangeTransaction`, but this protection was never extended to `ExchangeInject`/`ExchangeWithdraw`, even though both operations are equally exposed to pool-ratio manipulation between transaction broadcast and block inclusion.

An attacker monitoring the mempool for a broadcast `ExchangeInjectContract` or `ExchangeWithdrawContract` transaction can:
1. Front-run with an `ExchangeTransactionContract` trade that skews `firstTokenBalance`/`secondTokenBalance` in their favor.
2. Let the victim's inject/withdraw execute at the manipulated ratio — forcing them to inject far more of the paired token than intended, or receive far less back on withdrawal.
3. Back-run with a reverse trade to restore the ratio and capture the difference as extracted value, exactly the sandwich pattern described in the referenced Frankencoin `Equity` FPS mint/redeem report.

### Impact Explanation
Any account using the TRON on-chain Exchange feature to add or remove liquidity is exposed to value extraction via sandwich attacks, with no way to protect themselves since the contract offers no slippage parameter for these two operations. This is an accounting/asset-loss issue reachable by any anonymous account broadcasting a standard transaction type, not requiring any privileged access.

### Likelihood Explanation
The `Exchange*` contract types are legacy but still valid, broadcastable transaction types accepted by any full node and processed by `ExchangeInjectActuator`/`ExchangeWithdrawActuator` without any special permission [7](#0-6) . Exploitation requires only mempool visibility and the ability to submit ordinary transactions ahead of/after the victim's, which is standard MEV/front-running capability on any public chain; no special privilege or vulnerability elsewhere is needed. Actual profitability depends on pool liquidity/depth and the presence of active exchange pairs with nontrivial balances.

### Recommendation
Add explicit slippage-bound fields to `ExchangeInjectContract` and `ExchangeWithdrawContract` (e.g. `expected_another_token_min`/`max`), and enforce them in `ExchangeInjectActuator.doValidate()`/`execute()` and `ExchangeWithdrawActuator.doValidate()`/`execute()`, mirroring the `expected` check already present in `ExchangeTransactionActuator.doValidate()` [6](#0-5) .

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` from victim specifying `token_id = firstTokenID`, `quant = Q`.
2. Attacker submits and gets included first an `ExchangeTransactionContract` that sells a large amount of `firstTokenID` into the pool, shifting `secondTokenBalance/firstTokenBalance` ratio down.
3. Victim's inject executes: `anotherTokenQuant = secondTokenBalance * Q / firstTokenBalance` [8](#0-7)  now computes a smaller `anotherTokenQuant` than the victim intended when signing, or, depending on direction of manipulation, a larger amount than they wanted to spend — with no `expected`/minimum field to reject the transaction.
4. Attacker submits a reverse `ExchangeTransactionContract` to restore the ratio and pocket the extracted value, analogous to the Bob/Alice FPS scenario in the referenced report.

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L17-37)
```text
message ExchangeInjectContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}

message ExchangeWithdrawContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}

message ExchangeTransactionContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
  int64 expected = 5;
}
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L38-59)
```java
  @Override
  public boolean execute(Object object) throws ContractExeException {
    TransactionResultCapsule ret = (TransactionResultCapsule) object;
    if (Objects.isNull(ret)) {
      throw new RuntimeException(ActuatorConstant.TX_RESULT_NULL);
    }

    long fee = calcFee();
    AccountStore accountStore = chainBaseManager.getAccountStore();
    DynamicPropertiesStore dynamicStore = chainBaseManager.getDynamicPropertiesStore();
    ExchangeStore exchangeStore = chainBaseManager.getExchangeStore();
    ExchangeV2Store exchangeV2Store = chainBaseManager.getExchangeV2Store();
    AssetIssueStore assetIssueStore = chainBaseManager.getAssetIssueStore();
    try {
      final ExchangeInjectContract exchangeInjectContract = this.any
          .unpack(ExchangeInjectContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeInjectContract.getOwnerAddress().toByteArray());

      ExchangeCapsule exchangeCapsule;
      exchangeCapsule = Commons.getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeInjectContract.getExchangeId()));
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-83)
```java
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            firstTokenBalance, tokenQuant), secondTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, anotherTokenQuant),
            addExact(secondTokenBalance, tokenQuant));
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-231)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenID = secondTokenID;
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divide(bigFirstTokenBalance).longValueExact();
      newTokenBalance = addExact(firstTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(secondTokenBalance, anotherTokenQuant);
    } else {
      anotherTokenID = firstTokenID;
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divide(bigSecondTokenBalance).longValueExact();
      newTokenBalance = addExact(secondTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(firstTokenBalance, anotherTokenQuant);
    }

    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L77-89)
```java
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, tokenQuant),
            subtractExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, anotherTokenQuant),
            subtractExact(secondTokenBalance, tokenQuant));
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L205-253)
```java
    if (tokenQuant <= 0) {
      throw new ContractValidateException("withdraw token quant must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    BigDecimal bigFirstTokenBalance = new BigDecimal(String.valueOf(firstTokenBalance));
    BigDecimal bigSecondTokenBalance = new BigDecimal(String.valueOf(secondTokenBalance));
    BigDecimal bigTokenQuant = new BigDecimal(String.valueOf(tokenQuant));
    final boolean allowHarden = allowHarden();
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
      }
      if (allowHarden) {
        BigDecimal remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, RoundingMode.HALF_UP)
            .subtract(BigDecimal.valueOf(anotherTokenQuant));
        if (remainder.compareTo(
            BigDecimal.valueOf(anotherTokenQuant).multiply(new BigDecimal("0.0001"))) > 0) {
          throw new ContractValidateException("Not precise enough");
        }
      } else {
        double remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, BigDecimal.ROUND_HALF_UP).doubleValue()
            - anotherTokenQuant;
        if (remainder / anotherTokenQuant > 0.0001) {
          throw new ContractValidateException("Not precise enough");
        }
      }

    } else {
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigSecondTokenBalance).longValueExact();
      if (secondTokenBalance < tokenQuant || firstTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
