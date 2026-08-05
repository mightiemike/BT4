### Title
Missing slippage protection in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` allows sandwich-attack fund loss - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
java-tron's Bancor-style token `Exchange` mechanism computes the counter-token amount for liquidity injection and withdrawal from the exchange's live on-chain balances at execution time, with no user-supplied minimum/maximum bound. Unlike the swap path (`ExchangeTransactionActuator`), which enforces a caller-supplied `expected` amount, the inject/withdraw paths have no such protection, so the ratio can be moved against the caller between transaction submission and inclusion, causing the caller to give away more value or receive less than intended — the same "no slippage bound on a rate that depends on mutable pool state" root cause as the Teller Finance report.

### Finding Description
`ExchangeInjectActuator.doValidate()`/`execute()` derives `anotherTokenQuant` purely from the exchange's current `firstTokenBalance`/`secondTokenBalance` ratio and the caller's `tokenQuant`, with no bound supplied by the caller: [1](#0-0) 

Likewise `ExchangeWithdrawActuator.execute()` recomputes `anotherTokenQuant` from the live balances at execution time and immediately mutates account/exchange state with no minimum-out check: [2](#0-1) 

By contrast, `ExchangeTransactionActuator` explicitly requires and enforces a caller-supplied minimum (`tokenExpected`) against the freshly computed `anotherTokenQuant`, rejecting the transaction if the result is worse than expected: [3](#0-2) 

This asymmetry is the same bug class as the Teller `LenderCommitmentGroup_Smart` report: a rate/ratio derived from live pool state is used to mint/settle value without a caller-enforced bound, so any other party who can move that ratio inside the same block (here, via ordinary `ExchangeTransactionContract` swaps, which any unprivileged account may submit against the same exchange) can sandwich the inject/withdraw transaction and force the counterparty into an unfavorable exchange rate — a classic slippage/MEV extraction, not merely a self-inflicted risk, since it is triggered by a third party's trades landing before the victim's inject/withdraw transaction in the same block.

### Impact Explanation
An attacker (any unprivileged account, since anyone can submit `ExchangeTransactionContract` swaps against a public exchange pool) can front-run a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction with swaps that skew `firstTokenBalance`/`secondTokenBalance`, then back-run it to restore the ratio, extracting value from the injector/withdrawer's transaction. This is a concrete on-chain fund-loss/accounting impact: the victim either injects for less counter-value credited into the pool or withdraws for less counter-token than the pre-attack ratio implied, with the difference captured by the attacker's sandwich trades — matching the "underpriced" settlement class called out in the validation criteria.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to observe a pending inject/withdraw transaction (e.g., in the mempool or by controlling block production ordering) and execute swap transactions before and after it within the same block window. This is readily achievable by any account holding the relevant token/TRX, with no privileged access needed, since `ExchangeTransactionContract` is open to any account holding sufficient balance in either token of the pool.

### Recommendation
Add caller-supplied slippage bounds to `ExchangeInjectContract` and `ExchangeWithdrawContract` (e.g., a `minAnotherTokenQuant` for inject, and a `minAnotherTokenQuant`/`maxTokenQuant` for withdraw), and enforce them in `ExchangeInjectActuator.doValidate()`/`ExchangeWithdrawActuator.doValidate()` the same way `ExchangeTransactionActuator` already enforces `tokenExpected` against the computed `anotherTokenQuant`.

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) transaction from victim V targeting exchange E with balances `(firstTokenBalance, secondTokenBalance)`.
2. Attacker submits an `ExchangeTransactionContract` swap that shifts the ratio unfavorably for V's pending operation (e.g., increases the token V is injecting relative to the token V expects back), landing before V's transaction in the same block.
3. V's `ExchangeInjectActuator`/`ExchangeWithdrawActuator` executes against the now-skewed balances (see `ExchangeInjectActuator.java:215-227` / `ExchangeWithdrawActuator.java:74-89`), producing an `anotherTokenQuant` worse than what V expected when signing the transaction — with no check to reject it, unlike the `tokenExpected` guard present in `ExchangeTransactionActuator.java:217-221`.
4. Attacker submits a second `ExchangeTransactionContract` swap restoring the ratio, realizing a profit equal to the value extracted from V's inject/withdraw.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-227)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
