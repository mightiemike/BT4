### Title
No slippage control on `ExchangeInjectContract` liquidity injection allows front-run/back-run value extraction from exchange creator - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
`ExchangeInjectContract` — java-tron's on-chain Bancor-style liquidity-injection mechanism (analogous to Arrakis' vault `fund`/deposit) — carries only `owner_address`, `exchange_id`, `token_id`, and `quant` with **no minimum/maximum bound on the paired token amount**. The paired amount is computed at execution time from whatever the current pool ratio happens to be, exactly mirroring the unbounded-deposit root cause described in the Arrakis H-6 report.

### Finding Description
`ExchangeInjectContract` is defined with no slippage/deviation-bound field: [1](#0-0) 

Compare this to `ExchangeTransactionContract`, which does have an `expected` field enforced during validation: [2](#0-1) [3](#0-2) 

`ExchangeInjectActuator.execute` computes the paired ("another") token quantity purely from the **current on-chain pool balances at execution time**, with no bound supplied by the caller: [4](#0-3) 

The same unconstrained ratio calculation is repeated in `doValidate`: [5](#0-4) 

Only the exchange creator may call `ExchangeInject` (checked via `getCreatorAddress`), which is directly analogous to the "private vault owner" role in the Arrakis report — a single privileged depositor with no shares-based slippage metric: [6](#0-5) 

Crucially, the *attack vector* itself is fully unprivileged: any account can call `ExchangeTransactionActuator` to swap against the pool and shift `firstTokenBalance`/`secondTokenBalance` immediately before and after the creator's inject transaction is mined, exactly as Alice front-runs/back-runs Bob in the Arrakis PoC: [7](#0-6) 

The core AMM math (`ExchangeProcessor.exchange`) that determines swap output is a standard constant-product/Bancor-relay curve, so any swap immediately before the inject moves the ratio used by the inject calculation: [8](#0-7) 

### Impact Explanation
The exchange creator's `ExchangeInject` transaction is forced to inject tokens at an attacker-manipulated ratio (analogous to Bob depositing 1000 USDC:1000 DAI while the pool price is skewed to 0.51 in the report). Since injection is proportional to the ratio at execution time with no bound, an attacker can:
1. Front-run with `ExchangeTransactionContract` swap to skew `firstTokenBalance`/`secondTokenBalance`.
2. Let the creator's `ExchangeInjectContract` execute, forcing an economically unfavorable proportional deposit (`anotherTokenQuant` computed off the skewed balances).
3. Back-run with another swap to restore the ratio and extract the value donated by the mis-priced injection.

This is a direct, unbounded value-extraction path against the exchange creator's assets, with the funds flowing to/from real TRX/TRC10 asset balances (`accountCapsule.reduceAssetAmountV2`/`addAssetAmountV2`), i.e. concrete accounting loss, not theoretical. [9](#0-8) 

### Likelihood Explanation
The attack requires no special privilege: `ExchangeTransactionActuator` has zero access restriction (any account holding the sell token can call it), and the exploit only requires observing a pending `ExchangeInject` transaction in the mempool/block-production window and sandwiching it with two ordinary swap transactions. Exchange pools with any material liquidity delta between the creator's periodic injections make this practically exploitable, matching the "permissionless to exploit" reasoning that led Sherlock to raise the original Arrakis issue to High severity.

### Recommendation
Add a caller-supplied bound to `ExchangeInjectContract` (and analogously to `ExchangeWithdrawContract`, which has the identical unbounded-ratio problem), e.g. `min_another_token_quant` / `max_another_token_quant`, and enforce it in `ExchangeInjectActuator.doValidate`/`execute` the same way `ExchangeTransactionActuator` already enforces `expected` for swaps. This lets the exchange creator bound the acceptable pool ratio at inject time, preventing sandwich-style value extraction.

### Proof of Concept
1. Attacker observes creator's pending `ExchangeInjectContract` tx (`token_id = firstTokenID`, `quant = Q`) targeting `exchange_id = E`.
2. Attacker submits `ExchangeTransactionContract` selling a large amount of `secondTokenID` into pool `E`, sharply skewing `firstTokenBalance`/`secondTokenBalance` via `ExchangeCapsule.transaction` (`ExchangeInjectActuator.java:71-83`, `ExchangeCapsule.java:124-168`).
3. Creator's inject executes; `anotherTokenQuant = secondTokenBalance * Q / firstTokenBalance` is computed off the skewed balances (`ExchangeInjectActuator.java:73-76`), so the creator deposits far more/less of the paired token than intended, with no `expected`/bound field to reject this (`exchange_contract.proto:17-22`).
4. Attacker submits a reverse `ExchangeTransactionContract` swap to restore the original ratio, extracting the value the creator over-deposited, mirroring the DAI/USDC front-run/back-run numbers in the original Arrakis report.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L61-76)
```java
      byte[] firstTokenID = exchangeCapsule.getFirstTokenId();
      byte[] secondTokenID = exchangeCapsule.getSecondTokenId();

      byte[] tokenID = exchangeTransactionContract.getTokenId().toByteArray();
      long tokenQuant = exchangeTransactionContract.getQuant();

      byte[] anotherTokenID;
      long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
          dynamicStore.allowStrictMath(), allowHarden());

      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L85-99)
```java
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

**File:** chainbase/src/main/java/org/tron/core/capsule/ExchangeProcessor.java (L41-45)
```java
  @Override
  public long exchange(long sellTokenBalance, long buyTokenBalance, long sellTokenQuant) {
    long relay = exchangeToSupply(sellTokenBalance, sellTokenQuant);
    return exchangeFromSupply(buyTokenBalance, relay);
  }
```
