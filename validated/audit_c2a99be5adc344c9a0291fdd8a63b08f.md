### Title
Missing Slippage Protection in `ExchangeInjectActuator` Liquidity Injection - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
Java-tron's built-in TRX/TRC10 bancor-style Exchange contains a liquidity-injection path (`ExchangeInjectContract`) analogous to Uniswap's `addLiquidity`. Unlike `ExchangeTransactionContract`, which has an `expected` field to bound slippage on token swaps, `ExchangeInjectContract` has no minimum/maximum bound on the counterpart token amount that will be computed and deducted at execution time. This mirrors the reported Uniswap `addLiquidity` issue where amountAMin/amountBMin were hardcoded to `0`.

### Finding Description
`ExchangeInjectContract` only carries `owner_address`, `exchange_id`, `token_id`, and `quant` — no expected/minimum counterpart amount field. [1](#0-0) 

In `ExchangeInjectActuator.doValidate()`, the amount of the other token (`anotherTokenQuant`) that will be pulled from the caller's balance is computed purely from the *current* on-chain pool ratio (`firstTokenBalance`/`secondTokenBalance`) at validation/execution time, with no caller-supplied bound to reject unfavorable ratios: [2](#0-1) 

The same unconstrained calculation is repeated in `execute()`, and the resulting `anotherTokenQuant` is unconditionally deducted from the account: [3](#0-2) 

By contrast, `ExchangeTransactionActuator` (the swap path) explicitly validates a caller-supplied `tokenExpected` against the computed output and reverts if unmet — the exact "slippage tolerance" pattern the report recommends, which is conspicuously absent from the inject path: [4](#0-3) 

The pool ratio can be moved between the time an `ExchangeInjectContract` transaction is signed/broadcast and the block in which it is packed, because any other account can submit an `ExchangeTransactionContract` swap that shifts `firstTokenBalance`/`secondTokenBalance` in the interim (or a malicious block producer can reorder transactions within a block). Since injection has no min-bound, the injecting account has no way to cap the resulting `anotherTokenQuant` deduction, exposing them to unbounded slippage/economic loss — the direct analog of the `addLiquidity(amountAMin=0, amountBMin=0)` finding.

### Impact Explanation
The exchange's liquidity provider (the exchange creator, who is not a privileged system role but merely the user who created the pool via `ExchangeCreateContract`) can have significantly more of the "other" token deducted than expected at the time they signed the transaction, if the pool ratio shifts due to intervening swaps or transaction reordering. This is a concrete on-chain accounting/economic-loss impact (excess token deduction relative to the price the user intended), not merely theoretical, since the ratio-dependent, unbounded deduction executes atomically with no reject path for the injector.

### Likelihood Explanation
Likelihood is moderate to high: any account can submit `ExchangeTransactionContract` swaps against the same `exchange_id`, and TRON's transaction packing (by super representatives) is not guaranteed FIFO, making ratio manipulation between signing and inclusion straightforward for a party watching the mempool, or unavoidable simply due to natural trading activity on active exchanges.

### Recommendation
Add a caller-supplied bound field (e.g., `another_token_expected` / min or max acceptable counterpart quantity) to `ExchangeInjectContract`, and validate it in `ExchangeInjectActuator.doValidate()`/`execute()` analogous to the `expected` check already present in `ExchangeTransactionActuator`, rejecting the transaction if the computed `anotherTokenQuant` falls outside the caller's tolerance.

### Proof of Concept
1. User A creates/holds a liquidity position and submits `ExchangeInjectContract` for `exchange_id=X`, `token_id=firstToken`, `quant=Q`, expecting `anotherTokenQuant ≈ R` based on the pool ratio observed when signing.
2. Before this transaction is packed into a block, User B submits an `ExchangeTransactionContract` swap against the same exchange that shifts `firstTokenBalance`/`secondTokenBalance` significantly (within B's own `expected` slippage bound).
3. User A's `ExchangeInjectContract` executes using the now-shifted ratio in `ExchangeInjectActuator` at [5](#0-4) , deducting a substantially different `anotherTokenQuant` than User A intended, with no on-chain mechanism for User A to have capped this exposure.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-99)
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

      long newBalance = subtractExact(accountCapsule.getBalance(), calcFee());
      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, tokenQuant));
      } else {
        accountCapsule.reduceAssetAmountV2(tokenID, tokenQuant, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(anotherTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, anotherTokenQuant));
      } else {
        accountCapsule
            .reduceAssetAmountV2(anotherTokenID, anotherTokenQuant, dynamicStore, assetIssueStore);
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L209-231)
```java
    BigInteger bigFirstTokenBalance = new BigInteger(String.valueOf(firstTokenBalance));
    BigInteger bigSecondTokenBalance = new BigInteger(String.valueOf(secondTokenBalance));
    BigInteger bigTokenQuant = new BigInteger(String.valueOf(tokenQuant));
    long newTokenBalance;
    long newAnotherTokenBalance;

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
