### Title
Unchecked floating-point AMM math in `ExchangeTransactionActuator` can drain exchange pool reserves - (File: chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java)

### Summary
The `[M-07]` report describes a class of bug where a value that is supposed to be strictly conserved between two paired accounting ledgers (locked mainnet amount vs. mintable sidechain amount) can be inflated on one side without a corresponding, cryptographically/arithmetically enforced decrease on the other, letting an attacker drain the counter-party's reserve. The reachable java-tron analog of this bug class is the bonding-curve token-swap math in the TRC10 `Exchange` feature (`ExchangeTransactionContract` / `ExchangeTransactionActuator` / `ExchangeCapsule`), where the legacy (non-hardened) code path computes swap outputs with double-precision floating point and, unlike the newer hardened path, performs **no post-calculation invariant check** that the resulting pool balances stay non-negative/consistent.

### Finding Description
`ExchangeCapsule.transaction()` computes the amount of the counter-token to credit the caller using `ExchangeProcessor.exchange()`, which models a bancor-style relay curve with `Math.pow`/`StrictMath.pow` on `double` values: [1](#0-0) 

The result is applied to the pool balances in `ExchangeCapsule.transaction()`. Critically, the sanity check that the new pool balances cannot go negative is gated behind the `hardenedCalc` flag, which is only true when the `SafeExchangeProcessor` path is used: [2](#0-1) 

`hardenedCalc` is passed in as `allowHarden()` from `ExchangeTransactionActuator.execute()`: [3](#0-2) 

`allowHarden()`/`AllowHardenExchangeCalculation` is a **committee-gated dynamic parameter** (see `DynamicPropertiesStore`, `ProposalUtil`, `ProposalService` matches for `allowHardenExchangeCalculation`). Unless the committee has explicitly activated this proposal on a given chain, every `ExchangeTransactionContract` executed by any unprivileged account runs through the legacy `ExchangeProcessor`/floating-point path with `hardenedCalc == false`, where the `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` guard is skipped entirely — i.e. the actuator writes back whatever (possibly floating-point-corrupted or negative) balance the double-precision `pow()` math produces, with no arithmetic backstop.

This mirrors the `[M-07]` bug class: one side of a conserved-value system (the exchange pool's `firstTokenBalance`/`secondTokenBalance`, which represent locked user deposits from `ExchangeCreateActuator`/`ExchangeInjectActuator`) can be depleted/corrupted by a calculation path that is not properly validated for invariant preservation, letting a caller extract more value than the pool's true backing, at the expense of other depositors — analogous to draining `transferredAmount` by minting unconstrained value on the other side.

### Impact Explanation
Any account can call `ExchangeTransactionContract` (broadcast transaction, no special permission) against any active TRC10 `Exchange` pair. Repeated exploitation of floating-point rounding asymmetries in `exchangeToSupply`/`exchangeFromSupply`, combined with the missing non-negative-balance invariant check on the default code path, can corrupt or deplete the pool's `firstTokenBalance`/`secondTokenBalance` beyond what is economically backed, allowing an attacker to withdraw more of the counter-asset than the pool's real reserves justify. This directly harms other participants who hold a stake in that Exchange pair (its balances were deposited via `ExchangeCreateActuator`/`ExchangeInjectActuator`), i.e. it is an asset/accounting corruption analogous to the reported `transferredAmount` drain.

### Likelihood Explanation
Likelihood is Medium: exploitation requires no privileged role, node, or key compromise — only ordinary `ExchangeTransactionContract` transactions, matching the "unprivileged, broadcast transaction" requirement. However, the practical severity depends on whether a given network has already activated `AllowHardenExchangeCalculation`; if activated (as appears to be the intended long-term state, given `SafeExchangeProcessor` exists as a hardening measure), the guarded/hardened path is used and the missing invariant check is bypassed. If not yet activated, the unguarded legacy path remains the default and is exploitable to any degree floating-point rounding across the bancor relay formula permits.

### Recommendation
1. Enforce the non-negative/invariant balance check (`newFirstTokenBalance >= 0 && newSecondTokenBalance >= 0`, and ideally a product/conservation invariant) unconditionally in `ExchangeCapsule.transaction()`, not only when `hardenedCalc` is true.
2. Consider making `SafeExchangeProcessor` (integer/fixed-point-safe math) the sole and default processor rather than a governance-gated opt-in, removing the floating-point `ExchangeProcessor` path entirely from production use.
3. Add regression tests that specifically probe rounding edge cases (extreme balance ratios, minimal `tokenQuant`) to ensure the pool cannot be driven negative or made to pay out more than it received under the default configuration.

### Proof of Concept
1. Attacker identifies an `Exchange` pair created via `ExchangeCreateActuator` on a network where `AllowHardenExchangeCalculation` has not been enabled by the committee (default state).
2. Attacker repeatedly issues `ExchangeTransactionContract` transactions with small `tokenQuant` values chosen to maximize floating-point rounding in `ExchangeProcessor.exchangeToSupply`/`exchangeFromSupply` (double-precision `Math.pow` with `0.0005`/`2000.0` exponents).
3. Because `ExchangeCapsule.transaction()` only checks `newFirstTokenBalance < 0 || newSecondTokenBalance < 0` when `hardenedCalc` is true (`chainbase/.../ExchangeCapsule.java:160`), and this is false by default, the actuator accepts and persists pool balances distorted by floating-point error without any invariant re-verification.
4. Over many transactions, the attacker accumulates counter-token payouts exceeding the pool's true backing, effectively draining value that other Exchange depositors are owed — the on-chain analog of draining a bridge's locked reserve via an unvalidated "mint" pathway.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-76)
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

```
