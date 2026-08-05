Confirmed: `ExchangeInjectContract` and `ExchangeWithdrawContract` have no minimum-output/slippage field, while `ExchangeTransactionContract` does (`expected`) and is enforced at [1](#0-0) . This confirms the analog is real and reachable by any unprivileged account that created a TRC10 Exchange pair.

### Title
Missing slippage protection (min-output) in `ExchangeInjectActuator` and `ExchangeWithdrawActuator` enables front-running/sandwich losses on TRC10 Exchange liquidity operations - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
The java-tron built-in bancor-style TRC10 `Exchange` feature supports `ExchangeInjectContract` (add liquidity) and `ExchangeWithdrawContract` (remove liquidity). Unlike `ExchangeTransactionContract` (trade), which carries a caller-supplied `expected` minimum-output field that is validated on-chain [2](#0-1) , the inject and withdraw protobuf messages have no equivalent slippage-protection parameter at all [3](#0-2) . This is the direct analog of the Particle liquidity-position bug: `amount0Min`/`amount1Min` hardcoded to `0` gives no protection against price movement between transaction submission and execution.

### Finding Description
`ExchangeInjectActuator.execute` computes `anotherTokenQuant` purely from the current on-chain pool ratio at execution time (`firstTokenBalance`/`secondTokenBalance`) and the caller's `tokenQuant`, with no user-supplied bound on the acceptable ratio [4](#0-3) . The same pattern occurs in `ExchangeWithdrawActuator.execute`, where `anotherTokenQuant` returned to the user is computed from the pool ratio at execution time with no minimum-output check [5](#0-4) . Because a Tron block producer (or any actor able to order/insert transactions within a block/maintenance window) can execute an `ExchangeTransactionContract` trade immediately before the victim's inject/withdraw transaction, the pool ratio used to compute `anotherTokenQuant` can be manipulated: any account can freely trade against the exchange via `ExchangeTransactionActuator` (no privileged role required) since the only actor-restriction present is that only the exchange creator may call Inject/Withdraw [6](#0-5) , [7](#0-6)  — that restriction only limits who can inject/withdraw, not who can trade against the pool to move the ratio beforehand. There is no analog of Uniswap's `require(amount0 >= params.amount0Min ...)` check for these two operations.

### Impact Explanation
An attacker (any account, or the block producer packing the block) can front-run a pending `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction with an `ExchangeTransactionContract` trade that skews the pool ratio, causing the victim's liquidity operation to execute at an unfavorable ratio: on inject, the victim can be forced to contribute a disproportionately large amount of the paired asset for the same `tokenQuant`; on withdraw, the victim can receive less of the paired asset than the fair-ratio amount. The attacker then reverses the trade to restore the ratio and capture the difference (classic sandwich). This is a direct on-chain accounting/value-extraction impact on unprivileged user funds, analogous to the confirmed Particle finding.

### Likelihood Explanation
Likelihood is moderate-to-high: TRC10 Exchange trading (`ExchangeTransactionContract`) is unrestricted and cheap (`calcFee()` returns `0` in `ExchangeTransactionActuator`) [8](#0-7) , so sandwiching a visible pending inject/withdraw transaction requires no special access beyond transaction ordering (which block producers naturally control, and general MEV-style front-running is possible against any observable pending transaction).

### Recommendation
Add a caller-supplied minimum/maximum bound field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (mirroring the `expected` field already present on `ExchangeTransactionContract`), and enforce it in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` `doValidate`/`execute` by rejecting the operation if the computed `anotherTokenQuant` falls outside the caller's specified bound, exactly as `ExchangeTransactionActuator` already does for trades [1](#0-0) .

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` from the exchange creator for exchange pair (A, B) with `token_id = A`, `quant = Q`.
2. Attacker submits/front-runs an `ExchangeTransactionContract` trade that sells a large amount of B into the pool, sharply raising `firstTokenBalance`/lowering `secondTokenBalance` ratio (or vice versa depending on token order).
3. Victim's `ExchangeInjectContract` executes against the now-skewed ratio in `ExchangeInjectActuator.execute` [9](#0-8) , forcing the creator to lock in far more of token B than a fair-ratio injection would require, with no check available to reject the unfavorable rate.
4. Attacker submits a reverse trade to restore the ratio, extracting the value overpaid by the victim. The same sequence applies symmetrically to `ExchangeWithdrawContract`, reducing the `anotherTokenQuant` paid out to the victim.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L175-221)
```java
    long tokenExpected = contract.getExpected();

    if (dynamicStore.getAllowSameTokenName() == 1
        && !Arrays.equals(tokenID, TRX_SYMBOL_BYTES)
        && !isNumber(tokenID)) {
      throw new ContractValidateException("token id is not a valid number");
    }
    if (!Arrays.equals(tokenID, firstTokenID) && !Arrays.equals(tokenID, secondTokenID)) {
      throw new ContractValidateException("token is not in exchange");
    }

    if (tokenQuant <= 0) {
      throw new ContractValidateException("token quant must greater than zero");
    }

    if (tokenExpected <= 0) {
      throw new ContractValidateException("token expected must greater than zero");
    }

    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }

    long balanceLimit = dynamicStore.getExchangeBalanceLimit();
    long tokenBalance = (Arrays.equals(tokenID, firstTokenID) ? firstTokenBalance
        : secondTokenBalance);
    tokenBalance = addExact(tokenBalance, tokenQuant);
    if (tokenBalance > balanceLimit) {
      throw new ContractValidateException("token balance must less than " + balanceLimit);
    }

    if (Arrays.equals(tokenID, TRX_SYMBOL_BYTES)) {
      if (accountCapsule.getBalance() < addExact(tokenQuant, calcFee())) {
        throw new ContractValidateException("balance is not enough");
      }
    } else {
      if (!accountCapsule.assetBalanceEnoughV2(tokenID, tokenQuant, dynamicStore)) {
        throw new ContractValidateException("token balance is not enough");
      }
    }

    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L232-235)
```java
  @Override
  public long calcFee() {
    return 0;
  }
```

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
