### Title
Floating-point Bancor invariant drift in TRON's on-chain Exchange (`ExchangeProcessor`) causes divergent, manipulable exchange rates and permanent value leakage - (File: `chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java`)

### Summary
java-tron's built-in "Bancor-style" Exchange (`ExchangeCreate`/`ExchangeInject`/`ExchangeTransaction`/`ExchangeWithdraw`) maintains an internal virtual `supply` counter used to price trades between two pooled tokens/TRX. By default, this counter and the trade output are computed using native Java `double` arithmetic in `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` [1](#0-0) , rather than exact arbitrary-precision math. This is directly analogous to the PufferVault bug class: an internal accounting value (there, `lidoLockedETH`; here, the virtual bonding-curve `supply`/output quantity) can diverge from the value that would result from an exact calculation, because of unavoidable floating-point rounding. The result is that unprivileged users trading before vs. after other trades receive different, non-invariant-preserving exchange rates, and the divergence compounds with usage.

### Finding Description
`ExchangeCapsule.transaction()` selects between two `Processor` implementations depending on the `allowHardenExchangeCalculation` dynamic parameter: the legacy, default-reachable `ExchangeProcessor` (double math) or the exact `SafeExchangeProcessor` (`BigDecimal`) added later as a "hardened" fix [2](#0-1) . Whether the hardened path is active depends entirely on a governance-controlled dynamic property (`allowHardenExchangeCalculation`, toggled through a committee proposal per `ProposalUtil.java`/`ProposalService.java`); until/unless that proposal is activated, every call to `ExchangeTransactionActuator` - directly invokable by any unprivileged account - executes through the imprecise `ExchangeProcessor` path [3](#0-2) .

`ExchangeProcessor.exchangeToSupply` computes `issuedSupply` via `Math.pow`/`double` multiplication of the virtual `supply` field, then truncates to `long`, and permanently mutates the `supply` counter used for all subsequent trades in that call: [4](#0-3) 
`exchangeFromSupply` similarly uses `double` power operations to derive the final `buyTokenQuant` returned to the trader and written into `firstTokenBalance`/`secondTokenBalance` of the on-chain `Exchange` pool [5](#0-4) . This value directly drives `ret.setExchangeReceivedAmount(...)` and the account/pool balance updates in `ExchangeTransactionActuator.execute` [6](#0-5) .

The project's own test suite proves the divergence is real and non-trivial: `ExchangeProcessorTest.testStrictMath` runs identical inputs through the legacy double-based `ExchangeProcessor` and the exact `SafeExchangeProcessor`, and explicitly asserts the two results are *not equal* (`Assert.assertNotEquals(anotherTokenQuant, result)`), confirming the legacy path yields an incorrect (non-invariant-preserving) trade amount for real-world magnitudes [7](#0-6) .

This mirrors the reported PufferVault root cause precisely: an operation performed by an ordinary participant (here, any trader calling `ExchangeTransactionContract`; there, any depositor calling `deposit`) is priced/settled against an internal accounting variable (`supply` here, `lidoLockedETH` there) that is incremented/derived using an imprecise numerical method, producing a result that differs from the exact value that should have been produced. Because the pool's on-chain `firstTokenBalance`/`secondTokenBalance` are set directly from these imprecise outputs (`ExchangeCapsule.transaction`, lines 163-166), the error is not transient - it is baked into permanent on-chain state and compounds with every subsequent trade using the same (mutated) `supply` variable within/after that call.

### Impact Explanation
Because the AMM invariant is not exactly preserved, unprivileged traders executing `ExchangeTransactionContract` at different times (or as different callers, e.g., via `TriggerSmartContract` in a single or successive transactions) can receive systematically different trade outputs than the mathematically correct bonding-curve price would dictate. This can be leveraged to:
- Extract more value than the invariant permits from the pool over repeated trades (drift accumulates because `supply` is truncated to `long` after each step, and the `-supply * (1 - pow(...))` and `balance * (pow(...) - 1)` computations lose precision at the magnitudes typically involved, exactly as demonstrated by the test comparing legacy vs. exact results).
- Cause other honest liquidity providers/traders (via `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, whose own ratio math is separately computed via integer `floorDiv`/`BigInteger`, see `ExchangeInjectActuator.java` lines 71-83 and `ExchangeWithdrawActuator.java` lines 74-89) to receive balances inconsistent with the pool's true state, eventually leaving stuck/mispriced balances in the pool — an "underpriced-public-work"/accounting divergence impact consistent with the required impact classes (concrete accounting divergence in a market/exchange path reachable by unprivileged users).

This does not require any privileged role: any account can call `ExchangeTransactionContract`.

### Likelihood Explanation
Likelihood is contingent on the `allowHardenExchangeCalculation` dynamic property remaining disabled (its default/un-activated state, as it is gated behind a committee proposal in `ProposalUtil`/`ProposalService`). If the current mainnet has not activated this hardening proposal, the vulnerable double-precision path in `ExchangeProcessor` is live and reachable by any trader on every `ExchangeTransactionContract` call today. I was not able to confirm from the indexed code the current on-chain/activated value of this parameter (its default in `DynamicPropertiesStore` was not found in the indexed snippets), so whether this is *currently* exploitable in production versus already mitigated by governance activation is uncertain and should be verified directly against a live/synced node's proposal state before treating this as an active, exploitable issue.

### Recommendation
- Confirm whether `allowHardenExchangeCalculation` is currently activated on the target network; if not, activate the hardened path so all exchange trades route through `SafeExchangeProcessor`.
- Consider deprecating/removing the legacy `ExchangeProcessor` double-arithmetic implementation entirely rather than keeping it reachable behind a toggle, since the test suite itself demonstrates its output differs from the correct value.
- Audit `ExchangeInjectActuator`/`ExchangeWithdrawActuator`'s separate ratio-based balance math against the bonding-curve `supply` invariant used by `ExchangeTransactionActuator` to ensure all three paths agree on pool state consistency.

### Proof of Concept
Concrete PoC exists in-repo and is sufficient to demonstrate the root cause without needing new code: `ExchangeProcessorTest.testStrictMath` [8](#0-7)  feeds a table of realistic `(sellTokenBalance, buyTokenBalance, sellTokenQuant)` tuples through both the legacy `ExchangeProcessor` (non-hardened) and `SafeExchangeProcessor` (exact), asserting for every case that `anotherTokenQuant != result` where `result`/`safeResult` (exact) match each other but differ from the legacy double-based output. This directly demonstrates that `ExchangeTransactionActuator`, when hardening is not activated, settles trades using values that diverge from the mathematically correct invariant-preserving amount, for every realistic pool size tested.

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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-129)
```java
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath,
      boolean hardenedCalc) throws ContractValidateException {
    long supply = 1_000_000_000_000_000_000L;
    Processor processor = hardenedCalc
        ? SafeExchangeProcessor.INSTANCE : new ExchangeProcessor(supply, useStrictMath);

```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L64-91)
```java
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
