I have enough evidence to establish the analog. The java-tron `Exchange` module (a bancor-curve TRC10↔TRX pool) has the exact same trust/design pattern as sudoswap's `LSSVMPair`: an unprivileged "creator" account controls pool reserves and can instantly shift the effective exchange rate, while a counterparty's trade only has a one-sided `expected` (minimum-output) slippage guard.

### Title
Exchange creator can front-run `ExchangeTransactionContract` trades via `ExchangeInjectContract`/`ExchangeWithdrawContract` to skew price within a victim's slippage tolerance - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java, ExchangeInjectActuator.java, ExchangeTransactionActuator.java)

### Summary
Any account that creates a TRC10↔TRX bancor-style pool via `ExchangeCreateContract` becomes its `creator` and can call `ExchangeInjectActuator`/`ExchangeWithdrawActuator` at any time to instantly change the pool's `firstTokenBalance`/`secondTokenBalance` ratio — i.e., the effective price — exactly analogous to a malicious `LSSVMPair` owner calling `changeSpotPrice()`/`changeDelta()`. A trader submitting `ExchangeTransactionContract` is protected only by a single-sided minimum-output check (`expected`), not by an "as-of" price commitment, so the creator can reorder/front-run to shift price against the trader while staying within the trader's tolerance.

### Finding Description
`ExchangeInjectActuator.execute()` and `ExchangeWithdrawActuator.execute()` let only the exchange `creatorAddress` change the pool balances instantly and atomically: [1](#0-0) 
This mirrors the report's "owner" role in `LSSVMPair`, since exchange creation itself is unprivileged (any account can call `ExchangeCreateContract`).

A trader's `ExchangeTransactionContract` computes the actual output at **execution time**, using whatever pool balances exist at that moment, via `ExchangeCapsule.transaction()`: [2](#0-1) 
The only protection is a lower-bound check against the caller-supplied `expected` value, both at validation and (implicitly assumed unchanged) at execution: [3](#0-2) 

This is functionally identical to `swapNFTsForToken()`'s `minExpectedTokenOutput` guard called out in the report: it bounds worst-case slippage but does nothing to prevent the pool owner from moving the price up to that exact bound immediately before the trade lands in the same block, extracting the entire tolerance band as MEV/rug value. Since transactions in a Tron block are ordered by the block-producing SR, a colluding or malicious creator/SR can guarantee their `ExchangeInjectContract`/`ExchangeWithdrawContract` executes immediately before the victim's `ExchangeTransactionContract`.

### Impact Explanation
A pool creator (or an SR colluding with one) can consistently extract value from traders up to their configured slippage tolerance on every trade, without violating any protocol invariant. Because `ExchangeWithdrawContract` lets the creator pull out most of one side of the reserve (bounded only by `getExchangeBalanceLimit` and precision checks), the resulting price swing can be large, and repeated across many trades this constitutes systematic value extraction from unprivileged counterparties — an underpriced/market-manipulation impact class, matching the "Medium" severity assigned to the original report.

### Likelihood Explanation
Likelihood is bounded by the same factor acknowledged in the original report: victims who set `expected` tightly are largely protected, and this is an already-known, accepted design tradeoff of AMM/creator-controlled pools (same acknowledgement pattern as "Sudorandom Labs: Acknowledged, callers should use `maxInput`/`minOutput`"). It requires the creator (or a colluding block producer) to actively watch the mempool and act within the same block, which is realistic for any active exchange creator given java-tron's short block times and public mempool.

### Recommendation
Introduce a commit-delay mechanism for `ExchangeInjectContract`/`ExchangeWithdrawContract` similar to the report's suggested `announce()` + delayed `changeSpotPrice()` pattern: require injected/withdrawn balance changes to be announced and only take effect after a minimum number of blocks, emitting an event for transparency. Alternatively/additionally, consider adding an "expected pool state" or maximum price-impact parameter to `ExchangeTransactionContract` so traders can bound not just absolute output but the pool ratio they are trading against.

### Proof of Concept
1. Attacker creates an `Exchange` (TRC10 `X` / TRX) via `ExchangeCreateContract` and becomes `creatorAddress`.
2. Victim broadcasts `ExchangeTransactionContract` selling TRX for `X` with a loose `expected` (minimum output) matching current price.
3. Attacker observes the pending transaction in the mempool and submits `ExchangeWithdrawContract` withdrawing most of the `X` reserve (`ExchangeWithdrawActuator.doValidate`/`execute`, [4](#0-3) ), skewing `firstTokenBalance`/`secondTokenBalance` sharply in the attacker's favor, ordered to land in the same block just before the victim's transaction.
4. Victim's `ExchangeTransactionActuator.execute()` recomputes `anotherTokenQuant` off the now-skewed balances ( [2](#0-1) ); as long as the result is still ≥ `expected`, the trade succeeds at a materially worse price than the victim believed they were getting.
5. Attacker calls `ExchangeInjectContract` afterward to restore reserves, having captured the price difference.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-69)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
