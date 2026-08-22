Based on my investigation, I found a strong analog to the reported TWAP bug class in java-tron's on-chain Exchange (Bancor-style AMM) module.

### Title
Instantaneous (spot) pool-balance pricing without TWAP protection in Exchange actuators allows same-block price manipulation - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
java-tron implements an on-chain token exchange (`ExchangeCreateContract`/`ExchangeInjectContract`/`ExchangeWithdrawContract`/`ExchangeTransactionContract`) where the "price" between two tokens is derived purely from the exchange's current, instantaneous `firstTokenBalance`/`secondTokenBalance` stored in `ExchangeCapsule` [1](#0-0) . This is structurally the same bug class as the reported `setPrice` issue: the "price oracle" (here, the pool ratio) is a single spot reading with no time-weighting/averaging, so any single transaction that shifts the pool ratio immediately and fully affects the price used by the very next dependent operation.

### Finding Description
`ExchangeTransactionActuator.execute`/`doValidate` calls `exchangeCapsule.transaction(...)`, which computes the counter-party amount using a Bancor-relay formula (`ExchangeProcessor`/`SafeExchangeProcessor`) driven solely by the exchange's current on-chain balances at execution time [2](#0-1) [3](#0-2) . Likewise, `ExchangeInjectActuator` and `ExchangeWithdrawActuator` compute the required "another token" amount as a simple ratio of the two current pool balances (`secondTokenBalance * tokenQuant / firstTokenBalance`, and vice versa) with no reference to any historical/averaged price [4](#0-3) [5](#0-4) .

Because all three actuators (Transaction, Inject, Withdraw) are ordinary, unprivileged broadcast transaction types that any account can submit against any exchange pair, an attacker can sequence multiple `ExchangeTransactionContract` trades against the same `ExchangeCapsule` within one block (or even packed into transactions the attacker controls the ordering of) to skew `firstTokenBalance`/`secondTokenBalance`, then immediately execute an `ExchangeInjectContract` or `ExchangeWithdrawContract` against the now-manipulated ratio, extracting value at a distorted rate before the pool reverts to its natural price. This mirrors the Entangle Protocol finding where `setPrice` used a static/instantaneous value instead of a TWAP, making it manipulable within a short window.

### Impact Explanation
An attacker with no special privileges can distort the exchange pool ratio and profit by injecting or withdrawing liquidity at a manipulated rate, or by chaining `ExchangeTransactionContract` calls to arbitrage against Inject/Withdraw operations that lack slippage protection tied to a time-averaged reference price (only `ExchangeTransactionContract` has a caller-supplied `tokenExpected` floor; Inject/Withdraw have none) [6](#0-5) . This causes accounting/value corruption for other exchange participants (asset/accounting corruption), directly reachable via ordinary broadcast transactions.

### Likelihood Explanation
High reachability: all three contract types are processed through the normal transaction/actuator pipeline available to any account, requiring only sufficient token/TRX balance to move the pool ratio, no admin or witness privileges, and no cross-node trust assumptions.

### Recommendation
Introduce a time-weighted or cumulative price mechanism for the Exchange pools (e.g., accumulate `balance1/balance2` ratio over time similar to Uniswap V2 `price0CumulativeLast`) and use that TWAP, rather than the instantaneous `firstTokenBalance`/`secondTokenBalance`, when computing amounts for `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, and add a caller-specified minimum/maximum bound (already present only for `ExchangeTransactionContract` via `tokenExpected`) to Inject/Withdraw to guard against within-block manipulation.

### Proof of Concept
1. Attacker submits an `ExchangeTransactionContract` that sells a large amount of `tokenID` into an exchange pool, shifting `firstTokenBalance`/`secondTokenBalance` sharply per `exchangeCapsule.transaction(...)` [3](#0-2) .
2. In the same or immediately following transaction, attacker (or a colluding victim-baiting flow) submits an `ExchangeInjectContract`/`ExchangeWithdrawContract` referencing the same `exchangeId`; the "another token" amount is computed from the now-skewed spot ratio [7](#0-6) .
3. Attacker reverses the initial trade (sell back) to restore the pool, having extracted value from the mispriced inject/withdraw step, with no TWAP or minimum-bound check preventing this in the Inject/Withdraw path.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L106-112)
```java
  public long getFirstTokenBalance() {
    return this.exchange.getFirstTokenBalance();
  }

  public long getSecondTokenBalance() {
    return this.exchange.getSecondTokenBalance();
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-89)
```java
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
