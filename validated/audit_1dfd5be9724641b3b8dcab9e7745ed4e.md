## Title
Non-deterministic Bancor-formula pricing in `ExchangeProcessor` can cause consensus divergence for TRC10 Exchange trades - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The Peapods report is a class of bug where an AMM's token pricing formula computes the wrong output amount, causing incorrect crediting of value to users. The closest reachable analog in java-tron is the TRC10 "Exchange" feature (a Bancor-relay AMM), whose default, non-hardened pricing path performs the core power-function pricing math with `java.lang.Math.pow` (platform/JIT-dependent double arithmetic) instead of `StrictMath.pow`, unless the `allowStrictMath` chain parameter has been activated by committee proposal.

### Finding Description
`ExchangeTransactionActuator.execute()` and `ExchangeInjectActuator`/`ExchangeWithdrawActuator` are reachable directly from a broadcast transaction (`ExchangeTransactionContract`), and call `ExchangeCapsule.transaction()`, which selects the pricing processor based on `dynamicStore.allowStrictMath()`: [1](#0-0) 

When hardened calculation is not active, `new ExchangeProcessor(supply, useStrictMath)` is used, and when `useStrictMath` is `false`, the Bancor-style bonding-curve formula is computed with `Maths.pow(..., this.useStrictMath)`, which internally can dispatch to non-deterministic `java.lang.Math.pow`: [2](#0-1) 

This is the exact same bug class as the report: the "fair pricing" AMM formula (there, `2*sqrt(k*p0*p1)/totalSupply`; here, `supply*(1-(1+quant/newBalance)^exp)`) is computed with an arithmetic implementation that is not guaranteed to be exact/deterministic across platforms, producing incorrect output token quantities. The repository's own test suite proves the divergence: the same inputs produce a different result depending on whether strict math is used, and the "safe"/hardened `SafeExchangeProcessor` (BigDecimal-based) was specifically introduced to correct this: [3](#0-2) [4](#0-3) 

The activation of the corrected/deterministic path is gated behind the `allowStrictMath`/`allowHarden` dynamic parameters, which are chain proposals (`ProposalUtil`, `DynamicPropertiesStore#allowStrictMath`), meaning the flawed legacy formula path remains live and reachable on any network/period where these proposals have not been activated. [5](#0-4) 

### Impact Explanation
If `allowStrictMath`/hardened calc is not enabled, the exchanged/injected/withdrawn token quantity computed by `ExchangeProcessor` can differ across nodes running different JVM implementations or JIT optimization levels for `Math.pow`, producing divergent `anotherTokenQuant` results for the same transaction and Exchange pool state. Since this value directly determines account asset balances written to state (`accountCapsule.addAssetAmountV2`/`reduceAssetAmountV2`) and the updated exchange pool balances (`exchangeCapsule.setBalance`), a divergence here is a state-root/consensus divergence bug and — even absent cross-node divergence — an incorrect price computation that mis-distributes value between the trader and the pool, analogous to the reported "incorrect amount of shares minted" impact.

### Likelihood Explanation
The vulnerable code path is the default when `allowStrictMath` (and `allowHarden`) have not been activated via governance proposal; TRC10 Exchange trading (`ExchangeTransactionContract`, `ExchangeInjectContract`, `ExchangeWithdrawContract`) is a normal, unprivileged, broadcastable transaction type, so the path is trivially reachable by any user. I could not confirm from the index whether `allowStrictMath`/`allowHarden` are activated by default on the current mainnet/production configuration (this needs verification against `DynamicPropertiesStore` default values and current on-chain proposal state, which requires a live/full node inspection beyond index coverage).

### Recommendation
Ensure `allowStrictMath` (and the hardened Exchange calculation) are permanently activated on all networks, and consider removing the legacy `ExchangeProcessor` double/`Math.pow` code path entirely, always routing Exchange pricing through the BigDecimal/`StrictMath`-based `SafeExchangeProcessor` to guarantee deterministic, correct results independent of proposal activation state.

### Proof of Concept
`framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java#testStrictMath` demonstrates, for identical `(sellBalance, buyBalance, sellQuant)` inputs, that `ExchangeProcessor` with `useStrictMath=false` yields a result not equal to the `useStrictMath=true`/`SafeExchangeProcessor` result, confirming the formula's non-determinism/incorrectness on the legacy path: [6](#0-5)

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-129)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-39)
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
```

**File:** framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java (L218-281)
```java
  @Test
  public void testStrictMath() {
    long supply = 1_000_000_000_000_000_000L;
    long[][] testData = {
        {4732214L, 2202692725330L, 29218L},
        {5618633L, 556559904655L, 1L},
        {9299554L, 1120271441185L, 7000L},
        {62433133L, 12013267997895L, 100000L},
        {64212664L, 725836766395L, 50000L},
        {64126212L, 2895100109660L, 5000L},
        {56459055L, 3288380567368L, 165000L},
        {21084707L, 1589204008960L, 50000L},
        {24120521L, 1243764649177L, 20000L},
        {836877L, 212532333234L, 5293L},
        {55879741L, 13424854054078L, 250000L},
        {66388882L, 11300012790454L, 300000L},
        {94470955L, 7941038150919L, 2000L},
        {13613746L, 5012660712983L, 122L},
        {71852829L, 5262251868618L, 396L},
        {3857658L, 446109245044L, 20637L},
        {35491863L, 3887393269796L, 100L},
        {295632118L, 1265298439004L, 500000L},
        {49320113L, 1692106302503L, 123267L},
        {10966984L, 6222910652894L, 2018L},
        {41634280L, 2004508994767L, 865L},
        {10087714L, 6765558834714L, 1009L},
        {42270078L, 210360843525L, 200000L},
        {571091915L, 655011397250L, 2032520L},
        {51026781L, 1635726339365L, 37L},
        {61594L, 312318864132L, 500L},
        {11616684L, 5875978057357L, 20L},
        {60584529L, 1377717821301L, 78132L},
        {29818073L, 3033545989651L, 182L},
        {3855280L, 834647482043L, 16L},
        {58310711L, 1431562205655L, 200000L},
        {60226263L, 1386036785882L, 178226L},
        {3537634L, 965771433992L, 225L},
        {3760534L, 908700758784L, 328L},
        {80913L, 301864126445L, 4L},
        {3789271L, 901842209723L, 1L},
        {4051904L, 843419481286L, 1005L},
        {89141L, 282107742510L, 100L},
        {90170L, 282854635378L, 26L},
        {4229852L, 787503315944L, 137L},
        {4259884L, 781975090197L, 295L},
        {3627657L, 918682223700L, 34L},
        {813519L, 457546358759L, 173L},
        {89626L, 327856173057L, 27L},
        {97368L, 306386489550L, 50L},
        {93712L, 305866015731L, 4L},
        {3281260L, 723656594544L, 40L},
        {3442652L, 689908773685L, 18L},
    };

    for (long[] data : testData) {
      ExchangeProcessor processor = new ExchangeProcessor(supply, false);
      long anotherTokenQuant = processor.exchange(data[0], data[1], data[2]);
      processor = new ExchangeProcessor(supply, true);
      long result = processor.exchange(data[0], data[1], data[2]);
      long safeResult = SafeExchangeProcessor.INSTANCE.exchange(data[0], data[1], data[2]);
      Assert.assertNotEquals(anotherTokenQuant, result);
      Assert.assertEquals(safeResult, result);
    }
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L66-69)
```java

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```
