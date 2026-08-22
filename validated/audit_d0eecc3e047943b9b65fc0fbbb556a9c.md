### Title
Missing sanity check on `ExchangeCreateContract` token-balance ratio allows anyone to create a permanently non-tradable exchange pool - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java`)

### Summary
`ExchangeCreateActuator.doValidate()` only rejects `firstTokenBalance <= 0` or `secondTokenBalance <= 0` and enforces an upper `balanceLimit`, but performs no check on the *ratio* between `firstTokenBalance` and `secondTokenBalance` relative to the fixed internal `supply` constant (`1_000_000_000_000_000_000L`) used by the bancor-style curve in `ExchangeProcessor`/`SafeExchangeProcessor`. This is directly analogous to the NibblVault finding: an unprivileged, anonymous actor can pick initial pool parameters that make the core math round down to zero, permanently breaking the trading function for that pool.

### Finding Description
Any account can broadcast an `ExchangeCreateContract` to create a new token-pair exchange pool via `ExchangeCreateActuator`. Validation only checks:
- both balances are `> 0` [1](#0-0) 
- both balances are `<= exchangeBalanceLimit` [2](#0-1) 
- the account has enough balance/asset to fund them [3](#0-2) 

There is no check that the two balances are within a sane ratio of each other, nor relative to the hard-coded `supply` used by the bonding-curve math: [4](#0-3) 

The actual trade math is computed by `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` (and the `SafeExchangeProcessor` BigDecimal variant), both of which are driven by the fixed `SUPPLY = 1_000_000_000_000_000_000L` constant regardless of the pool's real token balances: [5](#0-4) [6](#0-5) 

If `firstTokenBalance` and `secondTokenBalance` are chosen with an extreme mismatch (e.g. one side minimal like `1` and the other side very large, or vice versa) relative to the fixed `1e18` supply constant, `exchangeToSupply`/`exchangeFromSupply` round the computed "relay" and resulting token quantity down to `0` for any trade size a normal user would attempt. `ExchangeTransactionActuator.doValidate()` then always rejects trades because the computed `anotherTokenQuant` is `0`, which is never `>= tokenExpected` (which must be `> 0`): [7](#0-6) 

This mirrors the NibblVault bug precisely: absence of a sanity check on the initial pool parameters (`_initialTokenSupply`/`_initialTokenPrice` there, `firstTokenBalance`/`secondTokenBalance` here) lets a rounding-to-zero condition in the pricing formula make the core `buy`/`sell` (here, `ExchangeTransactionContract`) functionality permanently unusable for that specific pool.

### Impact Explanation
This is a self-inflicted, per-pool denial of service: any account with any two tokens (including TRX) can broadcast an `ExchangeCreateContract` with a skewed ratio and create a pool that is functionally "dead" — no future `ExchangeTransactionContract` can ever succeed against it because the bancor curve always yields `0` output. This wastes/locks the funds deposited at creation (they can only be reclaimed by the creator via `ExchangeWithdrawContract`, not through trading), and pollutes on-chain state with unusable exchange pools. It does not directly threaten funds of other users or validators, and does not corrupt consensus state beyond the affected pool, so severity is limited to a localized, self-inflicted resource/accounting DoS rather than node RCE or global fund loss.

### Likelihood Explanation
High likelihood of accidental triggering: a user unaware of the internal `1e18` fixed-point supply constant used by `ExchangeProcessor` can easily pick balances (e.g. very small `1`-unit tokens versus a token with few decimals) that produce this broken state, exactly as described in the original report ("many users interact with contracts entering wrong values because they are not aware they needed to include decimals"). No special privileges, timing, or race conditions are required — a single `ExchangeCreateContract` transaction from any unprivileged account is sufficient.

### Recommendation
Add a sanity check in `ExchangeCreateActuator.doValidate()` (and mirror it in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` where new balances are computed) that:
- Rejects balances whose ratio to the fixed `supply` constant (`1_000_000_000_000_000_000L`) would make a minimum meaningful trade quantity round to `0` in `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply`.
- Optionally, simulate a small representative trade against the proposed `firstTokenBalance`/`secondTokenBalance` at creation time and reject creation if the result is `0`, similarly to how `ExchangeInjectActuator` already validates `anotherTokenQuant <= 0` before executing.

### Proof of Concept
1. Any account calls `ExchangeCreateContract` with `firstTokenId = TRX`, `firstTokenBalance = 1`, `secondTokenId = <asset>`, `secondTokenBalance = 100_000_000_000000` (near the `exchangeBalanceLimit`). `ExchangeCreateActuator.doValidate()` only checks both are `>0` and `<= balanceLimit`, so this passes.
2. `execute()` creates the pool with these balances via `ExchangeCapsule.setBalance` [8](#0-7) .
3. Any subsequent `ExchangeTransactionContract` trading a realistic (small) `tokenQuant` against this pool causes `ExchangeCapsule.transaction()` → `ExchangeProcessor.exchange()` to compute `anotherTokenQuant = 0` due to the extreme mismatch versus the fixed `1e18` supply constant.
4. `ExchangeTransactionActuator.doValidate()` then always throws `"token required must greater than expected"` (since `tokenExpected` must be `>0` per line 190-192) [9](#0-8) , meaning the pool can never process a trade — the deposited tokens sit idle and can only be retrieved by the creator via a withdraw, never traded.

Note: I could not fully verify the exact numeric threshold at which `exchangeToSupply`/`exchangeFromSupply` round to `0` for specific balance/quant combinations (this depends on floating point/`BigDecimal` precision behavior across the whole input space), since that would require running the arithmetic rather than static analysis. The finding is grounded in the code paths shown, but a precise minimal reproducing numeric example was not computed/executed.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L90-115)
```java
        exchangeCapsule.setBalance(firstTokenBalance, secondTokenBalance);
        exchangeStore.put(exchangeCapsule.createDbKey(), exchangeCapsule);

        //save to new asset store
        if (!Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
          String firstTokenRealID = assetIssueStore.get(firstTokenID).getId();
          firstTokenID = firstTokenRealID.getBytes();
        }
        if (!Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
          String secondTokenRealID = assetIssueStore.get(secondTokenID).getId();
          secondTokenID = secondTokenRealID.getBytes();
        }
      }

      {
        // only save to new asset store
        ExchangeCapsule exchangeCapsuleV2 =
            new ExchangeCapsule(
                exchangeCreateContract.getOwnerAddress(),
                id,
                now,
                firstTokenID,
                secondTokenID
            );
        exchangeCapsuleV2.setBalance(firstTokenBalance, secondTokenBalance);
        exchangeV2Store.put(exchangeCapsuleV2.createDbKey(), exchangeCapsuleV2);
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L201-203)
```java
    if (firstTokenBalance <= 0 || secondTokenBalance <= 0) {
      throw new ContractValidateException("token balance must greater than zero");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L205-208)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L210-228)
```java
    if (Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(firstTokenBalance, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(firstTokenID, firstTokenBalance, dynamicStore)) {
        throw new ContractValidateException("first token balance is not enough");
      }
    }

    if (Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(secondTokenBalance, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(secondTokenID, secondTokenBalance, dynamicStore)) {
        throw new ContractValidateException("second token balance is not enough");
      }
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-129)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-45)
```java
  private long exchangeToSupply(long balance, long quant) {
    logger.debug("balance: " + balance);
    long newBalance = balance + quant;
    logger.debug("balance + quant: " + newBalance);

    double issuedSupply = -supply * (1.0
        - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, this.useStrictMath));
    logger.debug("issuedSupply: " + issuedSupply);
    long out = (long) issuedSupply;
    supply += out;

    return out;
  }

  private long exchangeFromSupply(long balance, long supplyQuant) {
    supply -= supplyQuant;

    double exchangeBalance = balance
        * (Maths.pow(1.0 + (double) supplyQuant / supply, 2000.0, this.useStrictMath) - 1.0);
    logger.debug("exchangeBalance: " + exchangeBalance);

    return (long) exchangeBalance;
  }

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L19-44)
```java
  private BigDecimal exchangeToSupply(long balance, long quant) {
    long newBalance = StrictMathWrapper.addExact(balance, quant);
    BigDecimal bdQuant = BigDecimal.valueOf(quant);
    BigDecimal bdNewBalance = BigDecimal.valueOf(newBalance);
    BigDecimal base = BigDecimal.ONE.add(
        bdQuant.divide(bdNewBalance, 18, RoundingMode.HALF_UP));
    double powResult = StrictMathWrapper.pow(base.doubleValue(), 0.0005);
    return SUPPLY.negate().multiply(
        BigDecimal.ONE.subtract(BigDecimal.valueOf(powResult))).setScale(0, RoundingMode.DOWN);
  }

  private long exchangeFromSupply(long balance, BigDecimal supplyQuant) {
    BigDecimal bdBalance = BigDecimal.valueOf(balance);
    BigDecimal base = BigDecimal.ONE.add(
        supplyQuant.divide(SUPPLY, 18, RoundingMode.HALF_UP));
    double powResult = StrictMathWrapper.pow(base.doubleValue(), 2000.0);
    BigDecimal exchangeBalance = bdBalance.multiply(
        BigDecimal.valueOf(powResult).subtract(BigDecimal.ONE));
    return exchangeBalance.setScale(0, RoundingMode.DOWN).longValueExact();
  }

  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    BigDecimal relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L186-221)
```java
    if (tokenQuant <= 0) {
      throw new ContractValidateException("token quant must greater than zero");
    }

    if (tokenExpected <= 0) {
      throw new ContractValidateException("token expected must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
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

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
