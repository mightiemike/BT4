## Finding

### Title
`ExchangeWithdrawActuator` and `ExchangeInjectActuator` lack any slippage/bound protection, allowing front-run trades to steal AMM pool value from the exchange creator - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
The `ExchangeTransactionContract`/`ExchangeTransactionActuator` path correctly implements a slippage guard: the caller supplies an `expected` amount and `doValidate()` rejects the trade if the computed `anotherTokenQuant` is less than that expected minimum [1](#0-0) . By contrast, `ExchangeWithdrawContract` and `ExchangeInjectContract` carry no equivalent bound parameter at all - `anotherTokenQuant` is derived purely from the live pool ratio at validate/execute time, with no user-supplied minimum (for withdraw) or maximum (for inject) to protect against the ratio being manipulated between transaction submission and inclusion.

### Finding Description
In `ExchangeWithdrawActuator.doValidate()`/`execute()`, the creator specifies only `tokenID` and `tokenQuant`; the corresponding `anotherTokenQuant` returned to them is computed on the fly from `firstTokenBalance`/`secondTokenBalance` at execution time [2](#0-1) . There is no user-controlled lower bound analogous to `minSharesOut`/`tokenExpected` to guarantee the withdrawer receives at least a minimum amount of the other-side token.

Symmetrically, `ExchangeInjectActuator.doValidate()`/`execute()` computes `anotherTokenQuant` (the amount of the other token the creator must also deposit) purely from the current pool ratio [3](#0-2) , with no user-supplied maximum bound to protect the injector from being forced to deposit far more of the paired token than intended if the ratio shifts before execution.

Any unprivileged account can submit `ExchangeTransactionContract` trades against the same exchange pool to shift `firstTokenBalance`/`secondTokenBalance` before the victim's `ExchangeWithdraw`/`ExchangeInject` transaction is packed by a block producer, since transaction ordering within a block/mempool is not controlled by the sender. This is the same class of defect as the ERC4626 report: an AMM-style operation whose payout/cost is determined at execution time with no bound check to protect the caller from adverse price movement between submission and execution - except here the bound check is entirely absent rather than merely inverted.

### Impact Explanation
An exchange creator withdrawing liquidity can receive a smaller-than-expected amount of the paired token if the pool ratio is shifted against them right before their withdrawal executes, and a creator injecting liquidity can be forced to spend far more of the paired token than they intended for the same reason. Because `ExchangeWithdrawActuator`/`ExchangeInjectActuator` restrict the caller to the exchange's `creatorAddress` [4](#0-3) , only the pool's own creator is directly exposed, but the attacker triggering the price shift is any unprivileged account broadcasting ordinary `ExchangeTransactionContract` trades - no privileged role or leaked key is required on the attacker side.

### Likelihood Explanation
Exploitation only requires observing a pending `ExchangeWithdrawContract`/`ExchangeInjectContract` in the mempool (or anticipating one) and broadcasting a competing `ExchangeTransactionContract` trade with sufficient fee/timing priority to land first in the same block, which is a low-cost, permissionless action available to any account holding the relevant tokens/TRX.

### Recommendation
Add an explicit bound parameter to `ExchangeWithdrawContract` and `ExchangeInjectContract` (e.g., `expected`/`minAnotherTokenQuant` for withdraw, `maxAnotherTokenQuant` for inject), and validate it the same way `ExchangeTransactionActuator` validates `tokenExpected` at [1](#0-0) , rejecting the transaction in `doValidate()` if the computed `anotherTokenQuant` falls outside the caller's specified bound.

### Proof of Concept
1. Attacker monitors the mempool for an exchange creator's pending `ExchangeWithdrawContract` on exchange `X` (token A / token B pool).
2. Attacker broadcasts an `ExchangeTransactionContract` that buys a large amount of token A from the pool, shifting the A/B ratio unfavorably for a subsequent withdrawer of A.
3. Attacker's trade is confirmed in the same or an earlier block (via fee bump/priority).
4. Victim's `ExchangeWithdrawContract` executes against the now-skewed pool in `ExchangeWithdrawActuator.execute()` [2](#0-1) , and the victim receives materially less of token B than they would have received against the pre-attack ratio, with no on-chain check available to prevent or detect this since no minimum bound exists in the contract.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
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
