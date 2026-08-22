### Title
No slippage/minimum-output protection when injecting liquidity into an `Exchange` pool - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
`ExchangeInjectActuator` (the `ExchangeInjectContract` handler for java-tron's Bancor-style TRC10 `Exchange`/`ExchangeV2` liquidity pools) computes the counterpart token amount to debit from the caller purely from the pool's on-chain ratio *at execution time*, and debits that exact amount without any user-supplied bound. This mirrors the reported TridentRouter class of bug: a liquidity-add operation whose required "other side" amount is priced at the pool's current spot ratio, with no client-supplied min/max to protect the sender against ratio drift between submission and inclusion.

### Finding Description
`ExchangeInjectContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` [1](#0-0)  — there is no field for a minimum or maximum acceptable amount of the other token.

In both `doValidate` and `execute`, the actuator reads the exchange's current `firstTokenBalance`/`secondTokenBalance` and derives `anotherTokenQuant` as `otherBalance * tokenQuant / thisBalance` using the pool state present when the transaction is processed, then immediately debits that computed amount from the account: [2](#0-1) [3](#0-2) 

Contrast this with the sibling swap actuator `ExchangeTransactionActuator`, whose contract explicitly includes an `expected` field, and whose validation reverts if the computed output falls below what the caller specified: [4](#0-3) [5](#0-4) 

`ExchangeInjectActuator` has no analogous check. Because the pool ratio can move between the time a user builds/broadcasts an `ExchangeInjectContract` and the time it is actually included in a block (e.g., an intervening `ExchangeTransactionContract` swap, another `ExchangeInjectContract`, or an `ExchangeWithdrawContract` changes `firstTokenBalance`/`secondTokenBalance`), the amount of the "other" token actually debited from the sender can differ arbitrarily from what the sender expected when they signed the transaction — this is exactly the "not providing lp at the pool's spot price" failure mode from the report, except java-tron additionally lacks any bound at all (Uniswap V2's router at least reverts via `INSUFFICIENT_A/B_AMOUNT`; here nothing stops execution from silently debiting an unfavorable amount, subject only to having sufficient balance/asset).

### Impact Explanation
A liquidity provider calling `ExchangeInjectContract` can be debited a materially different (and potentially much larger) amount of the counterpart token than anticipated when the pool ratio shifts before their transaction lands, with no mechanism to cap this exposure. In the best case the transaction simply fails validation (`balance is not enough` / `token balance is not enough`, or the derived `anotherTokenQuant <= 0` check) — a DoS/wasted-fee outcome; in the worst case it succeeds and locks the sender's assets at an unfavorable ratio at the moment of inclusion, i.e., asset/accounting loss for the liquidity provider. This is a medium-severity issue affecting any user of the TRC10 `Exchange` liquidity feature, not a privileged actor.

### Likelihood Explanation
Reachable directly by any account broadcasting an ordinary `ExchangeInjectContract` transaction — no special permissions required. Exchange pools are TRC10-token pools that can be actively traded via `ExchangeTransactionContract` by anyone concurrently, so ratio drift between broadcast and inclusion is a normal occurrence (analogous to the BTC/USD bot-front-run scenario in the report), especially under network congestion or when a block includes both a swap and an inject for the same pool.

### Recommendation
Add minimum/maximum bound fields to `ExchangeInjectContract` (e.g., `another_token_min` and/or a max quant the caller is willing to supply), and enforce them in `ExchangeInjectActuator.doValidate`/`execute` before debiting the counterpart token, mirroring the `expected` check already present in `ExchangeTransactionActuator`. Consider the same treatment for `ExchangeWithdrawActuator` if it lacks equivalent bounds (not fully verified in this pass due to iteration limits).

### Proof of Concept
1. Pool P holds `firstTokenBalance = X`, `secondTokenBalance = Y` (ratio Y/X).
2. Alice observes this ratio and broadcasts `ExchangeInjectContract{token_id=first, quant=q}`, expecting to be debited `Y*q/X` of the second token.
3. Before Alice's transaction is included, a swap (`ExchangeTransactionContract`) or another inject/withdraw changes the pool to `X'`, `Y'`.
4. When Alice's transaction executes, `ExchangeInjectActuator` recomputes `anotherTokenQuant = Y'*q/X'` [3](#0-2)  and debits that amount from Alice's account with no check against her originally-expected value — either debiting an unexpectedly large amount (loss) or failing (`token balance is not enough`) with no way for Alice to have bounded the outcome in advance.

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L17-22)
```text
message ExchangeInjectContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}
```

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L31-37)
```text
message ExchangeTransactionContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
  int64 expected = 5;
}
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L65-83)
```java
      byte[] tokenID = exchangeInjectContract.getTokenId().toByteArray();
      long tokenQuant = exchangeInjectContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-220)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
```
