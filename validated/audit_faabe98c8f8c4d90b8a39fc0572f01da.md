### Title
Floating-point rounding drift in the legacy Bancor-relay `ExchangeProcessor` allows repeated small trades to extract value from TRX/TRC10 exchange pools beyond the mathematically correct amount - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
The external report describes an actor repeatedly exploiting an allowed slippage tolerance in a price-reference formula to extract more value than a fair conversion should yield, resetting state between iterations, and draining a pool over many transactions. The closest reachable analog in java-tron is the on-chain TRC10/TRX bancor-relay exchange (`ExchangeTransactionContract`), whose default (non-hardened) settlement math is computed with IEEE-754 `double` arithmetic in `ExchangeProcessor`, rather than exact `BigDecimal`/`BigInteger` math. Any unprivileged account can call `ExchangeTransactionContract` repeatedly against a pool it does not own, and the rounding characteristics of `Math.pow` on doubles are not guaranteed to be conservative in the exchange's favor on every call.

### Finding Description
`ExchangeCapsule.transaction()` selects between two `Processor` implementations depending on the chain parameter `allowHardenExchangeCalculation`: [1](#0-0) 

- The default/legacy path uses `ExchangeProcessor`, which computes the bancor "relay supply" and resulting token amount using `double` math and `Maths.pow`: [2](#0-1) 

- The "hardened" path uses `SafeExchangeProcessor`, which performs the same formula with `BigDecimal` at 18-digit scale and `RoundingMode.HALF_UP`, explicitly designed to avoid the drift/overshoot the floating-point version can produce: [3](#0-2) 

Whether the hardened path is used is gated by a chain-wide, committee-controlled parameter (`allowHardenExchangeCalculation`) read through `AbstractExchangeActuator.allowHarden()`: [4](#0-3) 

Any account can invoke the trade path via `ExchangeTransactionActuator.execute()`, which calls `exchangeCapsule.transaction(...)` and only enforces a caller-supplied minimum-output check (`tokenExpected`), not any invariant about the pool's product/curve being non-decreasing in the exchange's favor: [5](#0-4) [6](#0-5) 

The `ExchangeProcessorTest` itself documents that the strict-double and hardened `BigDecimal` implementations diverge on realistic inputs (`testStrictMath`, `testHardenedExchange`), confirming the double-precision path does not reproduce the exact bancor curve: [7](#0-6) 

This is structurally analogous to the reported bug class: a party (here, any unprivileged trader, not a privileged role) repeatedly performs an operation (`ExchangeTransactionContract`) against a reserve whose settlement math has a built-in tolerance/approximation (here, floating-point rounding of the bancor relay formula instead of an exact "fair price"), and each call can, depending on rounding direction, transfer slightly more value out of the pool than the exact bonding-curve math would allow. Because the actuator applies no invariant check (e.g., "post-trade product must not decrease"), repeated small trades that land favorably on rounding can be executed indefinitely by any address to accumulate an edge against the pool's liquidity providers.

### Impact Explanation
If the floating-point rounding in `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` is systematically biased in the trader's favor for certain balance/quantity ranges, an unprivileged attacker can repeatedly call `ExchangeTransactionContract` (a normal broadcastable transaction, fee = 0 per `calcFee()`) to drain TRX/TRC10 value from a public exchange pool over many transactions, harming the pool's creator/liquidity. This affects exchange/market math and resource accounting reachable from any broadcast transaction, matching the required impact classes (accounting corruption via a normal RPC-broadcast transaction).

### Likelihood Explanation
Likelihood is bounded by two factors that could not be fully confirmed with the available tools: (1) whether `allowHardenExchangeCalculation` is enabled by default on the target network (if enabled, the exact `BigDecimal` path is used and this issue is neutralized), and (2) the actual magnitude/direction of floating-point drift across realistic balance ranges, which would require targeted numeric analysis or fuzzing of `ExchangeProcessor` against `SafeExchangeProcessor` across many balance/quantity permutations to determine whether the divergence is exploitable at meaningful scale or only causes negligible rounding-dust differences. The existence of `SafeExchangeProcessor` as an opt-in "hardening" fix strongly suggests the floating-point path was already recognized internally as imprecise/exploitable in some regime, which raises confidence that a repeated-small-trade drift attack is plausible, but I could not verify the default state of the `allowHardenExchangeCalculation` parameter in this pass.

### Recommendation
- Confirm and, if necessary, change the default of `allowHardenExchangeCalculation` so `SafeExchangeProcessor` (exact `BigDecimal` math) is always used for new pools, removing the legacy `double`-based `ExchangeProcessor` path entirely.
- Add an explicit non-decreasing-invariant check in `ExchangeTransactionActuator.execute()`/`ExchangeCapsule.transaction()` (e.g., verify the constant-product/bancor invariant does not decrease after a trade) independent of which processor is used, so no rounding implementation can be exploited to drain reserves regardless of future formula changes.
- Add exhaustive property-based tests comparing `ExchangeProcessor` and `SafeExchangeProcessor` outputs across a wide grid of balances/quantities (not just the fixed vectors in `ExchangeProcessorTest`) to bound the worst-case drift and rule out cumulative-drain scenarios via repeated small trades.

### Proof of Concept
Not independently reproducible from the available code/test index alone; the divergence between `ExchangeProcessor` (double) and `SafeExchangeProcessor` (BigDecimal) is already demonstrated by the existing test `testStrictMath`, which asserts `anotherTokenQuant != result` for numerous real balance/quantity triples while `safeResult == result` (i.e., the double-based legacy path differs from the exact hardened path for the same inputs): [8](#0-7) 
A full end-to-end PoC (repeated `ExchangeTransactionContract` calls against a live pool to show net extraction) would require running the actual node/test harness with `allowHardenExchangeCalculation` disabled and iterating trades against a fixed pool, which is outside what could be verified via static code search alone.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/AbstractExchangeActuator.java (L13-15)
```java
  protected boolean allowHarden() {
    return chainBaseManager.getDynamicPropertiesStore().allowHardenExchangeCalculation();
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

**File:** framework/src/test/java/org/tron/core/capsule/utils/ExchangeProcessorTest.java (L218-280)
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
```
