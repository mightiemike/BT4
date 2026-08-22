## Title
Missing invariant check after floating-point bonding-curve swap computation in TRC10 `Exchange` (Bancor-style pool) — (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The `ExchangeProcessor`/`SafeExchangeProcessor` classes implement the TRC10 `Exchange` AMM pool's swap math using a two-step Bancor-style bonding-curve formula (`balance → relay-supply → balance`), computed with a floating-point `pow()` approximation exactly like the Newton-method approximation described in the external report. Just like the reported bug class, the code never verifies that the swap result is consistent with the underlying invariant of the bonding curve — it simply casts the approximate floating-point result to a `long` and applies it to the pool balances, with no check that the output does not exceed what the exact (infinite-precision) curve would produce.

### Finding Description
`ExchangeProcessor.exchange()` computes the swap amount via two chained power-function approximations: [1](#0-0) 

`exchangeToSupply` and `exchangeFromSupply` use `Maths.pow(base, exponent, useStrictMath)`, which is a double-precision floating point approximation of `(1+x)^w`. This is the java-tron analog of the Newton-method-approximated `t` in the Aftermath report: both approaches numerically approximate a nonlinear invariant curve and truncate/cast to an integer result. `SafeExchangeProcessor` (the "hardened" variant) performs the same computation with `BigDecimal` but still relies on `StrictMathWrapper.pow` (a double-precision `StrictMath.pow` call) as the core approximation: [2](#0-1) 

The only checks performed after the swap in `ExchangeCapsule.transaction()` are overflow (`addExact`/`subtractExact`) and non-negative balance: [3](#0-2) 

There is no assertion analogous to the Aftermath fix's `assert!(invariant_after >= invariant)` — nothing verifies that the computed `buyTokenQuant` does not exceed the amount implied by the exact bonding-curve invariant (i.e., that the swap is "fair" to the pool). Notably, `ExchangeWithdrawActuator`'s hardened path *does* add a precision-loss guard ("Not precise enough") for the linear withdraw ratio calculation: [4](#0-3) 

but the core swap path (`ExchangeTransactionActuator` → `ExchangeCapsule.transaction` → `ExchangeProcessor`/`SafeExchangeProcessor`) has no equivalent bound-check on the nonlinear `pow`-based invariant calculation, even though this is exactly the calculation most susceptible to floating-point approximation error (the historical `MathWrapper` hard-coded pow-result overrides for specific main-net blocks confirm this computation has previously produced divergent results across platforms): [5](#0-4) 

### Impact Explanation
`ExchangeTransactionActuator` is reachable by any account via a normal broadcast `ExchangeTransactionContract` transaction: [6](#0-5) 

Because the swap output is derived purely from an unchecked floating-point approximation of the bonding curve invariant, an attacker who can construct pool balances/quant combinations where the `pow` approximation rounds favorably (analogous to the PoC in the report where the new invariant became smaller than the old invariant) can extract more of the counter-asset than the exact curve entitles them to. Repeated exploitation across many transactions can drain a TRC10 Exchange pool's reserves (asset/accounting corruption), and because the legacy non-strict path (`MathWrapper`) is platform-dependent, it can also contribute to consensus divergence between nodes using different JVM/CPU pow implementations.

### Likelihood Explanation
The swap path is invoked by ordinary user transactions with attacker-controlled `tokenQuant`/`tokenId`, and pool balances are influenced over time by prior inject/withdraw/transaction operations, giving an attacker practical control over the inputs to the approximation. No permission or privileged role is required. However, exploitation requires finding balance/quant combinations where the floating-point rounding is favorable enough to be profitable net of any fee/expected-amount checks, which is a search problem rather than an immediate one-shot exploit — this is a real gap but requires targeted probing.

### Recommendation
Add an explicit invariant check after computing the swap result in `ExchangeProcessor`/`SafeExchangeProcessor` (or in `ExchangeCapsule.transaction`), analogous to the Aftermath Finance fix: recompute the bonding-curve invariant using the higher-precision path and assert that the post-swap invariant is not smaller than required (i.e., that `buyTokenQuant` never exceeds the value implied by the exact curve, with a bounded tolerance), rejecting the transaction (`ContractValidateException`) otherwise — mirroring the existing "Not precise enough" guard already used in `ExchangeWithdrawActuator`.

### Proof of Concept
No concrete failing input was constructed in this analysis (that requires live iteration over pool balances/quant values to find a case where `Maths.pow`/`StrictMathWrapper.pow` rounding favors the trader beyond the exact bonding-curve entitlement, mirroring the Move-based PoC in the report). The structural gap — swap output computed via an unchecked floating-point approximation with no post-swap invariant assertion — is demonstrated directly by the cited code in `ExchangeProcessor.java`, `SafeExchangeProcessor.java`, and `ExchangeCapsule.java`.

### Citations

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

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L19-38)
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
```

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

**File:** platform/src/main/java/arm/org/tron/common/math/MathWrapper.java (L16-22)
```java
  private static final Map<PowData, Double> powData = Collections.synchronizedMap(new HashMap<>());
  private static final String EXPONENT = "3f40624dd2f1a9fc"; // 1/2000 = 0.0005

  public static double pow(double a, double b) {
    double strictResult = StrictMath.pow(a, b);
    return powData.getOrDefault(new PowData(a, b), strictResult);
  }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L38-107)
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
      final ExchangeTransactionContract exchangeTransactionContract = this.any
          .unpack(ExchangeTransactionContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeTransactionContract.getOwnerAddress().toByteArray());

      ExchangeCapsule exchangeCapsule = Commons
          .getExchangeStoreFinal(dynamicStore, exchangeStore, exchangeV2Store)
          .get(ByteArray.fromLong(exchangeTransactionContract.getExchangeId()));

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

      ret.setExchangeReceivedAmount(anotherTokenQuant);
      ret.setStatus(fee, code.SUCESS);
    } catch (ItemNotFoundException | InvalidProtocolBufferException
        | ContractValidateException | ArithmeticException e) {
      logger.debug(e.getMessage(), e);
      ret.setStatus(fee, code.FAILED);
      throw new ContractExeException(e.getMessage());
    }
    return true;
  }
```
