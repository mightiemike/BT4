### Title
Unchecked long-overflow feeds an invalid (negative) base into `Math.pow`, letting `ExchangeProcessor` silently return corrupted results instead of reverting — ([File: chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java])

### Summary
The bancor-style AMM math used by `ExchangeCapsule.transaction()` mirrors the reported Ln.sol flaw: a mathematical helper (`Math.pow`/`StrictMath.pow` via `Maths.pow`) is invoked with a value that can fall outside its valid domain (a negative base combined with a non-integer exponent, which is mathematically undefined and returns `NaN` in Java) without any pre-validation. The `NaN` is then silently coerced to a `long` (via `(long) NaN == 0` in Java) and used to update on-chain exchange balances, instead of the transaction being rejected.

### Finding Description
`ExchangeCapsule.transaction()` [1](#0-0)  computes new token balances using plain `+`/`-` (no overflow check) when `hardenedCalc` is `false`, which is the default/legacy path unless `allowHardenExchangeCalculation()` is explicitly enabled:
```
newFirstTokenBalance = hardenedCalc ? StrictMathWrapper.addExact(...) : firstTokenBalance + sellTokenQuant;
```
The actual exchange amount is produced by `ExchangeProcessor.exchange()`:
```java
private long exchangeToSupply(long balance, long quant) {
    long newBalance = balance + quant;              // unchecked, can overflow to negative
    double issuedSupply = -supply * (1.0
        - Maths.pow(1.0 + (double) quant / newBalance, 0.0005, this.useStrictMath));
    long out = (long) issuedSupply;                  // NaN silently becomes 0
    ...
}
``` [2](#0-1) 

If `balance + quant` overflows to a negative `long` (or `quant` is large enough to make `1.0 + quant/newBalance` negative), `Maths.pow(negativeBase, 0.0005, ...)` computes `Math.pow`/`StrictMath.pow` with a negative base and a non-integer exponent — a mathematically undefined operation, exactly analogous to calling `ln()` on a negative number. Java's `Math.pow` returns `NaN` in this case instead of throwing. The code performs **no validation** of the base before calling `pow`, and the resulting `NaN` is silently cast to `0L`, corrupting the computed trade amount instead of causing the transaction to revert.

This differs from the "hardened" path (`SafeExchangeProcessor`, gated by `allowHardenExchangeCalculation()` [3](#0-2) ), which uses `StrictMathWrapper.addExact` and would throw `ArithmeticException` on overflow — but this hardened mode is opt-in via a dynamic chain parameter and is not the default computation path, per `ExchangeCapsule.transaction` [4](#0-3) . Additionally, the non-negative-balance check `if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0))` is only enforced in the hardened branch [5](#0-4) , meaning in the default legacy path an overflowed/negative balance is never detected.

### Impact Explanation
Reaching this code requires an unprivileged, anonymous broadcast of an `ExchangeTransactionContract` (or `ExchangeInjectContract`) against an existing bancor-relay exchange pair, via `ExchangeTransactionActuator.execute()` [6](#0-5) . A user can create a TRC10 token with an attacker-chosen total supply (up to `Long.MAX_VALUE`) and inject/sell a huge quantity into an exchange pair so that `balance + quant` overflows, or otherwise drives `1.0 + quant/newBalance` negative. This silently yields `NaN → 0` for the traded amount and can leave the exchange pool's stored balances corrupted (potentially negative/overflowed `long` values persisted to `ExchangeCapsule`/`ExchangeV2Store`), which is an accounting-corruption analog of the reported bug: silent acceptance of an out-of-domain math input instead of reverting.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to control a token with an extremely large balance/quantity (feasible for self-issued TRC10 tokens, since supply and quant are attacker-controlled at issuance time) and to interact with the legacy (non-hardened) exchange math path, which remains the default unless the `allowHardenExchangeCalculation` dynamic parameter has been activated network-wide. No special privilege is required — only an ordinary broadcast transaction.

### Recommendation
- In `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply`, validate that the computed base (`1.0 + quant/newBalance`) is strictly positive before calling `Maths.pow`, and reject (revert with `ContractValidateException`/`ArithmeticException`) if it is not, mirroring proper domain validation rather than allowing `NaN` to propagate.
- Always use overflow-checked arithmetic (`StrictMathWrapper.addExact`/`subtractExact`) for balance updates in `ExchangeCapsule.transaction`, not just in the hardened path, and always enforce the non-negative-balance invariant regardless of `hardenedCalc`.
- Consider making the hardened/safe exchange computation the default rather than an opt-in dynamic property.

### Proof of Concept
Conceptual reproduction (cannot be executed without a running node):
1. Issue a TRC10 token with total supply near `Long.MAX_VALUE`.
2. Create/participate in an `ExchangeTransactionContract` pair using this token, injecting a token quantity `quant` large enough that `firstTokenBalance + quant` overflows a signed 64-bit `long` to a negative value (or otherwise drives `1.0 + quant/newBalance` negative) while `allowHardenExchangeCalculation` is disabled (default).
3. Observe that `ExchangeProcessor.exchangeToSupply` computes `Maths.pow(negativeBase, 0.0005, ...)`, which is `NaN`, cast to `0L`; the trade silently completes with a corrupted/zeroed result and the pool balances may be left inconsistent, instead of the transaction being rejected as mathematically invalid.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L118-146)
```java
  @VisibleForTesting
  public long transaction(byte[] sellTokenID, long sellTokenQuant, boolean useStrictMath)
      throws ContractValidateException {
    return transaction(sellTokenID, sellTokenQuant, useStrictMath, false);
  }

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

```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L160-162)
```java
    if (hardenedCalc && (newFirstTokenBalance < 0 || newSecondTokenBalance < 0)) {
      throw new ContractValidateException("Exchange balance must be >=0 after transaction");
    }
```

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L17-29)
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
