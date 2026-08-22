### Title
Missing slippage control in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` allows front-running of liquidity operations - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
The TRC10 "Exchange" (AMM-style bancor-formula market) implementation provides slippage protection only for `ExchangeTransactionContract` (swap), via the `expected` field checked in `ExchangeTransactionActuator.doValidate()`. The two other market-mutating operations — inject liquidity (`ExchangeInjectContract`) and withdraw liquidity (`ExchangeWithdrawContract`) — compute the counter-token amount purely from the *current* pool ratio at execution time and provide no caller-supplied minimum/maximum bound, so a user broadcasting an inject/withdraw transaction has no way to bound how much of the paired asset they will actually pay or receive.

### Finding Description
`ExchangeTransactionContract` explicitly carries an `expected` field used as a slippage floor: [1](#0-0) 

`ExchangeTransactionActuator.doValidate()` enforces that floor before execution: [2](#0-1) 

In contrast, `ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `exchange_id`, `token_id`, and `quant` — there is no `expected`/minimum field for the counter-token amount: [3](#0-2) [4](#0-3) 

`ExchangeInjectActuator.execute()` computes `anotherTokenQuant` (the amount of the paired token the user must pay to inject liquidity) directly from `firstTokenBalance`/`secondTokenBalance` read from storage at execution time, then unconditionally deducts it from the user's account: [5](#0-4) 

`ExchangeInjectActuator.doValidate()` similarly recomputes `anotherTokenQuant` from the live pool balances with no user-supplied bound — the only checks are that it's `> 0` and under the balance limit: [6](#0-5) 

The same pattern exists in `ExchangeWithdrawActuator`: `anotherTokenQuant` (the amount of the paired token returned to the withdrawing user) is derived from live pool balances with no minimum-received check requested by the caller — validation only ensures a "precision" tolerance (0.01%) against the *actuator's own* recomputation, not against any user-specified floor: [7](#0-6) [8](#0-7) 

Both actuators call `ExchangeCapsule.transaction()`/pool-ratio math against the mutable `firstTokenBalance`/`secondTokenBalance`, which any account can move via `ExchangeTransactionContract` (a normal, unprivileged swap) executed in an earlier position within the same block or an earlier block: [9](#0-8) 

Because Java-Tron block producers (SRs) select and order the pending transactions that go into a block, and multiple pending transactions targeting the same `exchange_id` can be packed into one block in producer-chosen order, an adversary (acting as, or colluding with within, the transaction-ordering path — not necessarily a privileged SR) can submit a swap transaction against the same exchange immediately before a victim's pending inject/withdraw transaction, shift the ratio unfavorably, let the victim's inject/withdraw execute at the manipulated ratio, then submit a reverse swap to restore the ratio and pocket the difference — a classic sandwich attack. Since neither `ExchangeInjectContract` nor `ExchangeWithdrawContract` exposes any way for the caller to bound the resulting `anotherTokenQuant`, there is no way for the victim (or any wallet/dApp integrating with the exchange API) to protect against this at the protocol level.

### Impact Explanation
A victim injecting liquidity can be forced to pay significantly more of the paired asset than intended, or a victim withdrawing liquidity can receive significantly less of the paired asset than intended, with the difference captured by the attacker via a sandwiching swap. This is a direct, unprivileged loss-of-funds vector reachable by any account broadcasting ordinary `ExchangeInjectContract`/`ExchangeWithdrawContract` transactions, mirroring exactly the missing-slippage-control bug class described in the external report (there, mint/burn-equivalent operations lacked a slippage floor while swap had one).

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to observe a pending inject/withdraw transaction for a specific `exchange_id` and get a swap transaction (plus a reversing swap) ordered around it within the same or adjacent blocks — feasible for anyone monitoring the pending-transaction pool, and trivially so for a block-producing node ordering its own block, without requiring any privileged key compromise, since `ExchangeTransactionContract`, `ExchangeInjectContract`, and `ExchangeWithdrawContract` are all ordinary, unprivileged, anonymously-broadcastable transaction types.

### Recommendation
Add an explicit slippage-bound field (e.g., `expected_another_token_quant` with min/max semantics analogous to `expected` in `ExchangeTransactionContract`) to `ExchangeInjectContract` and `ExchangeWithdrawContract`, and enforce it in `ExchangeInjectActuator.doValidate()`/`execute()` and `ExchangeWithdrawActuator.doValidate()`/`execute()` before mutating balances, so that a caller-specified bound on the paired-token amount is validated against the pool state at execution time, causing the transaction to fail rather than execute at an attacker-manipulated ratio.

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` transaction from victim V targeting `exchange_id=E`, injecting `quant` of `firstTokenID`.
2. Attacker submits an `ExchangeTransactionContract` swap against `E` that shifts `firstTokenBalance`/`secondTokenBalance` unfavorably for V (e.g., sells a large amount of `secondTokenID` into the pool), landing in the block before V's inject transaction — this is only bounded by ordinary swap validation in `ExchangeTransactionActuator.doValidate()`, which has no relationship to V's pending inject.
3. V's `ExchangeInjectContract` executes via `ExchangeInjectActuator.execute()`; `anotherTokenQuant` is recomputed from the now-skewed `firstTokenBalance`/`secondTokenBalance` (see `ExchangeInjectActuator.java:71-83`), forcing V to pay more `secondTokenID` than the ratio at broadcast time implied — with no `expected`/minimum check to abort the transaction.
4. Attacker submits a reversing swap to restore the pool ratio, capturing the value difference extracted from V's inject.
5. The identical sequence applies to `ExchangeWithdrawContract`/`ExchangeWithdrawActuator`, causing V to receive less of the paired token than expected on withdrawal.

### Citations

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

**File:** Tron protobuf protocol document.md (L1422-1440)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-246)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-120)
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());

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

      Commons.putExchangeCapsule(exchangeCapsule, dynamicStore, exchangeStore, exchangeV2Store,
          assetIssueStore);

      ret.setExchangeWithdrawAnotherAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
    } catch (ItemNotFoundException | InvalidProtocolBufferException
        | ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }
    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-244)
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-168)
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

    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
    this.exchange = this.exchange.toBuilder()
        .setFirstTokenBalance(newFirstTokenBalance)
        .setSecondTokenBalance(newSecondTokenBalance)
        .build();

    return buyTokenQuant;
```
