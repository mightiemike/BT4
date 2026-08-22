### Title
Missing invariant check on stable-swap style Exchange balances allows any user to drive `ExchangeCapsule` reserves negative and corrupt accounting - (`chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java`)

### Summary
`java-tron`'s native Bancor-style `Exchange` feature (`ExchangeCreateContract` / `ExchangeTransactionContract`) is the closest on-chain analog to a Velodrome-style stable-swap AMM: it maintains two token reserves per pair and computes swap outputs with a closed-form pricing formula, exactly like `Pair::_k()` maintains an invariant for Velodrome pools. Just as the Velodrome bug allowed the curve invariant to be bypassed due to a missing minimum/невalidated post-state check, `ExchangeCapsule.transaction()` updates the pair reserves without validating that the resulting balances stay non-negative unless a governance-gated "hardened" mode is enabled, which is off by default.

### Finding Description
`ExchangeCapsule.transaction()` computes the counter-asset amount using `ExchangeProcessor` (a double/`Math.pow` based Bancor relay formula) and then updates reserves with plain `long` arithmetic: [1](#0-0) 

The non-negative check on the resulting reserves is only performed when `hardenedCalc` is true: [2](#0-1) 

`hardenedCalc` is derived from `allowHarden()`, which reads a dynamic property that is only turned on through a committee proposal (`allowHardenExchangeCalculation`), i.e. it is not enabled by default: [3](#0-2) 

`ExchangeTransactionActuator.doValidate()` only checks that the anonymous caller's `tokenQuant`/`tokenExpected` bounds are respected and that the computed `anotherTokenQuant` meets the caller's minimum-expected amount - it never re-checks that the post-swap reserves would stay non-negative or otherwise consistent with an invariant: [4](#0-3) 

`ExchangeCreateActuator` also has no analog to `MINIMUM_LIQUIDITY`/`MINIMUM_K`: any two positive balances (e.g., `1` and `1`) are accepted as long as they are `>0` and below `ExchangeBalanceLimit`: [5](#0-4) 

Because `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` use floating-point `Math.pow` on a user-created pool with arbitrarily small reserves, the computed `buyTokenQuant` can, due to rounding, be disproportionate relative to the actual reserve size: [6](#0-5) 

In the default (non-hardened) execution path this lets `ExchangeTransactionActuator.execute()` persist a negative `firstTokenBalance`/`secondTokenBalance` into `ExchangeCapsule`/`ExchangeV2Store` via `Commons.putExchangeCapsule`, since no check rejects it: [7](#0-6) 

The presence of `SafeExchangeProcessor` and the `hardenedCalc`/`allowHardenExchangeCalculation` gating (visible in tests such as `hardenedSuccessExchangeCreate`) is direct evidence that the project itself recognized the unhardened math/invariant path as unsafe, but the mitigation is opt-in via governance proposal rather than default-on.

### Impact Explanation
An unprivileged, anonymous account (any address able to broadcast an `ExchangeCreateContract` + `ExchangeTransactionContract`) can create a minimal-liquidity exchange pair and drive its persisted reserves negative or otherwise arithmetically inconsistent. This corrupts on-chain accounting state (`Exchange`/`ExchangeV2Store`) analogous to the reported "pool drain / broken invariant" bug class, and can be leveraged to extract more of a counter-asset than the pool actually holds, or to permanently break the exchange for subsequent legitimate participants (DoS of that market), mirroring the impact category described in the report (loss of funds / accounting corruption / DoS via a reachable RPC/broadcast transaction path).

### Likelihood Explanation
Reachable directly by any account via a standard broadcast transaction (`ExchangeCreateContract` then `ExchangeTransactionContract`), with no privileged role required. The only barrier is the small `ExchangeCreateFee` and normal transaction fees, comparable to the "no real cost" griefing scenario in the original report. Exploitability depends on precisely how much floating point rounding in `ExchangeProcessor` can be abused at extreme reserve ratios/sizes, which the report's PoC methodology (repeated `mint`/`swap` transfer-and-swap patterns) is designed to probe empirically.

### Recommendation
Make the reserve non-negativity/invariant check (`newFirstTokenBalance < 0 || newSecondTokenBalance < 0`) unconditional in `ExchangeCapsule.transaction()` rather than gated behind `hardenedCalc`, and consider requiring a minimum reserve size (analogous to `MINIMUM_LIQUIDITY`/`MINIMUM_K`) in `ExchangeCreateActuator.doValidate()` so pools cannot be created with reserves small enough for floating-point rounding in `ExchangeProcessor` to become exploitable.

### Proof of Concept
Not independently executed; conceptually mirrors the original report's methodology: (1) call `ExchangeCreateContract` with minimal token balances (e.g., `1`/`1`) to bypass any liquidity floor, since none exists in `ExchangeCreateActuator.doValidate()`; (2) repeatedly call `ExchangeTransactionContract` with `tokenQuant`/`tokenExpected` chosen so that `ExchangeProcessor`'s double-based Bancor formula rounds `buyTokenQuant` disproportionately relative to actual reserves; (3) observe that, absent the governance-gated `allowHardenExchangeCalculation` flag, `ExchangeCapsule.transaction()` persists the resulting (potentially negative or inconsistent) balances without rejection. Full confirmation of exact numeric parameters that trigger the worst-case rounding would require running `ExchangeProcessor`/`ExchangeCapsuleTest`-style scenarios, which could not be executed in this read-only analysis.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L136-158)
```java
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L160-169)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L66-99)
```java

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-224)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }

    return true;
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L201-208)
```java
    if (firstTokenBalance <= 0 || secondTokenBalance <= 0) {
      throw new ContractValidateException("token balance must greater than zero");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
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
