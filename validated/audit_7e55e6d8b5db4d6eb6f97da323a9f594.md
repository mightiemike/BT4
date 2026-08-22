### Title
Missing slippage/bound parameter in `ExchangeInjectContract` and `ExchangeWithdrawContract` allows front-running of TRC10 Bancor-style exchange operations - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java, actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
`ExchangeInjectContract` and `ExchangeWithdrawContract` (the liquidity add/remove operations for java-tron's built-in TRC10 Bancor-style exchange) compute the paired-token amount (`anotherTokenQuant`) purely from the exchange's *current* on-chain reserve ratio at execution time, with no user-supplied bound/slippage parameter to protect against ratio manipulation. This is in contrast to `ExchangeTransactionContract`, which does carry an `expected` minimum-output field precisely to prevent this class of issue.

### Finding Description
`ExchangeInjectActuator.execute()`/`doValidate()` reads the exchange's `firstTokenBalance`/`secondTokenBalance` and derives `anotherTokenQuant` as a direct ratio of the injected `tokenQuant` against the live reserves: [1](#0-0) 
The same pattern applies in `ExchangeWithdrawActuator.doValidate()`, where `anotherTokenQuant` (the amount of the paired token returned to the creator) is likewise computed strictly from the current reserve ratio with no user-specified minimum: [2](#0-1) 

Neither the `ExchangeInjectContract` nor `ExchangeWithdrawContract` protobuf messages carry any bound field — only `owner_address`, `exchange_id`, `token_id`, and `quant`: [3](#0-2) 
By contrast, `ExchangeTransactionContract` explicitly includes an `expected` field ("expected minimum number of tokens") specifically to defend the trade side of the exchange against this exact bug class: [4](#0-3) 
and `ExchangeTransactionActuator` enforces it via `exchangeCapsule.transaction(...)`, throwing `"token required must greater than expected"` when the trade output would be worse than the caller's bound — confirmed by test cases: [5](#0-4) 

Because `ExchangeInject`/`ExchangeWithdraw` have no analogous bound, the reserve ratio they read is whatever it happens to be at the moment their transaction executes on-chain — which any third party can manipulate beforehand by broadcasting an `ExchangeTransactionContract` trade against the same `exchange_id` (trading requires no special permission, only that the exchange exists and has reserves). A pending inject/withdraw transaction sitting in the mempool can therefore be front-run: an attacker observes the victim's transaction, submits a trade that shifts `firstTokenBalance`/`secondTokenBalance` in their favor, lets it be mined first, and the victim's inject/withdraw then executes against the post-trade (manipulated) ratio.

### Impact Explanation
For `ExchangeInjectContract`, a manipulated ratio forces the injecting account to hand over a different (attacker-favorable) amount of the paired token than they intended when signing, without any way to cap or reject the outcome — validate() only checks `anotherTokenQuant > 0` and sufficient balance, not that it matches the signer's expectations: [6](#0-5) 
For `ExchangeWithdrawContract`, the exchange creator withdrawing liquidity can be made to receive less of the paired token than the pre-manipulation ratio would have given, again with no minimum-output floor enforced beyond the "greater than zero" and balance-sufficiency checks: [7](#0-6) 
This results in direct asset-value loss for the account performing inject/withdraw — analogous to the referenced `mintForToken()` finding where a missing `minAmountOut` allowed value extraction via reserve manipulation.

### Likelihood Explanation
Any account can create an exchange pair (via `ExchangeCreateContract`) and become its "creator" (an ordinary account role, not a privileged node/validator), then later call `ExchangeInject`/`ExchangeWithdraw` on it. Any other anonymous account can broadcast an `ExchangeTransactionContract` trade against that same `exchange_id` at will — trading is unrestricted and reachable from any broadcast transaction via the exposed RPC (`rpc ExchangeTransaction`): [8](#0-7) 
Given mempool visibility of pending transactions (standard for all chains including TRON), front-running the creator's inject/withdraw transaction with a trade is straightforward and requires no privileged access.

### Recommendation
Add a bound parameter to `ExchangeInjectContract` and `ExchangeWithdrawContract` (e.g., `expected`/`minAnotherTokenQuant` for inject and `expected`/`minAnotherTokenQuant` for withdraw, mirroring `ExchangeTransactionContract.expected`), and enforce it in `ExchangeInjectActuator.doValidate()`/`ExchangeWithdrawActuator.doValidate()` by rejecting the transaction if the computed `anotherTokenQuant` falls outside the caller-supplied bound, the same way `ExchangeTransactionActuator` already does for trades.

### Proof of Concept
1. Attacker monitors the mempool for a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) transaction from the exchange creator for `exchange_id = X`.
2. Attacker broadcasts an `ExchangeTransactionContract` trade against `exchange_id = X` that shifts `firstTokenBalance`/`secondTokenBalance` favorably (no `expected` protection needed on attacker's own trade, or attacker sets a loose one).
3. Attacker's trade is included first (via higher fee/priority or natural ordering), changing the reserve ratio.
4. Victim's `ExchangeInjectContract`/`ExchangeWithdrawContract` then executes: `ExchangeInjectActuator`/`ExchangeWithdrawActuator` recompute `anotherTokenQuant` from the now-manipulated ratio, with no way for the victim's originally-signed transaction to reject an unfavorable outcome, as shown by the unconstrained ratio math in [1](#0-0)  and [2](#0-1) .

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-227)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L229-256)
```java
    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }

    if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(anotherTokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(anotherTokenID, anotherTokenQuant, dynamicStore)) {
        throw new ContractValidateException("another token balance is not enough");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-227)
```java
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L245-254)
```java
    } else {
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigSecondTokenBalance).longValueExact();
      if (secondTokenBalance < tokenQuant || firstTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
      }
```

**File:** Tron protobuf protocol document.md (L1384-1420)
```markdown
     - message `ExchangeInjectContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to inject.
    
       `quant`: token amount to inject.
    
      ```java
      message ExchangeInjectContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
    
     - message `ExchangeWithdrawContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to withdraw.
    
       `quant`: token amount to withdraw.
    
      ```java
      message ExchangeWithdrawContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
```

**File:** Tron protobuf protocol document.md (L1422-1442)
```markdown
     - message `ExchangeTransactionContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to sell.
    
       `quant`: token amount to sell.
    
       `expected`: expected minimum number of tokens.
    
      ```java
      message ExchangeTransactionContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
          int64 expected = 5;
      }
      ```
```

**File:** framework/src/test/java/org/tron/core/actuator/ExchangeTransactionActuatorTest.java (L1535-1557)
```java
    long expected = 0;
    try {
      ExchangeCapsule exchangeCapsule = dbManager.getExchangeStore()
          .get(ByteArray.fromLong(exchangeId));
      expected = exchangeCapsule.transaction(tokenId.getBytes(), quant, useStrictMath);
    } catch (ItemNotFoundException | ContractValidateException e) {
      fail();
    }

    ExchangeTransactionActuator actuator = new ExchangeTransactionActuator();
    actuator.setChainBaseManager(dbManager.getChainBaseManager()).setAny(getContract(
        OWNER_ADDRESS_SECOND, exchangeId, tokenId, quant, expected + 1));

    TransactionResultCapsule ret = new TransactionResultCapsule();

    try {
      actuator.validate();
      actuator.execute(ret);
      fail("should not run here");
    } catch (ContractValidateException e) {
      Assert.assertTrue(e instanceof ContractValidateException);
      Assert.assertEquals("token required must greater than expected",
          e.getMessage());
```

**File:** protocol/src/main/protos/api/api.proto (L184-188)
```text
  rpc ExchangeWithdraw (ExchangeWithdrawContract) returns (TransactionExtention) {
  }

  rpc ExchangeTransaction (ExchangeTransactionContract) returns (TransactionExtention) {
  }
```
