### Title
Missing Slippage Protection in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` (No `expected` Minimum/Maximum Guard) - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java, actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
`ExchangeInjectContract` and `ExchangeWithdrawContract` compute the counter-token amount (`anotherTokenQuant`) purely from the pool's current on-chain balances at execution time, with no field allowing the sender to bound the acceptable outcome. This is the same bug class as the referenced report: withdraw/deposit-style operations against an AMM pool with no `amountMin`/`amountMax` slippage guard, exposing the sender to unfavorable execution if the pool ratio shifts between signing and inclusion.

### Finding Description
`ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `owner_address`, `exchange_id`, `token_id`, and `quant` — unlike `ExchangeTransactionContract`, which additionally carries an `expected` field used as a minimum-received guard for trades. [1](#0-0) 

In `ExchangeInjectActuator.execute`, the `anotherTokenQuant` (the amount of the paired token the user must also deposit) is derived from the exchange pool's live `firstTokenBalance`/`secondTokenBalance` at execution time via `floorDiv(multiplyExact(...))`, with no comparison against any user-supplied bound: [2](#0-1) 

Similarly, `ExchangeWithdrawActuator.execute` computes `anotherTokenQuant` (the amount of the paired token returned to the user) from the live pool ratio, again with no user-specified minimum acceptable amount: [3](#0-2) 

The `doValidate()` methods for both actuators only check that `anotherTokenQuant` is positive, that the exchange has sufficient balance, and (in "hardened" mode) that the BigDecimal/BigInteger division is "precise enough" — none of these are slippage bounds set by the caller: [4](#0-3) 

Because Tron's transaction broadcast/inclusion is asynchronous (a signed transaction can sit in the mempool before being packed into a block, and multiple transactions touching the same exchange pool can be ordered arbitrarily within or across blocks), the pool ratio referenced by `firstTokenBalance`/`secondTokenBalance` can change between the time a user signs an `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction and the time it executes — e.g., another party broadcasting an `ExchangeTransactionContract` trade against the same `exchange_id` first. This is directly analogous to the reported Multipool `rebalanceAll` issue, where `_withdraw`/`_deposit` were called without `amount0Min`/`amount1Min`, even though the underlying primitive (`ExchangeTransactionContract.expected`, analogous to Uniswap's `amountOutMin`) already exists elsewhere in the same contract family and is simply omitted for inject/withdraw.

### Impact Explanation
A user injecting or withdrawing liquidity from a TRON Bancor-style `Exchange` pool has no way to bound the counter-token amount they will pay or receive. If the pool ratio is shifted (intentionally, via a preceding `ExchangeTransactionContract` trade broadcast by another party, or unintentionally due to normal trading activity) before the inject/withdraw transaction executes, the user can be forced to deposit more of the paired token than intended, or receive less than intended, resulting in a direct value loss to the pool creator without their consent.

### Likelihood Explanation
The pool creator is anonymous with respect to other network participants trading against the same `exchange_id`; any account can submit `ExchangeTransactionContract` transactions that shift `firstTokenBalance`/`secondTokenBalance` before a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` is packed. This requires no special privilege — only ordinary broadcast transactions — making the precondition straightforward to trigger, though it requires transaction-ordering influence (e.g. same block or targeted timing) to be reliably exploitable.

### Recommendation
Add optional minimum/maximum guard fields (analogous to `ExchangeTransactionContract.expected`) to `ExchangeInjectContract` and `ExchangeWithdrawContract`, e.g. `expected_another_token_min`/`expected_another_token_max`, and enforce them in `ExchangeInjectActuator`/`ExchangeWithdrawActuator`'s `execute`/`validate` so the computed `anotherTokenQuant` is checked against the caller-supplied bound before mutating balances.

### Proof of Concept
1. Pool creator broadcasts `ExchangeInjectContract` for exchange `X` intending to inject `tokenA` at the currently observed ratio, expecting to also deposit `N` of `tokenB`.
2. Before this transaction is included, another account broadcasts an `ExchangeTransactionContract` trade against exchange `X`, shifting `firstTokenBalance`/`secondTokenBalance`.
3. When the pool creator's `ExchangeInjectContract` executes, `ExchangeInjectActuator.execute` recomputes `anotherTokenQuant` from the now-shifted balances [5](#0-4) , causing the pool creator to deposit a different (larger) amount of `tokenB` than they intended when signing, with no failure or bound check preventing this.

### Citations

**File:** Tron protobuf protocol document.md (L1384-1442)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-99)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeInjectContract.getTokenId().toByteArray();
      long tokenQuant = exchangeInjectContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L63-89)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeWithdrawContract.getTokenId().toByteArray();
      long tokenQuant = exchangeWithdrawContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

      BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
      BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
      BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-243)
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
```
