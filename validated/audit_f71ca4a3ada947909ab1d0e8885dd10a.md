### Title
Stale/Divergent Redemption Payout in `ExchangeTransactionActuator` (TRX10 Bancor Exchange) — `execute()` Trusts a Second, Unlinked Rate Computation Instead of the Rate Checked in `validate()` - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java`)

### Summary
The reported bug class is: an actuator/router computes an "expected" payout from a state snapshot, but the value actually paid out is derived from a second, independent computation against the underlying reserve/rate state, with no on-chain verification that the two match. `ExchangeTransactionActuator` (java-tron's TRX10 Bancor-style token exchange) exhibits the same structural pattern: `doValidate()` computes `anotherTokenQuant` from a first call to `exchangeCapsule.transaction(...)` and checks it only against the caller-supplied `tokenExpected` slippage bound, while `execute()` independently re-invokes `exchangeCapsule.transaction(...)` a second time to compute the amount that is actually paid to the user — with no code path re-verifying that this second, execution-time result still satisfies `tokenExpected`, and no assertion that it equals the value computed during validation.

### Finding Description
`ExchangeCapsule.transaction()` is not a pure/read-only function — it mutates the exchange pool's `firstTokenBalance`/`secondTokenBalance` fields as a side effect of computing the swap output [1](#0-0) .

In `doValidate()`, the actuator calls this mutating function once to get `anotherTokenQuant` and checks it against the user-specified slippage floor `tokenExpected`: [2](#0-1) 

In `execute()`, run immediately afterward on a freshly-loaded `ExchangeCapsule`, the actuator calls the same mutating `transaction()` method **a second, independent time** to compute the `anotherTokenQuant` that is actually credited to the user's account: [3](#0-2) 

There is no code that carries the `anotherTokenQuant` value validated in `doValidate()` into `execute()`, and no re-check in `execute()` against `tokenExpected`. This mirrors the CoreRouter pattern exactly: an "expected" amount is derived and checked once against a stale/pre-call snapshot of state, while the value that is actually transferred is derived from a second, later computation against the underlying (bonding-curve) reserve state, without reconciling the two. Just as `LToken.redeem()`'s internal exchange rate can diverge from `CoreRouter`'s pre-computed `exchangeRateStored()`-based expectation, the exchange pool's `firstTokenBalance`/`secondTokenBalance` can diverge between the `validate()` snapshot and the `execute()` snapshot if any other operation touches the same `ExchangeCapsule` in between (e.g., another `ExchangeInjectActuator`/`ExchangeWithdrawActuator`/`ExchangeTransactionActuator` call sharing the exchange ID is processed by the node between the two reads, or if `validate()` and `execute()` are invoked non-atomically, e.g. during transaction re-validation/repush in `Manager.generateBlock`/`processTransaction`'s retry path at [4](#0-3) ). In that case, the user's slippage guarantee (`tokenExpected`) checked in `validate()` is not enforced against the value actually paid in `execute()`.

Note: `SafeExchangeProcessor`/`allowHarden()` hardening was later added [5](#0-4) , and the entire `ExchangeTransactionContract` type is now rejected from the mempool and block-packing paths as of fork `VERSION_4_8_0_1` [6](#0-5) . This confirms the exchange-calculation mismatch bug class was already a known concern in this subsystem, and the feature's live reachability is now conditional/deprecated rather than a fully active mainnet code path.

### Impact Explanation
If the double-computation of `transaction()` (validate-time vs execute-time) diverges due to intervening pool-state changes, the actuator can either (a) credit the user fewer tokens than the validated/expected slippage-protected amount, silently violating the user's price-protection contract, or (b) apply an execute-time price that is more favorable/unfavorable than what was validated, causing an accounting mismatch between what the "expected" check certified and what actually moved. This is an accounting/invalid-state divergence in a live financial primitive (token AMM), analogous to CoreRouter's fund-drain/trapped-funds pattern, though the impact here is bounded by the fact that both sides operate on the same self-contained pool reserves (no external call to an untrusted contract), so no reserve depletion beyond the pool's own balances is possible.

### Likelihood Explanation
Likelihood is low-to-moderate on current mainnet: `ExchangeTransactionContract` transactions are explicitly rejected from `pushTransaction` and block packing once fork `VERSION_4_8_0_1` is active [7](#0-6) , and `isExchangeTransaction` is gated behind `allowHardenExchangeCalculation()` [8](#0-7) . This means the double-computation-mismatch window is largely closed under current chain parameters, and the analog is not a strongly "reachable" unprivileged path on a fully-upgraded network — it would only resurface if a fork reactivated `ExchangeTransactionContract` processing or if the retry path at `Manager.processTransaction` re-executes `trace.exec()` (calling actuator validate+execute a second time) using stale block/trace state [9](#0-8) , a scenario I was not able to fully confirm re-reads a changed `ExchangeCapsule` state.

### Recommendation
Have `execute()` reuse the exact `anotherTokenQuant` value produced and validated in `doValidate()` (e.g., by caching it in the actuator instance or contract execution context) rather than recomputing it from a freshly re-read, mutable `ExchangeCapsule`. Alternatively, re-assert `anotherTokenQuant >= tokenExpected` at execution time, immediately before crediting the user, so that any divergence between the validate-time and execute-time pool state fails the transaction instead of silently applying a different rate.

### Proof of Concept
Not constructible from static analysis alone — reproducing the divergence requires confirming (a) whether `validate()` and `execute()` for the same `ExchangeTransactionContract` can execute non-atomically with intervening exchange-pool mutations (e.g., via the `checkNeedRetry`/re-`exec()` path in `Manager.processTransaction`), and (b) whether `ExchangeTransactionContract` is still processed on any currently-supported network configuration. I was unable to confirm either condition from the available code and did not want to speculate further; a Devin session with test/replay tooling would be needed to determine actual exploitability.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-91)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L897-899)
```java
    if (isExchangeTransaction(trx.getInstance())) {
      throw new ContractValidateException("ExchangeTransactionContract is rejected");
    }
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1561-1571)
```java
    trace.exec();

    if (Objects.nonNull(blockCap)) {
      trace.setResult();
      if (trace.checkNeedRetry()) {
        trace.init(blockCap, eventPluginLoaded);
        trace.checkIsConstant();
        trace.exec();
        trace.setResult();
        logger.info("Retry result when push: {}, for tx id: {}, tx resultCode in receipt: {}.",
            blockCap.hasWitnessSignature(), txId, trace.getReceipt().getResult());
```

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L1809-1828)
```java
  private boolean isExchangeTransaction(Transaction transaction) {
    if (getDynamicPropertiesStore().allowHardenExchangeCalculation()) {
      return false;
    }
    Contract contract = transaction.getRawData().getContract(0);
    switch (contract.getType()) {
      case ExchangeTransactionContract: {
        return true;
      }
      default:
        return false;
    }
  }

  private void rejectExchangeTransaction(Transaction transaction) throws ContractValidateException {
    if (isExchangeTransaction(transaction) && chainBaseManager.getForkController()
            .pass(Parameter.ForkBlockVersionEnum.VERSION_4_8_0_1)) {
      throw new ContractValidateException("ExchangeTransactionContract is rejected");
    }
  }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java (L1-47)
```java
package org.tron.core.capsule;

import java.math.BigDecimal;
import java.math.RoundingMode;
import lombok.extern.slf4j.Slf4j;
import org.tron.common.math.StrictMathWrapper;

@Slf4j(topic = "capsule")
public class SafeExchangeProcessor implements ExchangeCapsule.Processor {

  private static final BigDecimal SUPPLY = BigDecimal.valueOf(1_000_000_000_000_000_000L);

  public static final SafeExchangeProcessor INSTANCE = new SafeExchangeProcessor();

  private SafeExchangeProcessor() {

  }

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
}


```
