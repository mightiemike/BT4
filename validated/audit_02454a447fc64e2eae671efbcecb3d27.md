### Title
No slippage/minimum-output protection for `ExchangeWithdrawContract` liquidity withdrawal - (`actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeTransactionContract` (the TRC10 Bancor-style swap) already implements the exact mitigation the Sherlock report recommends: it takes a caller-supplied `expected` (minimum-out) value and rejects the transaction if the computed output falls below it [1](#0-0) . However, `ExchangeWithdrawContract`, which removes liquidity from the same pooled reserves and returns *both* tokens at a ratio computed purely from the reserves at execution time, has no equivalent minimum-output or deadline parameter at all [2](#0-1) .

### Finding Description
`ExchangeWithdrawContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` — there is no `expected`/`min_out` field and no `deadline` field. In `ExchangeWithdrawActuator.execute()`, the counter-token amount returned to the user is computed strictly from the *current* on-chain reserve ratio at the moment of execution: [3](#0-2) 

`doValidate()` similarly recomputes the ratio at validation time but has no user-supplied bound to check it against, unlike `ExchangeTransactionActuator.doValidate()` which explicitly checks `anotherTokenQuant < tokenExpected` [1](#0-0) .

Because `ExchangeInjectContract`/`ExchangeWithdrawContract` are not subject to the fork-gated rejection that applies to `ExchangeTransactionContract` (`isExchangeTransaction()`/`rejectExchangeTransaction()` only match `ExchangeTransactionContract`) [4](#0-3) , withdraw transactions remain fully reachable by any unprivileged holder of exchange-issued balance at all times, regardless of the `allowHardenExchangeCalculation` state.

This mirrors the report's root cause precisely: the pool reserves can be moved between the time a user broadcasts their withdrawal and the time it is packed into a block (e.g., by an intervening `ExchangeTransactionContract` swap that shifts `firstTokenBalance`/`secondTokenBalance`), and the withdrawer has no way to bound the acceptable output or set a deadline — the only check is the generic transaction expiration window used by every TRON transaction type, not a swap/withdraw-specific deadline [5](#0-4) .

### Impact Explanation
A user submitting `ExchangeWithdrawContract` can receive a materially different split of the two underlying tokens than they expected if the exchange's reserve ratio is manipulated (via a sandwiching swap before/after the withdrawal) or shifts naturally between broadcast and inclusion. Since there is no `expected`/minimum-output enforcement, the withdrawal always executes at whatever ratio exists at execution time, exposing the withdrawer to unbounded, unprotected slippage on their redeemed assets — directly analogous to the M-3 finding but on the liquidity-removal path rather than the swap path.

### Likelihood Explanation
`ExchangeWithdrawContract` is always reachable by any account holding a position in an exchange pair (no privilege required), and pool reserves are mutable within the same block by ordinary `ExchangeTransactionContract`/`ExchangeInjectContract` calls from other unprivileged accounts, so the precondition (reserve state changing between submission and execution) is realistic, not merely theoretical. Likelihood is comparable to (or higher than) the original report, since here there is no partial mitigation at all (in contrast to the swap actuator, which already has the `expected` guard).

### Recommendation
Add `expected_first_token` / `expected_second_token` (minimum-out) fields—and optionally a `deadline`—to `ExchangeWithdrawContract`, and enforce them in `ExchangeWithdrawActuator.doValidate()`/`execute()` the same way `tokenExpected` is enforced in `ExchangeTransactionActuator`, so a withdrawal reverts if either returned amount falls below the caller's specified minimum.

### Proof of Concept
1. Pool P has reserves `firstTokenBalance = A`, `secondTokenBalance = B`.
2. Victim broadcasts `ExchangeWithdrawContract` withdrawing `quant` of the first token, expecting `~B*quant/A` of the second token.
3. Before the victim's tx is packed, an unprivileged actor submits `ExchangeTransactionContract` swaps that shift the pool to `A'`, `B'` such that `B'/A' << B/A`.
4. Victim's withdraw executes against `A'`/`B'` per `ExchangeWithdrawActuator.execute()` [3](#0-2) , receiving far less of the second token than anticipated, with no `expected` field to have prevented this.
5. The actor reverses the swap afterward to restore the ratio, capturing the value the victim lost — with `ExchangeWithdrawActuator` providing no on-chain mechanism to reject the unfavorable execution.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L63-90)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

      byte[] tokenID = exchangeWithdrawContract.getTokenId().toByteArray();
      long tokenQuant = exchangeWithdrawContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant;

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

**File:** framework/src/main/java/org/tron/core/db/Manager.java (L846-858)
```java
    long transactionExpiration = transactionCapsule.getExpiration();
    long headBlockTime = chainBaseManager.getHeadBlockTimeStamp();
    if (transactionCapsule.isInBlock()
        && chainBaseManager.getDynamicPropertiesStore().allowConsensusLogicOptimization()) {
      transactionCapsule.checkExpiration(chainBaseManager.getNextBlockSlotTime());
    }
    if (transactionExpiration <= headBlockTime
        || transactionExpiration > headBlockTime + Constant.MAXIMUM_TIME_UNTIL_EXPIRATION) {
      throw new TransactionExpirationException(
          String.format(
          "Transaction expiration, transaction expiration time is %d, but headBlockTime is %d",
              transactionExpiration, headBlockTime));
    }
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
