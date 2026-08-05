### Title
Missing slippage/price protection in `ExchangeWithdrawActuator` allows front-running to reduce Bancor pool creator's withdrawal proceeds - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Java-tron's Bancor-style `Exchange`/`ExchangeV2` mechanism exposes two related operations on a token pair pool: `ExchangeTransactionContract` (a trade, executed by `ExchangeTransactionActuator`) and `ExchangeWithdrawContract` (an LP-style withdrawal of the pool's underlying token balances, executed by `ExchangeWithdrawActuator`, callable only by the pool's creator). The trade path has explicit slippage protection via an `expected` field, while the withdrawal path has none — mirroring exactly the HydraDX asymmetry between `remove_liquidity` (has `safe_withdrawal`/price-difference guard) and `withdraw_protocol_liquidity` (lacks it).

### Finding Description
`ExchangeTransactionActuator.doValidate()` requires the caller to supply `tokenExpected` and rejects the trade if the computed `anotherTokenQuant` is below it: [1](#0-0) 

In contrast, `ExchangeWithdrawActuator.doValidate()` computes `anotherTokenQuant` purely from the *current* pool ratio (`firstTokenBalance`/`secondTokenBalance`) at validation/execution time, with only a "Not precise enough" ratio-rounding check — there is no caller-supplied minimum/expected amount and no price-divergence guard: [2](#0-1) 

Because the withdrawal amount for the "other" token is derived from the live pool balances rather than a value fixed by the creator at submission time, any transaction that changes `firstTokenBalance`/`secondTokenBalance` between the time the creator signs `ExchangeWithdrawContract` and the time it's actually included/executed (e.g., a preceding `ExchangeTransactionContract` trade, or another `ExchangeInjectContract`/`ExchangeWithdrawContract`) changes the ratio used in `execute()`: [3](#0-2) 

This is the same root cause as the HydraDX finding: a liquidity-removal function that lacks the price/slippage check present in its sibling swap/trade function, letting a third party manipulate the pool state immediately before the withdrawal executes to shift the payout ratio against the withdrawer.

### Impact Explanation
The exchange creator withdrawing pool liquidity can receive a smaller amount of the "other" token than they expected when they signed the transaction, because the computation in `execute()` uses whatever pool ratio exists at execution time rather than a value bound to the creator's intent. Since `Commons.getExchangeStoreFinal(...)` pools are shared, permissionless-tradeable Bancor pairs (anyone can call `ExchangeTransactionContract` against the same `exchangeId`), an attacker who observes a pending `ExchangeWithdrawContract` in the mempool can submit a trade that shifts the balance ratio just before the withdrawal executes, causing the creator to receive an unexpectedly unfavorable split of `firstTokenBalance`/`secondTokenBalance` upon withdrawal. This is an accounting/settlement impact on the withdrawing party, directly analogous to the confirmed HydraDX Medium finding.

### Likelihood Explanation
Exploitation requires an attacker to monitor the mempool for a `ExchangeWithdrawContract` and race a trade transaction ahead of it in the same or an earlier block — feasible for any block producer or a well-connected node, similar to the front-running assumption in the original report. Only the exchange's creator can call `ExchangeWithdrawActuator` (enforced by the "not creator" check), so this is not exploitable by a fully arbitrary attacker against arbitrary victims, but it is not privileged/trusted-role code — the "creator" is simply whichever ordinary account created the pool via `ExchangeCreateContract`, an unprivileged, permissionless action.

### Recommendation
Add a caller-supplied minimum-amount parameter to `ExchangeWithdrawContract` (mirroring `ExchangeTransactionContract.expected`) and enforce it in `ExchangeWithdrawActuator.doValidate()`/`execute()`, rejecting the withdrawal if the computed `anotherTokenQuant` falls below the caller's specified minimum.

### Proof of Concept
1. Creator submits `ExchangeWithdrawContract` for `exchangeId` X, withdrawing `tokenQuant` of `firstTokenID`, expecting `anotherTokenQuant` computed from the pool's current `firstTokenBalance`/`secondTokenBalance` ratio.
2. Before this transaction executes, an attacker submits `ExchangeTransactionContract` against the same `exchangeId`, shifting `firstTokenBalance`/`secondTokenBalance` via `ExchangeCapsule.transaction(...)`.
3. When the creator's withdrawal executes, `ExchangeWithdrawActuator.execute()` recomputes `anotherTokenQuant` using the now-manipulated balances: [4](#0-3) 
   yielding a payout ratio worse than what the creator anticipated, with no `expected`/minimum check to block it — unlike the protected `ExchangeTransactionActuator` path.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L185-243)
```java
    byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
    byte[] secondTokenID = exchangeCapsule.getSecondTokenId();
    long firstTokenBalance = exchangeCapsule.getFirstTokenBalance();
    long secondTokenBalance = exchangeCapsule.getSecondTokenBalance();

    byte[] tokenID = contract.getTokenId().toByteArray();
    long tokenQuant = contract.getQuant();

    long anotherTokenQuant;

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }

    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("withdraw token quant must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    BigDecimal bigFirstTokenBalance = new BigDecimal(String.valueOf(firstTokenBalance));
    BigDecimal bigSecondTokenBalance = new BigDecimal(String.valueOf(secondTokenBalance));
    BigDecimal bigTokenQuant = new BigDecimal(String.valueOf(tokenQuant));
    final boolean allowHarden = allowHarden();
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
