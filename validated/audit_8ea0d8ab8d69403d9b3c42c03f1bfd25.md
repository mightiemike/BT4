## Title
Exchange liquidity injections lack minimum-received (slippage) protection, enabling front-running of `ExchangeInjectContract` to drain liquidity providers - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
`ExchangeInjectActuator` computes the counterpart token amount for a liquidity injection strictly from the *current on-chain* pool ratio at execution time, and `ExchangeInjectContract` provides no field to bound that computed amount (no minimum/maximum, no expected price). This mirrors the root cause of the reported Uniswap V3 issue — a price-dependent, liquidity-affecting operation that is executed without any protection against the price being manipulated immediately beforehand by another party — except here the exploitable window is the mempool gap before an `ExchangeInjectContract` transaction is confirmed, rather than the gap between `initialize` and `mint`.

### Finding Description
`ExchangeInjectActuator.execute` reads the exchange's current `firstTokenBalance`/`secondTokenBalance` and derives the required counterpart amount proportionally: [1](#0-0) 

The validation path performs the identical ratio computation using the pool's state at validation time and only checks that resulting balances stay under `getExchangeBalanceLimit()` and that the sender has enough balance — there is no bound comparing the computed `anotherTokenQuant` to any value supplied by the caller: [2](#0-1) 

The contract message itself has no field to express a minimum/maximum acceptable counterpart amount: [3](#0-2) 

Contrast this with `ExchangeTransactionActuator` (the swap actuator), which does defend against this exact class of price manipulation by requiring the caller to specify `expected` and reverting if the actual output is worse: [4](#0-3) [5](#0-4) 

Because `ExchangeInjectContract` lacks this same guard, an attacker can:
1. Observe a pending `ExchangeInjectContract` in the mempool from a liquidity provider (LP) who intends to inject `tokenQuant` of `firstTokenID` at the exchange's current ratio.
2. Front-run it with an `ExchangeTransactionContract` swap that sharply skews `firstTokenBalance`/`secondTokenBalance` (Tron's Bancor-style exchange has no per-block price limits beyond `getExchangeBalanceLimit()`).
3. Let the victim's injection execute — `ExchangeInjectActuator` will now compute `anotherTokenQuant` from the *manipulated* ratio, forcing the LP to pay a far larger (or smaller) amount of the counterpart token than intended at the fair market ratio.
4. Back-run with a reverse swap to restore the ratio and pocket the difference, which is effectively extracted from the LP's deposit.

This is directly analogous to the reported bug class: a price-setting/price-dependent operation (pool initialization in Uniswap, liquidity injection ratio in Tron) is executed without slippage/price protection and can be sandwiched by an unprivileged attacker who only needs to submit ordinary transactions (`ExchangeTransactionContract` calls) around the victim's transaction.

### Impact Explanation
An LP performing `ExchangeInjectContract` can be forced to deposit assets at an attacker-controlled unfair ratio, directly transferring value from the LP to the attacker — the same "draining of liquidity provider's deposits" impact described in the source report. This is a concrete accounting/underpriced-liquidity-injection impact affecting any unprivileged user who injects liquidity into a Tron Bancor-style exchange pool.

### Likelihood Explanation
Likelihood is limited by the requirement that the attacker (1) sees the pending injection transaction (feasible via mempool monitoring, as with any front-running attack) and (2) has enough balance/assets to swap against the pool to move its ratio meaningfully and to reverse it afterward. There is no special privilege needed — any account can submit `ExchangeTransactionContract` and `ExchangeInjectContract`. Given `ExchangeCreateActuator` sets the initial price atomically together with the deposit (creator supplies both balances in one transaction), the pure "front-run pool initialization" scenario from the original report does not apply to exchange creation, but it fully applies to subsequent liquidity injections via `ExchangeInjectContract`, which lack any of the protections present in the swap actuator.

### Recommendation
Add a `min_expected` (or `max_expected`) field to `ExchangeInjectContract`, and in `ExchangeInjectActuator.doValidate()` require the computed `anotherTokenQuant` to satisfy the caller-provided bound, mirroring the `expected` check already implemented in `ExchangeTransactionActuator`.

### Proof of Concept
1. Alice submits `ExchangeInjectContract{exchange_id=X, token_id=A, quant=100}` intending to inject at the current 1:1 ratio, expecting to also spend ~100 of token B.
2. Bob observes this in the mempool and submits `ExchangeTransactionContract` swapping a large amount of token A for B in exchange X, shifting the ratio to (e.g.) 1:10, and ensures it is included before Alice's transaction.
3. Alice's `ExchangeInjectContract` executes: `ExchangeInjectActuator.execute` (lines 71-83) computes `anotherTokenQuant` using the now-skewed balances, forcing Alice to contribute ~1000 of token B instead of ~100.
4. Bob submits a reverse `ExchangeTransactionContract` to restore the ratio, realizing a profit approximately equal to the excess token B Alice was forced to contribute.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-83)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L209-236)
```java
    BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
    BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
    BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
    long newTokenBalance;
    long newAnotherTokenBalance;

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

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** Tron protobuf protocol document.md (L1384-1401)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
