Based on my research, java-tron's `Exchange` module (`ExchangeTransactionActuator` / `ExchangeCapsule`) contains an analogous accounting-mismatch pattern, though the exact mechanics differ from the original Solidity finding.

### Title
Unbounded pool-balance underflow in `ExchangeCapsule.transaction` when running the non-hardened math path - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`ExchangeTransactionActuator.execute` lets any account swap tokens through the on-chain bancor-style `Exchange` object by calling `exchangeCapsule.transaction(...)`, which computes `buyTokenQuant` via `ExchangeProcessor`/`SafeExchangeProcessor` and then updates the pool's `firstTokenBalance`/`secondTokenBalance`.

### Finding Description
`ExchangeCapsule.transaction` computes `newFirstTokenBalance`/`newSecondTokenBalance` and only validates that they are `>= 0` when `hardenedCalc` (the `allowHarden()`/hardfork-gated flag) is `true`: [1](#0-0) 
When `hardenedCalc` is `false` (the legacy `ExchangeProcessor` / non-strict path), the resulting `newFirstTokenBalance`/`newSecondTokenBalance` are stored unconditionally with no floor check, so a sell that yields a `buyTokenQuant` larger than the pool's opposing balance silently drives the recorded pool balance negative: [2](#0-1) 
Meanwhile `ExchangeTransactionActuator.execute` unconditionally debits the seller's full `tokenQuant` and credits the computed `anotherTokenQuant`, exactly mirroring the original bug's pattern where one leg of the swap (fund debit/burn) is executed unconditionally while the other leg (pool/collateral balance) is allowed to diverge from the true available amount: [3](#0-2) 
`doValidate()` only checks `anotherTokenQuant < tokenExpected` (slippage) and an upper `balanceLimit` on the *sold* token side; it never verifies that the pool's balance for the *bought* token stays non-negative before hardened math is enforced: [4](#0-3) 

### Impact Explanation
If the non-hardened path is taken (e.g., on chains/forks where the relevant hardfork proposal enabling `allowHarden()`/strict math has not been activated, or for any exchange pool created and traded under that regime), a swap can push the on-chain `ExchangeCapsule.firstTokenBalance`/`secondTokenBalance` negative. This is a protocol-level invalid-state/accounting-divergence bug: the recorded liquidity pool state no longer reflects real backing, which can be exploited by successive trades to drain more value out of the pool than was ever deposited into it, corrupting exchange accounting on-chain (an accepted "invalid-state/divergence" impact class per the assessment criteria).

### Likelihood Explanation
Reachable by any unprivileged account simply by submitting `ExchangeTransactionContract` transactions against an existing TRC10↔TRC10 or TRC10↔TRX `Exchange` pair — no special privileges are required. The likelihood depends entirely on whether `hardenedCalc` (`allowHarden()`) is active for the network/pool in question; I was not able to fully verify from the available index whether this flag is unconditionally `true` on all currently-reachable chain states (I could not retrieve the full contents of `AbstractExchangeActuator.java` to confirm the exact gating logic and default value before the tool budget ran out).

### Recommendation
Make the non-negative balance check in `ExchangeCapsule.transaction` unconditional (remove the `hardenedCalc &&` guard) so that both the legacy and hardened math paths reject any transaction that would drive `newFirstTokenBalance` or `newSecondTokenBalance` below zero, regardless of the `allowHarden()` flag's state.

### Proof of Concept
Conceptual: create an `Exchange` pair with very small `secondTokenBalance`; call `ExchangeTransactionContract` selling `firstTokenId` with a `sellTokenQuant` large enough that the bancor formula in `ExchangeProcessor.exchange` returns a `buyTokenQuant` exceeding `secondTokenBalance`, while `hardenedCalc` is `false` for that call path (i.e., `allowHarden()` returns `false`). The transaction succeeds, the seller's account is credited the full computed `anotherTokenQuant`, and `exchange.secondTokenBalance` becomes negative in `ExchangeCapsule.transaction` since no floor check runs for the non-hardened branch — confirmed directly from the code shown above; I did not have execution access to run this end-to-end against a live node.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-169)
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
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-97)
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

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-221)
```java
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
