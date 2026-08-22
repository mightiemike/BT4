This confirms the analog: only `ExchangeTransactionContract` has an `expected` field for slippage protection; `ExchangeWithdrawContract` and `ExchangeInjectContract` have no equivalent minimum-output/expected-amount protection field at all, at the protocol level.### Title
Missing minimum/expected-output (slippage) protection in `ExchangeWithdrawActuator` and `ExchangeInjectActuator` allows liquidity providers to suffer unbounded loss when withdrawing/injecting from a Bancor-style exchange pool - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`)

### Summary
Java-tron's built-in decentralized exchange (Bancor-style token pools) exposes three sibling operations reachable directly from any anonymous broadcast transaction: `ExchangeTransactionContract` (swap), `ExchangeInjectContract` (add liquidity), and `ExchangeWithdrawContract` (remove liquidity). Only the swap operation carries a user-supplied `expected` field that is validated against the actual computed output, giving the caller slippage/IL protection. The inject and withdraw operations have no such field in their protobuf messages or actuators, so a transaction broadcast against a stale/expected pool state can execute at a radically different exchange ratio with no on-chain guard, mirroring the analog report's "removeLiquidity() lacks IL protection unlike Router.removeLiquidity()" pattern.

### Finding Description
The exchange contract protobuf definitions show the asymmetry directly: [1](#0-0) 

- `ExchangeTransactionContract` includes `int64 expected = 5;`, which `ExchangeTransactionActuator.doValidate()` uses to reject the swap if the actual output falls below the user's expectation: it computes `anotherTokenQuant` via `exchangeCapsule.transaction(...)` and throws `"token required must greater than expected"` if `anotherTokenQuant < tokenExpected`. [2](#0-1) 

- `ExchangeWithdrawContract`, by contrast, has no `expected`/minimum field at all. `ExchangeWithdrawActuator.doValidate()` computes `anotherTokenQuant` purely from the current pool ratio and only checks internal precision-loss tolerances ("Not precise enough") and pool-liquidity sufficiency — never a user-specified floor on the amount received: [3](#0-2) 

- The same asymmetry exists for `ExchangeInjectContract`, which also lacks any expected/minimum-received parameter for the paired token deposited. [4](#0-3) 

Because `ExchangeWithdrawContract`/`ExchangeInjectContract` transactions are ordinary broadcast transactions signed by the account owner and processed directly by `ExchangeWithdrawActuator`/`ExchangeInjectActuator` (no router-style wrapper enforces a minimum-output check before submission), any withdrawal or injection executes at whatever pool ratio exists at the time of block inclusion, with no on-chain mechanism to bound the resulting output/loss — exactly the missing protection described in the external report for `Pools.removeLiquidity()`/`removeLiquidityDirectly()` versus `Router.removeLiquidity()`.

### Impact Explanation
A liquidity provider withdrawing (or injecting) into an `Exchange` pool can receive far less value than intended if the pool ratio shifts between the time they compose the transaction and the time it is confirmed (e.g., due to preceding swaps in the same or a prior block, or an attacker sandwiching the withdrawal with `ExchangeTransactionContract` swaps that move the ratio unfavorably before the withdrawal executes and then reversing it afterward). This is an on-chain, unprivileged, economically exploitable accounting/market-math defect reachable purely via broadcast transactions — no special role or off-chain compromise is required.

### Likelihood Explanation
Likelihood is high: `ExchangeWithdrawContract` and `ExchangeInjectContract` are ordinary transaction types any account can broadcast, and the java-tron exchange pools are permissionless and continuously tradable via `ExchangeTransactionContract`. Sandwiching a withdrawal/injection with swaps is a standard, low-cost MEV-style attack pattern once a target's pending withdrawal is visible in the transaction pool.

### Recommendation
Add an `expected`/minimum-output field (and, for injection, a maximum-input or expected-paired-amount field) to `ExchangeWithdrawContract` and `ExchangeInjectContract`, and validate it in `ExchangeWithdrawActuator.doValidate()` / `ExchangeInjectActuator.doValidate()` analogous to the existing check in `ExchangeTransactionActuator.doValidate()` (`anotherTokenQuant < tokenExpected` → reject). Until such a protocol change ships, this limitation should be clearly documented so wallets/SDKs building withdrawal/injection transactions can implement their own pre-broadcast simulation and can warn users that no on-chain slippage protection exists for these two operations.

### Proof of Concept
1. Create an exchange pool via `ExchangeCreateContract` with tokens A/B at some ratio (see `ExchangeCreateContract` definition). [5](#0-4) 
2. Account `victim` broadcasts an `ExchangeWithdrawContract` intending to withdraw `tokenQuant` of token A expecting a proportional amount of token B based on the pool ratio observed off-chain.
3. Before `victim`'s transaction is packed into a block, an attacker broadcasts one or more `ExchangeTransactionContract` swaps that shift the A/B ratio unfavorably for withdrawals of token A.
4. `ExchangeWithdrawActuator.execute()` computes `anotherTokenQuant` from the now-manipulated ratio with no minimum-output check, crediting `victim` a far smaller amount of token B than originally anticipated, and the transaction still succeeds. [6](#0-5) 
5. The attacker reverses the swap afterward, realizing a profit at the victim's expense, while `victim` had no on-chain parameter available to prevent this outcome (unlike `ExchangeTransactionContract`'s `expected` field).

### Citations

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L9-15)
```text
message ExchangeCreateContract {
  bytes owner_address = 1;
  bytes first_token_id = 2;
  int64 first_token_balance = 3;
  bytes second_token_id = 4;
  int64 second_token_balance = 5;
}
```

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L17-37)
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L205-243)
```java
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
