### Title
Missing slippage protection in `ExchangeInjectContract`/`ExchangeWithdrawContract` enables sandwich attacks on Bancor-style liquidity operations - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`java-tron`'s built-in Bancor-style Exchange (`ExchangeCreate/Inject/Withdraw/Transaction`) is the closest on-chain analog to the Basin `Well` AMM described in the report. Just like Basin's `addLiquidity`/`removeLiquidityOneToken`, the inject/withdraw operations compute the amount of the "other" token proportionally from the live pool reserves at execution time. Unlike `ExchangeTransactionContract` (the swap operation), which carries an `expected` field enforced by a slippage check, the `ExchangeInjectContract` and `ExchangeWithdrawContract` messages carry no analogous minimum/maximum bound on the computed `anotherTokenQuant`. This lets an attacker who can reorder/front-run a victim's inject/withdraw transaction shift the pool ratio beforehand (via `ExchangeTransactionContract` swaps) so the victim's proportional deposit/withdrawal executes at a manipulated ratio, then reverse the swap afterward to extract value — the same "unbalanced deposit/withdraw sandwich" root cause as the reported C4 finding.

### Finding Description
`ExchangeInjectActuator.execute`/`doValidate` reads the current `firstTokenBalance`/`secondTokenBalance` from `ExchangeCapsule` and derives `anotherTokenQuant` purely from the ratio present at execution time: [1](#0-0) 

The validation path recomputes the same ratio-derived amount with no user-supplied bound check other than "not exceeding the account's own balance": [2](#0-1) 

`ExchangeWithdrawActuator` has the identical pattern: `anotherTokenQuant` is derived from `firstTokenBalance`/`secondTokenBalance` at execution time, and while it has a "precision" (`allowHarden`) sanity check, it has no user-defined minimum for the amount actually returned: [3](#0-2) [4](#0-3) 

By contrast, the swap actuator `ExchangeTransactionActuator` explicitly protects the caller with an `expected` minimum-output check: [5](#0-4) 

The `ExchangeInjectContract`/`ExchangeWithdrawContract` protobuf messages only carry `owner_address`, `exchange_id`, `token_id`, and `quant` — no `expected`/minimum field exists to bound the counter-asset amount: [6](#0-5) 

The reserve ratio that both `Inject`/`Withdraw` rely on is mutated by ordinary swaps through `ExchangeCapsule.transaction`, which applies the Bancor formula against `firstTokenBalance`/`secondTokenBalance`: [7](#0-6) 

Because a `ExchangeTransactionContract` swap immediately before the victim's `ExchangeInject`/`ExchangeWithdraw`, followed by a reversing swap immediately after, is an ordinary and fully unprivileged sequence of broadcast transactions, an attacker who observes a pending inject/withdraw in the mempool (or otherwise controls transaction ordering within the same block) can manipulate the ratio used by the victim's operation, causing the victim to deposit more of the counter-asset than intended, or withdraw less of it than intended, while the attacker profits by reversing the price move.

### Impact Explanation
This is a genuine, unprivileged asset/accounting corruption vector reachable purely via broadcast transactions against the on-chain Exchange contracts (a TRON-native AMM feature, not privileged/test-only code). A successful sandwich lets the attacker extract value from any user who injects or withdraws liquidity from an `Exchange` pool without knowledge of this gap, mirroring the Medium-severity classification given to the original Basin finding (fund loss for LPs performing unbalanced operations without slippage protection). Severity is bounded by the same factors noted by the original judges: front-running AMMs is a known, inherent risk, and the impact is limited by pool size/`ExchangeBalanceLimit`, not unbounded contract compromise.

### Likelihood Explanation
Likelihood is meaningful but not certain: it requires (a) an Exchange pool with sufficient liquidity/spread to make the sandwich profitable after transaction fees/bandwidth costs, and (b) the attacker being able to place transactions immediately before and after the victim's inject/withdraw transaction in the same or adjacent blocks — feasible for any observer of the public TRON mempool/broadcast network, no privileged role required.

### Recommendation
Add an optional user-defined minimum/maximum bound field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to the `expected` field already present in `ExchangeTransactionContract`), and enforce it in `ExchangeInjectActuator`/`ExchangeWithdrawActuator`'s `execute`/`doValidate` so a victim can reject execution if the computed `anotherTokenQuant` falls outside their acceptable range.

### Proof of Concept
Conceptual sequence (mirrors the Basin PoC's front-run/deposit/back-run structure), using only existing java-tron actuators:
1. Attacker observes a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) from victim targeting `exchange_id = X`, token pair (A, B).
2. Attacker submits `ExchangeTransactionContract` swapping a large amount of A for B (or vice versa) against exchange `X`, shifting `firstTokenBalance`/`secondTokenBalance` sharply, using `ExchangeCapsule.transaction` math shown above.
3. Victim's `ExchangeInjectActuator.execute` runs next, computing `anotherTokenQuant` from the now-skewed ratio (lines 71-83 of `ExchangeInjectActuator.java`), causing the victim to deposit a disproportionate amount of token B (or, for withdraw, to receive a disproportionately small amount of B).
4. Attacker submits a second `ExchangeTransactionContract` reversing the initial swap, restoring the ratio and realizing a profit extracted from the victim's mis-priced inject/withdraw, exactly as the original Basin PoC front-runs `addLiquidity` and back-runs with `removeLiquidityOneToken`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-83)
```java
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            secondTokenBalance, tokenQuant), firstTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, tokenQuant),
            addExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = floorDiv(multiplyExact(
            firstTokenBalance, tokenQuant), secondTokenBalance);
        exchangeCapsule.setBalance(addExact(firstTokenBalance, anotherTokenQuant),
            addExact(secondTokenBalance, tokenQuant));
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-256)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenID = secondTokenID;
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divide(bigFirstTokenBalance).longValueExact();
      newTokenBalance = addExact(firstTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(secondTokenBalance, anotherTokenQuant);
    } else {
      anotherTokenID = firstTokenID;
      anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
          .divide(bigSecondTokenBalance).longValueExact();
      newTokenBalance = addExact(secondTokenBalance, tokenQuant);
      newAnotherTokenBalance = addExact(firstTokenBalance, anotherTokenQuant);
    }

    if (anotherTokenQuant <= 0) {
      throw new ContractValidateException("the calculated token quant  must be greater than 0");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (newTokenBalance > balanceLimit || newAnotherTokenBalance > balanceLimit) {
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

    if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(anotherTokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(anotherTokenID, anotherTokenQuant, dynamicStore)) {
        throw new ContractValidateException("another token balance is not enough");
      }
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-89)
```java
      BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
      BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
      BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
        anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, tokenQuant),
            subtractExact(secondTokenBalance, anotherTokenQuant));
      } else {
        anotherTokenID = firstTokenID;
        anotherTokenQuant = bigFirstTokenBalance.multiply(bigTokenQuant)
            .divide(bigSecondTokenBalance).longValueExact();
        exchangeCapsule.setBalance(subtractExact(firstTokenBalance, anotherTokenQuant),
            subtractExact(secondTokenBalance, tokenQuant));
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-243)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
      }
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

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
