### Title
Exchange invariant (non-negative reserve / no free-token creation) is only enforced under an opt-in feature flag, leaving the default TRC10 bancor-exchange path unchecked - (File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java)

### Summary
The Trail-of-Bits report on Umee flags the absence of enforced invariants (e.g. "reward == equivalent value", "collateral >= reward", balances never negative) around monetary/exchange math, recommending that critical invariants be asserted unconditionally rather than left untested. java-tron's built-in TRC10 bancor-style exchange (`ExchangeCreate/Inject/Withdraw/TransactionActuator`) has an analogous core invariant — pool reserves must never go negative and the double-precision bancor formula must not let users extract more value than the pool holds — but this invariant is only checked when the `allowHardenExchangeCalculation` (a committee-controlled dynamic parameter) is turned on.

### Finding Description
`ExchangeCapsule.transaction()` computes the exchanged amount via `Processor.exchange()`, which by default is `ExchangeProcessor`, a floating-point (`double`) bancor-formula implementation using `Maths.pow` (backed by `Math.pow`/`StrictMath.pow` plus hand-patched historical divergence data in `MathWrapper`) [1](#0-0) . Only when `hardenedCalc` is `true` (i.e. `dynamicStore.allowHardenExchangeCalculation()` returns true) does the code route through `SafeExchangeProcessor` (BigDecimal-based) and add the explicit invariant check:

```
if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
  throw new ContractValidateException("Exchange balance must be >=0 after transaction");
}
``` [2](#0-1) 

When `hardenedCalc` is `false` (the non-hardened/default code path), `newFirstTokenBalance`/`newSecondTokenBalance` are computed with plain `long` arithmetic and never validated against going negative:
```
newFirstTokenBalance = hardenedCalc ? StrictMathWrapper.addExact(...) : firstTokenBalance + sellTokenQuant;
newSecondTokenBalance = hardenedCalc ? StrictMathWrapper.subtractExact(...) : secondTokenBalance - buyTokenQuant;
...
this.exchange = this.exchange.toBuilder().setFirstTokenBalance(newFirstTokenBalance)...
``` [3](#0-2) 

`ExchangeTransactionActuator.execute()` calls this same `transaction()` and then unconditionally credits the account with `anotherTokenQuant` and mutates the pool balances, without any additional server-side sanity check that the resulting reserves are non-negative or that the bancor formula's floating-point rounding didn't let the trader extract more tokens than the pool can back [4](#0-3) . `doValidate()` for `ExchangeTransactionActuator` only checks `anotherTokenQuant < tokenExpected` (a slippage floor chosen by the caller) — it performs no independent recomputation or invariant assertion of reserve conservation [5](#0-4) .

The project's own test suite proves the two code paths diverge for realistic inputs — `testStrictMath` explicitly asserts `Assert.assertNotEquals(anotherTokenQuant, result)` between the non-strict (`double`) and strict/hardened paths for real pool data [6](#0-5) , confirming that the legacy floating-point formula can legitimately produce different (and unchecked) results than the invariant-preserving BigDecimal implementation. This is exactly the "recommended invariant not enforced" pattern the Umee report calls out: the fix (`SafeExchangeProcessor` + the `>=0` check) exists in the codebase but is gated behind a proposal-controlled flag rather than being the default, unconditional behavior.

### Impact Explanation
If a network (or period before the hardening proposal is activated by the committee) runs with `allowHardenExchangeCalculation` disabled — which is the code's fall-through/default behavior — repeated exchange transactions using floating-point rounding at pool-balance extremes (very small reserves, very large `quant`, or values near the boundaries the historical `MathWrapper` patch table was built to paper over) could drive one side of the exchange pool's `firstTokenBalance`/`secondTokenBalance` negative or allow a trader to receive more of the counter-token than the pool actually holds. Because these balances back real TRX/TRC10 asset accounting, this is a direct accounting-corruption / value-extraction primitive reachable by any account broadcasting `ExchangeTransactionContract`, `ExchangeInjectContract`, or `ExchangeWithdrawContract` transactions — no privileged role required.

### Likelihood Explanation
Exploitability is contingent on `allowHardenExchangeCalculation` being disabled for the target network/height, which the code treats as the default/fallback state whenever the dynamic property has not been explicitly set to `1` via committee proposal. The repository's own regression tests demonstrate concrete numeric divergence between the unchecked and checked formulas [6](#0-5) , showing the unsafe path is reachable and produces different results in practice, not merely a theoretical rounding concern.

### Recommendation
Make the reserve non-negativity/invariant check in `ExchangeCapsule.transaction()` and reconciliation logic in the actuators unconditional (not gated by `hardenedCalc`/`allowHardenExchangeCalculation`), and consider migrating the default exchange formula to the `SafeExchangeProcessor` BigDecimal implementation network-wide instead of leaving legacy floating-point math as the default. Additionally, add explicit assertions/regression tests (as recommended by the Umee report for its leverage module) that pool reserves after every `ExchangeInject/Withdraw/Transaction` execution satisfy `firstTokenBalance >= 0 && secondTokenBalance >= 0` and that the bancor constant-product/exchange-rate relationship holds within expected bounds, independent of feature-flag state.

### Proof of Concept
Not independently reproduced against a live node in this analysis (index-only investigation); the divergence is demonstrated in-repo by `ExchangeProcessorTest.testStrictMath`, which shows the default (`useStrictMath=false`) `ExchangeProcessor.exchange()` and the invariant-checked `SafeExchangeProcessor.exchange()` produce different results for the same realistic pool/quant inputs [6](#0-5) . A full PoC would require running a `java-tron` node with `allowHardenExchangeCalculation` unset/0, creating an `Exchange` via `ExchangeCreateContract`, and broadcasting a series of `ExchangeTransactionContract`s with `quant` values chosen (via the same search technique used to build the `MathWrapper` divergence table) to push `newFirstTokenBalance`/`newSecondTokenBalance` negative, then confirming `ExchangeCapsule.transaction()` does not reject the resulting negative balance because the `hardenedCalc` check is only reached when the flag is enabled.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L1-46)
```java
package org.tron.core.capsule;

import lombok.extern.slf4j.Slf4j;
import org.tron.common.math.Maths;

@Slf4j(topic = "capsule")
public class ExchangeProcessor implements ExchangeCapsule.Processor {

  private long supply;
  private final boolean useStrictMath;

  public ExchangeProcessor(long supply, boolean useStrictMath) {
    this.supply = supply;
    this.useStrictMath = useStrictMath;
  }

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-99)
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
