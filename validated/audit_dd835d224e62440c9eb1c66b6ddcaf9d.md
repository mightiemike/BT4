### Title
Governance-controlled `exchangeBalanceLimit` can permanently halt one-sided trading/injection in the TRX Exchange (Bancor-style AMM) - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java)

### Summary
The Comet report describes an immutable `targetReserves` value that gates `buyCollateral`: once reserves reach that level, the sell path is blocked, and if the threshold is poorly sized the protocol can get stuck holding assets it cannot dispose of. java-tron's TRX Exchange (`ExchangeCreateContract` / `ExchangeInjectContract` / `ExchangeTransactionContract`) has a directly analogous single global threshold, `dynamicStore.getExchangeBalanceLimit()`, that gates both `ExchangeInjectActuator` and `ExchangeTransactionActuator`. Any unprivileged user's ordinary trade (`ExchangeTransactionContract`) that would push either token's pool balance above this limit is rejected, and there is no complementary mechanism forcing balance back down except one-sided withdrawal. Because normal user activity (not a trusted role) can push a pool balance right up to the limit, the pool can become "one-way stuck" — usable for withdrawing and for trading in the direction that shrinks the capped side, but permanently unable to accept further deposits/trades that grow that side, mirroring the "protocol may end up holding collateral assets in an unwanted manner" bug class.

### Finding Description
`ExchangeTransactionActuator.doValidate()` computes the new balance of whichever token the trade increases and rejects the transaction if it would exceed the chain-wide `balanceLimit`: [1](#0-0) 

`ExchangeInjectActuator.doValidate()` applies the identical check for manual liquidity injection: [2](#0-1) 

`ExchangeCreateActuator.doValidate()` also enforces the same cap at pool-creation time: [3](#0-2) 

All three reads come from the same single global parameter, `getExchangeBalanceLimit()`, defined in `DynamicPropertiesStore`. This is a chain parameter, not a per-pool tunable — it applies uniformly to every AMM pool on the network, similar in spirit to Comet's single immutable `targetReserves` gating `buyCollateral`.

Root-cause parallel to the report:
- Comet: `targetReserves` gates `buyCollateral`; reserves can rise toward the target through normal absorptions (unprivileged liquidation flow), and once at/above target, selling collateral is blocked — the protocol is stuck holding collateral.
- java-tron Exchange: `exchangeBalanceLimit` gates `ExchangeTransactionContract` (ordinary trading, callable by any account) and `ExchangeInjectContract`. Ordinary trading activity by any unprivileged user can push a pool's `firstTokenBalance` or `secondTokenBalance` toward the limit; once at/above it, that side of the market can no longer accept further trades or injections that grow it — the pool is effectively frozen for that direction while the price on the AMM curve continues to be quoted by `ExchangeCapsule.transaction()`. [4](#0-3) 

Because the value is a single global constant (default `1_000_000_000_000_000`, confirmed by the exact error strings observed in the actuator tests, e.g. "token balance must less than 1000000000000000"), any token pair paired against a very liquid token (e.g. TRX, whose supply and typical pool sizes are large) can realistically approach this cap through organic trading volume, without any need for a malicious or privileged actor.

### Impact Explanation
When a pool's capped-side balance reaches `exchangeBalanceLimit`:
- Users can no longer inject additional liquidity of that token (`ExchangeInjectContract` fails validation with "token balance must less than <limit>").
- Users can no longer execute trades that increase that side further (`ExchangeTransactionContract` fails validation the same way).
- The pool remains tradable only in the direction that shrinks the capped side, and is still withdrawable (`ExchangeWithdrawContract` has no such cap), so unlike Comet's `buyCollateral` block this is not a pure one-way trap for the asset itself — but it does constitute a concrete, unprivileged-user-reachable denial-of-service on core AMM functionality (an "underpriced/blocked public work" analog: the intended-open trading/injection actuators become permanently unusable in one direction for that pool) and can distort/halt price discovery for that trading pair, a state-divergence/halt style impact on the exchange subsystem.

### Likelihood Explanation
The check is reachable by any account submitting a normal `ExchangeTransactionContract` — no special permission is required, matching the "unprivileged-user analog" requirement. Given `exchangeBalanceLimit` is a single global, committee-set constant applied identically to every pool regardless of the paired token's typical liquidity, pools created against high-volume/high-value tokens are the most exposed to organically reaching the cap through everyday trading, entirely without malicious intent, which is exactly the "target set too small relative to real usage" scenario flagged in the source report.

### Recommendation
- Reconsider a single fixed, global `exchangeBalanceLimit` shared by all pools; consider a per-pool or per-token-decimal-scaled limit so it cannot be trivially reached by legitimate trading volume on high-liquidity pairs.
- Ensure the parameter can be raised via governance (`ProposalController`) faster than it can be exhausted by organic trading, and monitor/alert when pools approach the cap.
- Provide a way to rebalance a capped pool (e.g., allow injection/trade on the constrained side while blocking only if it would exceed a much higher hard ceiling, or add a symmetric "reserve/soft-limit" band similar to gradually discouraging further growth rather than a hard cliff at the same global threshold used at pool creation).

### Proof of Concept
1. Create an exchange pool (`ExchangeCreateContract`) with `firstTokenId = TRX`, sizing `firstTokenBalance` reasonably below `exchangeBalanceLimit`.
2. Have unprivileged users repeatedly submit `ExchangeTransactionContract` trades that sell the second token for TRX (increasing `firstTokenBalance`), or submit `ExchangeInjectContract` with the first token, using ordinary trading activity.
3. Each trade succeeds while `firstTokenBalance + tokenQuant <= exchangeBalanceLimit`, as enforced in [1](#0-0) 
4. Once `firstTokenBalance` reaches `exchangeBalanceLimit`, any further trade or injection that would increase it — even from an entirely different, unrelated unprivileged user — fails validation with `"token balance must less than " + balanceLimit"`, permanently freezing that side of the pool for injections/growth-direction trades until governance raises the limit or the balance is later reduced by opposite-direction trades/withdrawals.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L199-205)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-236)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L205-208)
```java
    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    if (firstTokenBalance > balanceLimit || secondTokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
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
