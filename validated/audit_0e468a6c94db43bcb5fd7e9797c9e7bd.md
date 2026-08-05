I now have sufficient evidence to confirm this analog.

### Title
Exchange (TRC10 Bancor-style) liquidity injection has no slippage/price-bound protection, enabling front-run/back-run manipulation of `ExchangeInjectContract` - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java)

### Summary
The reported Trident bug class concerns liquidity-provision transactions whose paired-token amount is computed from the pool's spot price and can be invalidated (or executed unfavorably) if the price moves between transaction signing and inclusion. Java-tron's `Exchange` module (its TRC10 Bancor/constant-product liquidity pool) has the exact same structural weakness in `ExchangeInjectContract`/`ExchangeInjectActuator`: the user only specifies one side's amount, and the actuator computes and unconditionally deducts the paired amount using the pool ratio at the time the transaction executes on-chain, with no user-supplied bound (min/max) to protect against ratio drift.

### Finding Description
`ExchangeInjectContract` only carries `exchange_id`, `token_id`, and `quant` — there is no field allowing the caller to bound the resulting paired amount [1](#0-0) . In `ExchangeInjectActuator.doValidate()`, the "another token" amount is derived purely from the exchange's current on-chain balances (`firstTokenBalance`/`secondTokenBalance`) at validation/execution time, using a simple ratio multiplication with no caller-supplied ceiling: [2](#0-1) 

This computed `anotherTokenQuant` is then force-deducted from the user's balance in `execute()` with no check that it matches what the user expected when they signed and broadcast the transaction: [3](#0-2) 

This differs materially from `ExchangeTransactionContract` (the swap/trade contract), which *does* include an `expected` field acting as a minimum-output slippage guard [4](#0-3) . No equivalent protection exists for liquidity injection. Because any other account can call `ExchangeTransactionContract` to shift the pool ratio (front-running/back-running the victim's pending `ExchangeInjectContract`), a witness/attacker with mempool visibility can move the ratio between the time the victim signs their inject transaction and the time it is packed into a block, causing the actuator to compute and silently withdraw a materially different (larger or smaller) amount of the second token than the victim intended.

### Impact Explanation
This is an accounting/economic-loss issue reachable by any unprivileged user who is the *creator* of the exchange pool (per `doValidate`'s creator check) [5](#0-4) , or more broadly demonstrates that the pool-ratio-dependent debit has no user-side bound at all. Unlike the Trident report where the transaction merely reverts (a griefing/availability issue), java-tron's actuator has no revert-guard for ratio drift — it will happily succeed and debit whatever amount the manipulated ratio dictates (bounded only by balance sufficiency and the global `getExchangeBalanceLimit()`), producing unexpected loss of value for the liquidity provider rather than a safe failure. This is a genuine settlement/accounting-divergence risk between what the user intended to spend and what was actually debited.

### Likelihood Explanation
Exploitation requires only the ability to observe a pending `ExchangeInjectContract` transaction and to submit an `ExchangeTransactionContract` (swap) transaction that lands in an earlier position within the same or a preceding block to shift the pool ratio — both of which are unprivileged, permissionless actions available to any account. Given java-tron's short block times and the exchange feature's continued live usage for TRC10 pairs, this is a realistically triggerable scenario, though it is limited to accounts holding the "creator" role for a given exchange (since only the pool creator may currently call inject) which narrows — but does not eliminate — the pool of affected victims.

### Recommendation
Add an optional bound field to `ExchangeInjectContract` (e.g., `expected_another_token_max` and/or `expected_another_token_min`, analogous to `ExchangeTransactionContract.expected`), and enforce it in `ExchangeInjectActuator.doValidate()`/`execute()` by rejecting the transaction if the computed `anotherTokenQuant` falls outside the caller-specified bound. This mirrors the Uniswap V2 router mitigation (`amountAMin`/`amountBMin`) referenced in the source report and ensures the injector's transaction fails safely rather than silently debiting an unintended amount.

### Proof of Concept
1. Pool P holds TokenA/TokenB at ratio 1:50000 (BTC/USD analog).
2. Creator C broadcasts `ExchangeInjectContract{token_id=TokenA, quant=1}`, expecting to also deposit ~50000 TokenB based on the ratio observed when signing.
3. Before C's transaction is packed, attacker A broadcasts an `ExchangeTransactionContract` that swaps a large amount of TokenB into TokenA, shifting the pool ratio to, say, 1:70000.
4. C's inject transaction is then processed: `doValidate()`/`execute()` recompute `anotherTokenQuant` using the *new* ratio, deducting ~70000 TokenB from C instead of the ~50000 C expected, with the transaction succeeding rather than reverting [6](#0-5) .
5. C has now provided liquidity at an unfavorable, manipulated ratio with no on-chain mechanism to have prevented or bounded this outcome.

### Citations

**File:** Tron protobuf protocol document.md (L1384-1401)
```markdown
     - message `ExchangeInjectContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to inject.
    
       `quant`: token amount to inject.
    
      ```java
      message ExchangeInjectContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
      }
      ```
```

**File:** Tron protobuf protocol document.md (L1422-1442)
```markdown
     - message `ExchangeTransactionContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to sell.
    
       `quant`: token amount to sell.
    
       `expected`: expected minimum number of tokens.
    
      ```java
      message ExchangeTransactionContract {
          bytes owner_address = 1;
          int64 exchange_id = 2;
          bytes token_id = 3;
          int64 quant = 4;
          int64 expected = 5;
      }
      ```
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L68-99)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
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
