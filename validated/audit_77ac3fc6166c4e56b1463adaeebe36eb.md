### Title
Missing slippage/minimum-output protection in `ExchangeWithdrawContract` allows unexpected asset ratio loss on TRC10 exchange withdrawal - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeWithdrawContract`, unlike `ExchangeTransactionContract`, carries no `expected`/minimum-output field, so an exchange creator withdrawing liquidity from a TRC10 bancor-style exchange cannot bound the amount of the paired token they will receive. The counter-asset quantity is derived from the live pool ratio (`firstTokenBalance` / `secondTokenBalance`) at the time the transaction is actually processed, not at the time it was signed, so any trade that changes the pool ratio between signing and processing (including transactions ordered ahead of it in the same block) can change the payout with no on-chain check to reject it.

### Finding Description
`ExchangeTransactionContract` includes an `expected` field [1](#0-0)  that is enforced in `ExchangeTransactionActuator.doValidate()`, rejecting the trade if the computed output is less than the caller's minimum expectation [2](#0-1) .

By contrast, `ExchangeWithdrawContract` has only `owner_address`, `exchange_id`, `token_id`, and `quant` — no minimum/expected output parameter [3](#0-2) . In `ExchangeWithdrawActuator`, the paired token amount (`anotherTokenQuant`) that the withdrawer receives is computed proportionally from whatever `firstTokenBalance`/`secondTokenBalance` happen to be in the `ExchangeCapsule` at validation time, then recomputed independently again at execution time from the exchange state as it exists when the block actually executes the transaction [4](#0-3) . There is no persisted "expected minimum" carried from validate to execute, and no comparison against the value the withdrawer intended when they built/signed the transaction.

Because the exchange pool reserves (`first_token_balance` / `second_token_balance`) are mutated by ordinary, unprivileged `ExchangeTransactionContract` trades executed via the same bonding-curve `ExchangeProcessor`/`SafeExchangeProcessor` [5](#0-4) , any trade(s) ordered before the withdrawal within the same block — e.g. by an SR/packer, or simply by mempool ordering — shift the ratio at which the withdrawal is settled. This is directly analogous to the reported bug class: "operations that change the allocated collateral are potentially vulnerable to slippage" and the report explicitly calls out both user withdrawals and privileged rebalancing operations as affected. Here the withdrawal actuator is the vulnerable operation, and the "asset value" is the live exchange reserve ratio which — unlike an idealized ERC4626 vault assumed to be monotonically increasing — can move in either direction due to ordinary trading activity, with no min-out guard for the withdrawer.

### Impact Explanation
The `ExchangeWithdrawContract` is restricted to the exchange creator (`accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())`) [6](#0-5) , so this is a privileged-but-not-fully-trusted operation exactly like the "rebalancing between vaults" scenario in the report — the creator issues a withdrawal expecting a certain ratio of assets back, but is exposed to price-impact from interleaved trades they do not control, potentially receiving materially less of the paired token than anticipated, or being sandwiched by third-party trades executed just before the withdrawal lands on-chain. This is a concrete accounting/settlement-underpricing impact: the creator's withdrawal is settled at an unfavorable, attacker/market-influenced rate with no on-chain recourse, since the actuator provides no mechanism to reject the transaction if the received amount falls below a threshold.

### Likelihood Explanation
Likelihood is moderate: TRC10 bancor-exchanges are less used than TRC20/TVM DEXes today, but the mechanism is fully reachable by any exchange creator issuing a withdrawal while the exchange remains active and tradeable by third parties, and it requires no special conditions beyond ordinary trading activity or deliberate front-running/sandwiching of the withdrawal transaction (a witness/packer, or any third party racing to trade ahead of the visible pending withdrawal, can shift the ratio).

### Recommendation
Add a minimum-expected-output (or maximum-slippage-tolerance) field to `ExchangeWithdrawContract`, mirroring the `expected` field already present on `ExchangeTransactionContract`, and enforce it in `ExchangeWithdrawActuator.execute()` (not just at `validate()` time, since pool state can still change between validation and execution) — throwing/reverting the transaction if the recomputed `anotherTokenQuant` at execution time is less than the caller-specified minimum.

### Proof of Concept
1. Exchange creator holds a TRC10 exchange with `firstTokenBalance = A`, `secondTokenBalance = B`.
2. Creator signs and broadcasts `ExchangeWithdrawContract{token_id=first, quant=Q}`, expecting `anotherTokenQuant ≈ B*Q/A` based on the currently observed reserves.
3. Before this transaction is packed/executed, one or more `ExchangeTransactionContract` trades execute against the same exchange (by any unprivileged user), shifting the ratio `B/A` unfavorably.
4. When the withdrawal executes, `ExchangeWithdrawActuator.execute()` recomputes `anotherTokenQuant` from the now-different `firstTokenBalance`/`secondTokenBalance` [7](#0-6) , delivering a smaller (or otherwise unintended) amount of the paired token, with no check against the creator's original expectation and no failure path to prevent settlement at the unfavorable rate.

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L24-29)
```text
message ExchangeWithdrawContract {
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L63-89)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L181-183)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
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
