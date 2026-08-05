### Title
Missing slippage protection in `ExchangeInjectActuator` and `ExchangeWithdrawActuator` enables sandwich attacks on TRC10/TRX bancor-exchange pools - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
Java-tron implements an on-chain bancor-style AMM ("Exchange") for TRC10/TRX pairs. The swap operation, `ExchangeTransactionActuator`, correctly implements slippage protection via a user-supplied `expected` field that is validated against the freshly-computed output at execution time [1](#0-0) . However, the two other actuators that mutate pool ratios and move funds proportional to the *current* pool state at execution time — `ExchangeInjectActuator` (add liquidity) and `ExchangeWithdrawActuator` (remove liquidity) — provide no equivalent user-supplied bound. This mirrors exactly the class of bug described in the Derby report: an amount is computed from mutable state read *at execution time* instead of a caller-supplied minimum/maximum guard, making it sandwichable.

### Finding Description
`ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `owner_address`, `exchange_id`, `token_id`, and `quant` — no `expected`/limit field, unlike `ExchangeTransactionContract` which explicitly carries `expected` [2](#0-1) .

In `ExchangeInjectActuator.execute()`, the amount of the *paired* token the caller is forced to deposit (`anotherTokenQuant`) is derived purely from the pool's current balances at the moment the transaction executes: [3](#0-2) 
There is no check that `anotherTokenQuant` stays within any bound the user intended when they signed the transaction. If a block producer/attacker front-runs the inject with an `ExchangeTransactionContract` that skews the pool ratio, then back-runs it after the inject executes to restore the ratio and skim the difference, the victim is forced to deposit far more of the paired token than intended, at the manipulated price — a classic sandwich.

Symmetrically, `ExchangeWithdrawActuator.execute()`/`doValidate()` computes the amount of paired token returned to the withdrawer (`anotherTokenQuant`) from the pool ratio read at execution time, with only an internal precision/rounding tolerance check, not a user-specified minimum: [4](#0-3) 
A front-run/back-run sandwich around a withdrawal can shift the ratio at the moment of execution so the withdrawer receives materially less of the paired token than the ratio implied when they signed and broadcast the transaction, with the attacker capturing the difference.

Both actuators rely exclusively on `ExchangeCapsule` state (`firstTokenBalance`/`secondTokenBalance`) that any other account can move in the same block window via `ExchangeTransactionActuator`, `ExchangeInjectActuator`, or `ExchangeWithdrawActuator` itself, exactly as described in the report: "Slippage calculations (min out) have to be calculated outside of the swap transaction... Otherwise, it uses the already modified pool values to calculate the min out value."

### Impact Explanation
Any unprivileged account calling `exchangeinject` or `exchangewithdraw` (via the wallet API, `HttpMethed.exchangeTransaction`-style flows, or direct broadcast) can have their transaction sandwiched by a block-producer or MEV searcher who observes the pending transaction, executes a preceding `ExchangeTransactionContract` to shift `firstTokenBalance`/`secondTokenBalance`, lets the victim's inject/withdraw execute at the skewed ratio, then reverses the shift — extracting value from the victim's deposit or withdrawal. This is a direct loss-of-funds/underpriced-work-style impact on real account balances (TRX and TRC10 asset amounts), reachable by any account with funds and no special privileges.

### Likelihood Explanation
Exploitation requires only the ability to observe a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction in the mempool and to submit surrounding transactions — something a block producer (or a searcher colluding with one) can trivially do, and it requires no privileged role. TRON's Exchange feature and its pools are user-facing and permissionless to interact with (`ExchangeCreateActuator` lets anyone create pools), so any liquidity provider using inject/withdraw is exposed.

### Recommendation
Add an `expected`/limit field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (mirroring `ExchangeTransactionContract.expected`), and validate it in `ExchangeInjectActuator.doValidate()`/`execute()` and `ExchangeWithdrawActuator.doValidate()`/`execute()` against the freshly computed `anotherTokenQuant`, rejecting the transaction if the computed amount is worse than the caller's specified bound (a maximum for inject, a minimum for withdraw).

### Proof of Concept
1. Attacker observes victim's pending `ExchangeInjectContract` for `exchange_id=X`, `token_id=A`, `quant=Q`.
2. Attacker submits `ExchangeTransactionContract` selling token `B` into the pool, shrinking `secondTokenBalance` relative to `firstTokenBalance` (or vice versa), which is accepted because `ExchangeTransactionActuator` only checks the attacker's own `expected` [1](#0-0) .
3. Victim's `ExchangeInjectContract` executes next, and `anotherTokenQuant = floorDiv(secondTokenBalance * tokenQuant, firstTokenBalance)` is computed against the now-skewed balances [3](#0-2) , forcing the victim to deposit more of token `B` than they would have at the pre-attack ratio.
4. Attacker submits a reverse `ExchangeTransactionContract` restoring the ratio and pocketing the extracted value, with no validation step in the victim's inject having ever bounded `anotherTokenQuant`.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L214-243)
```java
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
