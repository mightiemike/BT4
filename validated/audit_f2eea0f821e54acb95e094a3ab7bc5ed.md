Confirmed: `ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `token_id` and `quant` — no user-supplied minimum/expected output field, unlike `ExchangeTransactionContract`, which has an `expected` field checked in `ExchangeTransactionActuator.doValidate()`. [1](#0-0) 

This means `ExchangeInjectActuator` and `ExchangeWithdrawActuator` both compute the counter-asset amount purely from the current on-chain pool ratio at execution time, with no slippage bound supplied or enforced by the caller. [2](#0-1) [3](#0-2) 

### Title
Sandwichable TRC10 Exchange inject/withdraw due to missing slippage protection on pool-ratio-derived asset amounts - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
The TRC10 `Exchange` contracts (`ExchangeInjectContract` and `ExchangeWithdrawContract`) let any account inject or withdraw liquidity from a bancor-style AMM pool. Both actuators compute the paired-asset amount (`anotherTokenQuant`) strictly from the pool's current on-chain balances at execution time, with no user-supplied minimum-output/maximum-slippage parameter to bound the result, unlike the sibling `ExchangeTransactionContract`, which does carry an `expected` field validated in `ExchangeTransactionActuator`.

### Finding Description
`ExchangeInjectActuator.execute()` computes the second-token amount as a direct proportion of the current pool balances: [4](#0-3) 

and its validator only checks the balance-limit invariant, not any caller-provided bound on `anotherTokenQuant`: [5](#0-4) 

Similarly, `ExchangeWithdrawActuator.execute()` derives the counter-asset payout purely from the spot ratio of `firstTokenBalance`/`secondTokenBalance` at execution time: [6](#0-5) 

Because Tron blocks include many transactions and the transaction order within a block/producer window can be influenced (transactions are broadcast publicly and pending in the mempool before being packed), an attacker observing a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction can front-run it with an `ExchangeTransactionContract` trade that skews `firstTokenBalance`/`secondTokenBalance`, then back-run it with an opposite trade to restore the ratio and pocket the difference — the same "use of spot AMM price without slippage bound" root cause as the external report's `_calculatePortalRepayment`/`swapFromLocalAssetIfNeededForExactOut` flow, which also derived a critical amount purely from spot pool state with no independent bound. This directly harms the LP/injector/withdrawer, causing them to receive a manipulated (unfavorable) ratio of assets, and enables extraction of value from the pool by the attacker across sandwiched inject/withdraw operations.

### Impact Explanation
This is a real, reachable path from a broadcast transaction (anyone can submit `ExchangeInjectContract`/`ExchangeWithdrawContract`) leading to asset/accounting corruption for the sandwiched party: the injector deposits at a manipulated ratio (locking in a bad price for the LP-like operation) or the withdrawer receives less of the counter-asset than the fair pool state would produce. Because the pool is on-chain TRX/TRC10 value, losses are real economic losses, matching the "asset or accounting corruption" acceptance criterion. However, the amounts involved are bounded by TRC10 exchange liquidity (typically much smaller and less liquid than DeFi AMMs), and the balance-limit checks (`getExchangeBalanceLimit`) cap pool sizes, limiting the blast radius relative to the original Connext report (which affected cross-chain bridge repayments).

### Likelihood Explanation
Exploitation requires an attacker to observe a pending inject/withdraw transaction and sandwich it with two `ExchangeTransactionContract` trades in the same or adjacent blocks. TRON's public mempool and block production model make transaction observation and ordering feasible for a well-positioned attacker (e.g., colluding with or being a super representative, or simply being fast enough with normal broadcast/relay). Given TRC10 Exchange usage has historically been low-volume, the practical incentive is lower than for the original Connext bridge (which could accrue "millions" as claimed in the source report), but the code-level vulnerability pattern is identical and always exploitable when profitable.

### Recommendation
Add an `expected`/minimum-output (or maximum-input) field to `ExchangeInjectContract` and `ExchangeWithdrawContract`, mirroring `ExchangeTransactionContract.expected`, and validate it in `ExchangeInjectActuator.doValidate()` / `ExchangeWithdrawActuator.doValidate()` before computing/committing `anotherTokenQuant`, so callers can bound acceptable slippage the same way exchange trades already do.

### Proof of Concept
1. Attacker monitors the mempool for a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) transaction from a victim for exchange pool `P` (tokens A/B).
2. Attacker submits an `ExchangeTransactionContract` trade that shifts `P`'s A/B balance ratio unfavorably for the victim's pending operation, ensuring it lands before the victim's transaction (front-run).
3. Victim's `ExchangeInjectActuator`/`ExchangeWithdrawActuator` executes using the now-skewed `firstTokenBalance`/`secondTokenBalance` in the proportional computation at [2](#0-1)  (or the withdraw equivalent), locking in an unfavorable `anotherTokenQuant` for the victim with no caller-supplied bound to reject the trade.
4. Attacker submits a reverse `ExchangeTransactionContract` trade (back-run) to restore the pool ratio and realize profit extracted from the victim's inject/withdraw operation.

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L17-29)
```text
message ExchangeInjectContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}

message ExchangeWithdrawContract {
  bytes owner_address = 1;
  int64 exchange_id = 2;
  bytes token_id = 3;
  int64 quant = 4;
}
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L60-83)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
      long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
      long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L215-231)
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
