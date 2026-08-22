### Title
Exchange creator can front-run trader's `ExchangeTransactionContract` by injecting/withdrawing liquidity to manipulate price with no minimum-output protection - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java])

### Summary
The TRC10 bancor-style exchange pools created via `ExchangeCreateContract` allow the pool's creator to unilaterally and instantly change the pool's price ratio through `ExchangeInjectContract`/`ExchangeWithdrawContract` at any time, with no bound on how much the price can move in a single call. Because `ExchangeTransactionContract` (the trade contract) accepts only a `quant` of the token being sold with no minimum-expected-output/slippage field, a malicious (but otherwise unprivileged) pool creator can front-run a pending trade transaction by injecting or withdrawing liquidity to shift the exchange rate against the trader, then let the trade execute at the worse price, and optionally reverse the injection/withdrawal afterward — extracting value from the trader. This is the same bug class as the reported `setFeeRate`/`setLocalFeeRate` issue: an actor who controls a pricing parameter with no upper/lower bound can move it to their advantage immediately before a counterparty's already-agreed trade executes.

### Finding Description
`ExchangeCreateActuator` lets any account become the "creator" of an exchange pool (a normal, permissionless role, not a network-privileged committee/witness) that holds `firstTokenBalance`/`secondTokenBalance` used for bancor-formula pricing in `ExchangeCapsule.transaction()` [1](#0-0) .

The creator alone is authorized to call `ExchangeInjectContract` and `ExchangeWithdrawContract`, both of which are validated only by checking `accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())` — there is no rate limit, cooldown, or maximum single-call price-impact bound [2](#0-1) [3](#0-2) . Both actuators immediately rewrite `firstTokenBalance`/`secondTokenBalance`, which directly determines the exchange rate for the next `ExchangeTransactionContract` [4](#0-3) [5](#0-4) .

Meanwhile, `ExchangeTransactionActuator.execute()` computes the trader's output solely from the pool balances at execution time via `exchangeCapsule.transaction(tokenID, tokenQuant, ...)`, and the trader's contract carries only `exchangeId`, `tokenId`, and `quant` — there is no minimum-expected-output ("slippage") field to protect the trader from the rate having moved [6](#0-5) .

Because a trader's `ExchangeTransactionContract` is broadcast and visible before being packed into a block, the exchange creator can observe it and insert an `ExchangeInjectContract`/`ExchangeWithdrawContract` immediately before it (front-running via mempool ordering or same-block sequencing), moving the pool ratio unfavorably for the trader, letting the trade execute at the worse rate, and then reversing the injection/withdrawal afterward to restore the pool — capturing the difference exactly as described in the reported `setFeeRate` bug class, except the "fee rate" here is the pool's price ratio, and the "owner" is the pool creator.

### Impact Explanation
High for affected traders: a malicious pool creator can extract value from any counterparty who submits a trade against their pool, similar in effect to the reported fee-rate front-run — the trader receives less than the amount implied by the rate they observed when signing their transaction, with no on-chain mechanism (slippage bound) to prevent or bound the loss.

### Likelihood Explanation
Low-to-Medium: it requires the exchange creator (an unprivileged but pool-controlling role) to actively watch for incoming trades against their pool and front-run them, which is economically motivated and technically simple (no special privileges, network access, or validator status is required — any account that created the exchange can do this with ordinary broadcast transactions).

### Recommendation
Add a minimum-expected-output ("slippage tolerance") field to `ExchangeTransactionContract` and enforce it in `ExchangeTransactionActuator.execute()`/`validate()`, rejecting trades whose realized `anotherTokenQuant` falls below the trader-specified minimum. Additionally, consider bounding the maximum price impact allowed per single `ExchangeInjectContract`/`ExchangeWithdrawContract` call, or introducing a time-lock/cooldown on creator-initiated liquidity changes, so pool ratios cannot be moved instantaneously against pending trades.

### Proof of Concept
1. Attacker creates an exchange pool for tokens A/B via `ExchangeCreateContract`, becoming `creatorAddress`.
2. Victim broadcasts `ExchangeTransactionContract{exchangeId, tokenId=A, quant=X}` expecting a certain amount of B, computed off-chain from the current `firstTokenBalance`/`secondTokenBalance`.
3. Attacker, seeing the pending transaction, broadcasts `ExchangeWithdrawContract` (or `ExchangeInjectContract`) that shifts the A/B ratio unfavorably, and ensures it lands in the block before the victim's transaction (e.g., via fee/ordering or same-block sequencing) — validated only by the creator-address check in `ExchangeWithdrawActuator.doValidate()` [3](#0-2) .
4. Victim's `ExchangeTransactionContract` executes against the now-worse ratio in `ExchangeCapsule.transaction()`, with no minimum-output check to reject the unfavorable trade [7](#0-6) .
5. Attacker submits a follow-up `ExchangeInjectContract`/`ExchangeWithdrawContract` to restore the pool balance, retaining the difference extracted from the victim.

### Citations

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeCapsule.java (L124-158)
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
```

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L77-89)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
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
