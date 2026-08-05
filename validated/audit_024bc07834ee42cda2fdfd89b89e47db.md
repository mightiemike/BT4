## Title
Lack of Slippage Protection in `ExchangeWithdraw`/`ExchangeInject` Actuators - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
Java-tron's Bancor-style `Exchange` (TRC10 liquidity pool) feature exhibits the same missing-slippage-protection pattern described in the PoolTogether report. `ExchangeTransactionContract` (a swap) explicitly carries an `expected` field used to bound the output amount [1](#0-0) , and `ExchangeTransactionActuator` enforces it [2](#0-1) . However `ExchangeWithdrawContract` and `ExchangeInjectContract` carry **no** equivalent minimum/maximum bound field [3](#0-2) , so the amount of the "other" token a pool owner receives (or must pay) is computed strictly from the pool's *current* balances at execution time, exactly mirroring PrizeVault's unprotected `previewWithdraw`/`previewRedeem` ratio recomputation.

### Finding Description
`ExchangeWithdrawActuator.execute()` computes `anotherTokenQuant` from the live `firstTokenBalance`/`secondTokenBalance` ratio of the exchange pool at the moment the transaction is executed, not at the moment it was signed/submitted: [4](#0-3) 

The withdraw contract itself only carries `exchange_id`, `token_id`, and `quant` — there is no `expected`/minimum-out parameter that the caller can use to bound the outcome, unlike `ExchangeTransactionContract`: [5](#0-4) 

`ExchangeInjectActuator` has the identical structural issue: it derives `anotherTokenQuant` from the pool's current ratio with no bound supplied by the caller: [6](#0-5) 

Because any account can submit an `ExchangeTransactionContract` swap against the same pool (this is the pool's core purpose — public, unprivileged trading), the pool ratio can shift between the time a withdraw/inject transaction is broadcast and the time it is actually executed in a block (due to mempool ordering, block-producer reordering, or simply other trades landing first). The withdrawer/injector then receives an amount computed against a ratio different from the one they observed when signing the transaction — the same root cause as the PrizeVault report: a state-dependent conversion ratio applied at execution time with no client-supplied bound to protect against divergence.

Note: while only the exchange "creator" address may call `ExchangeWithdraw`/`ExchangeInject` for a given pool [7](#0-6) , this "creator" is not a privileged/trusted system role — it is simply the ordinary, unprivileged account that created that particular liquidity pool via `ExchangeCreateContract`, functionally equivalent to an LP/vault-owner acting on their own funds. This matches the "unprivileged-user" scope of the analog (comparable to a PrizeVault depositor acting on their own vault position).

### Impact Explanation
A pool creator withdrawing or injecting liquidity can receive/pay a worse-than-expected amount of the counter-token if the pool ratio moves against them due to intervening trades (from any other unprivileged account) before their transaction executes. This is a direct fund-accounting divergence between the user's expectation at signing time and the actual settlement amount — the same "user loses funds due to unprotected exchange-rate conversion" impact class as the original finding, applied to java-tron's native Bancor exchange settlement logic rather than an ERC-4626 vault.

### Likelihood Explanation
The `Exchange` feature is a legacy/lower-traffic mechanism compared to the newer order-book `Market` (which explicitly documents `buy_token_quantity` as "min to receive" [8](#0-7)  and thus is *not* vulnerable to this class), but `Exchange` contracts remain fully active and reachable on-chain. Any account can trigger `ExchangeTransactionContract` swaps against a pool immediately before a pending `ExchangeWithdraw`/`ExchangeInject` from that pool's creator executes, whether via natural mempool activity or deliberate front-running/sandwiching by a block producer or MEV actor. No special privilege is required to trigger the adverse ratio shift.

### Recommendation
Add an optional bound field to `ExchangeWithdrawContract` and `ExchangeInjectContract` (analogous to `expected` in `ExchangeTransactionContract`), e.g. a minimum `anotherTokenQuant` for withdraw and a maximum `anotherTokenQuant` for inject, and enforce it in `ExchangeWithdrawActuator`/`ExchangeInjectActuator` the same way `ExchangeTransactionActuator` enforces `expected` [2](#0-1) , allowing the transaction to fail validation rather than silently settling at an unfavorable ratio.

### Proof of Concept
1. Alice (pool creator) broadcasts `ExchangeWithdrawContract{exchange_id=X, token_id=A, quant=Q}` expecting `anotherTokenQuant = secondTokenBalance*Q/firstTokenBalance` based on the pool state she observed.
2. Before Alice's transaction is included, Bob submits `ExchangeTransactionContract` swaps against pool X (fully permitted, unprivileged operation), shifting `firstTokenBalance`/`secondTokenBalance`.
3. Alice's withdraw executes using the new (post-swap) balances at [4](#0-3) , yielding an `anotherTokenQuant` lower than what Alice expected when she signed the transaction, with no on-chain mechanism to reject the unfavorable outcome.

### Citations

**File:** Tron protobuf protocol document.md (L1384-1420)
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
    
     - message `ExchangeWithdrawContract`
    
       `owner_address`: address of owner.
    
       `exchange_id`: token pair id.
    
       `token_id`: token id to withdraw.
    
       `quant`: token amount to withdraw.
    
      ```java
      message ExchangeWithdrawContract {
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L71-83)
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
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L175-177)
```java
    if (!accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())) {
      throw new ContractValidateException("account[" + readableOwnerAddress + "] is not creator");
    }
```

**File:** protocol/src/main/protos/core/contract/market_contract.proto (L8-14)
```text
message MarketSellAssetContract {
    bytes owner_address = 1;
    bytes sell_token_id = 2;
    int64 sell_token_quantity = 3;
    bytes buy_token_id = 4;
    int64 buy_token_quantity = 5; // min to receive
}
```
