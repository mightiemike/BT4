Confirmed: `ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `owner_address`, `exchange_id`, `token_id`, `quant` — no `expected`/minimum-output field, whereas `ExchangeTransactionContract` explicitly adds an `expected` field for slippage protection. [1](#0-0) 

### Title
No slippage/minimum-output protection in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` enables sandwich-style value extraction from Bancor-curve exchange pools - (File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java, actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java)

### Summary
The TRC10 Bancor-formula `Exchange` feature lets an account create a token pair pool and, as its creator, `Inject` or `Withdraw` liquidity. Both operations compute the paired token amount (`anotherTokenQuant`) purely from the pool's *current* on-chain balances at execution time, with no user-supplied minimum/maximum bound. This mirrors exactly the reported Smart Vault issue: an action whose settlement price is determined by mutable on-chain state at execution time, with no per-transaction slippage guard — except here there isn't even the blunt 90% floor that the Smart Vault contract had; there is no bound at all.

### Finding Description
`ExchangeInjectActuator.execute` and `.doValidate()` derive `anotherTokenQuant` as a simple ratio of the *current* `firstTokenBalance`/`secondTokenBalance` read from the `ExchangeCapsule` at execution time: [2](#0-1) 

Likewise, `ExchangeWithdrawActuator.execute` computes `anotherTokenQuant` from the pool ratio at execution time and immediately mutates balances and pays out tokens: [3](#0-2) 

Neither `ExchangeInjectContract` nor `ExchangeWithdrawContract` expose any field for the caller to specify an acceptable minimum/maximum for `anotherTokenQuant`: [1](#0-0) 

By contrast, `ExchangeTransactionContract` (a plain swap) *does* carry an `expected` field, and `ExchangeTransactionActuator.doValidate()` explicitly enforces it as a slippage floor before allowing the trade to proceed: [4](#0-3) 

This asymmetry means the pool creator's inject/withdraw calls — which move real value between the creator's TRX/TRC10 balances and the pool — are computed at whatever ratio exists the instant the transaction lands on-chain, with zero user-defined bound. Any `ExchangeTransactionContract` trade included by the block producer immediately before the creator's `ExchangeInjectContract`/`ExchangeWithdrawContract` transaction shifts `firstTokenBalance`/`secondTokenBalance`, which directly changes the `anotherTokenQuant` the creator will pay in (inject) or receive (withdraw). A trade placed right after to reverse the price shift completes a classic sandwich, extracting value from the creator with no cap on the loss, unlike the reported Smart Vault case where losses were at least capped at 10%.

### Impact Explanation
An exchange creator injecting or withdrawing liquidity can be made to pay significantly more of one token than intended (inject) or receive significantly less of the paired token than intended (withdraw), because the on-chain settlement ratio used by `ExchangeInjectActuator`/`ExchangeWithdrawActuator` is fully attacker-influenceable via an `ExchangeTransactionContract` trade ordered immediately before it, and with no minimum-output check the transaction cannot revert to protect the victim. This is a concrete accounting/value-loss impact directly analogous to (and unbounded relative to) the reported bug.

### Likelihood Explanation
Any account can create an `Exchange` pool and thus become eligible to call `Inject`/`Withdraw`, so this is reachable by ordinary, unprivileged users — it is not gated by any committee/witness-only permission (the only restriction, `account is not creator`, ties the call to the specific pool's own creator, not a privileged chain role). Front-running/sandwiching is achievable by any actor able to submit competing transactions before block packing (including a witness ordering transactions within its own produced block, or any user racing to get transactions included in the same or an earlier block), making this practically exploitable whenever a creator injects/withdraws non-trivial liquidity.

### Recommendation
Add an `expected`-style minimum/maximum bound field to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to `ExchangeTransactionContract.expected`), and enforce it in `ExchangeInjectActuator`/`ExchangeWithdrawActuator` before mutating balances, e.g. reject if the computed `anotherTokenQuant` is less than a caller-supplied minimum (withdraw) or greater than a caller-supplied maximum (inject).

### Proof of Concept
1. Attacker observes a pending `ExchangeWithdrawContract` from the pool creator withdrawing `tokenQuant` of `firstTokenID`.
2. Attacker submits an `ExchangeTransactionContract` selling a large amount of `secondTokenID` into the same exchange, shrinking `secondTokenBalance` relative to `firstTokenBalance` right before the creator's withdrawal is packed (or is naturally ordered before it in the block).
3. `ExchangeWithdrawActuator.execute` computes `anotherTokenQuant = secondTokenBalance * tokenQuant / firstTokenBalance` using this manipulated ratio — see [5](#0-4) 
 — yielding far less `secondTokenID` to the creator than the pre-manipulation ratio would have.
4. Attacker follows with a reverse trade restoring the original ratio, pocketing the difference extracted from the creator, who had no `expected`/minimum parameter available to guard against this, unlike `ExchangeTransactionContract.expected` used in `ExchangeTransactionActuator.doValidate()` (line 219).

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```
