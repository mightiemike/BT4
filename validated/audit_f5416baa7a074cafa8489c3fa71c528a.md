## Title
`ExchangeWithdrawContract` Lacks Slippage/Expected-Amount Protection - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java])

### Summary
`ExchangeWithdrawActuator` computes the amount of the paired token (`anotherTokenQuant`) a liquidity-providing creator receives when withdrawing from a bancor-style TRX exchange pool, based solely on the pool's *current* token ratio at execution time. Unlike its sibling `ExchangeTransactionActuator`, which lets the caller supply an `expected` minimum output that is enforced before execution, `ExchangeWithdrawContract` provides no such parameter, so the actual output amount can silently diverge from what the creator expected when they signed the transaction — the exact bug class described in the external report for `Escrow.redeem`.

### Finding Description
In `ExchangeWithdrawActuator.doValidate()` and `execute()`, `anotherTokenQuant` is derived purely from the exchange pool's `firstTokenBalance`/`secondTokenBalance` ratio at the moment the transaction is processed: [1](#0-0) 

This is only checked for "precision" (rounding-error) reasons, not for a caller-specified minimum acceptable amount: [2](#0-1) 

The protobuf contract itself has no `expected` field, in contrast to `ExchangeTransactionContract`, which does carry an `expected` field: [3](#0-2) [4](#0-3) 

For comparison, `ExchangeTransactionActuator` explicitly validates the caller-supplied minimum before allowing execution — this is the slippage protection pattern the external report recommends adding: [5](#0-4) 

Because the pool's `firstTokenBalance`/`secondTokenBalance` state can be mutated by other transactions (`ExchangeTransactionContract`, `ExchangeInjectContract`) that are ordered/executed earlier within the same block or between the time the withdraw transaction is signed/broadcast and the time it is actually executed by the block-producing witness, the ratio used to compute `anotherTokenQuant` for the withdraw is not guaranteed to match the ratio the creator observed when constructing the transaction.

### Impact Explanation
An exchange creator withdrawing liquidity via `ExchangeWithdrawContract` can receive a different (lower) amount of the paired token than they expected, with no on-chain mechanism to revert if the received amount falls below an acceptable threshold. This is an accounting/settlement-divergence impact: the transaction succeeds and permanently consumes the creator's LP position at a price that was not verified against the creator's expectations, mirroring the "redeem" slippage issue in the report (users get less value than intended, with no ability to specify or enforce a minimum).

### Likelihood Explanation
This is reachable by any account that is the `creatorAddress` of an exchange pool calling the standard `ExchangeWithdrawContract` API — no privileged role is required. The window for ratio manipulation exists naturally any time other trades against the same exchange pool are processed before the withdraw within the same block (transaction ordering is determined by the block producer / mempool, not the withdrawing user), making the divergence plausible under normal usage and trivially exploitable by front-running with an `ExchangeTransactionContract` trade immediately preceding the withdraw.

### Recommendation
Add an `expected` (or `min_another_token_quant` / min-of-both-sides) field to `ExchangeWithdrawContract`, and in `ExchangeWithdrawActuator.doValidate()`/`execute()` enforce that the computed `anotherTokenQuant` (and/or `tokenQuant` returned) is not less than the caller-specified minimum, reverting with a `ContractValidateException`/`ContractExeException` otherwise — following the exact pattern already implemented in `ExchangeTransactionActuator` at lines 217-221.

### Proof of Concept
1. Creator holds an exchange pool with `firstTokenBalance = 100_000_000`, `secondTokenBalance = 200_000_000`, and prepares an `ExchangeWithdrawContract` withdrawing `10_000_000` of the first token, expecting to receive `~20_000_000` of the second token based on the currently observed ratio.
2. Before the withdraw transaction is packed into a block, another party submits an `ExchangeTransactionContract` trade against the same pool that shifts the ratio substantially (e.g., buys a large amount of the second token), which is processed first within the same block via `ExchangeTransactionActuator.execute()` ( [6](#0-5) ).
3. The withdraw is then executed against the now-skewed pool state in `ExchangeWithdrawActuator.execute()` ( [7](#0-6) ), producing an `anotherTokenQuant` far lower than the creator anticipated, with the transaction still succeeding (`code.SUCESS`) since no minimum-expected check exists to cause a revert.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-227)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L228-243)
```java
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

**File:** Tron protobuf protocol document.md (L1403-1420)
```markdown
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-69)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
