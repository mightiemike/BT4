### Title
Missing slippage/minimum-output protection in `ExchangeInjectActuator` and `ExchangeWithdrawActuator` liquidity operations - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java`, `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
TRON's built-in TRC10 AMM ("Bancor-style" exchange) supports three market operations: swap (`ExchangeTransactionContract`), add liquidity (`ExchangeInjectContract`), and remove liquidity (`ExchangeWithdrawContract`). The swap operation was hardened with an explicit `expected` field that guarantees a minimum output, but the liquidity-provision (`inject`) and liquidity-removal (`withdraw`) operations have no equivalent slippage-bound field. The counterpart token amount is computed purely from the pool's current on-chain ratio at execution time, exactly the pattern flagged in the external report for `spot_lp`.

### Finding Description
`ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `token_id` and `quant` — there is no minimum/maximum bound for the other side of the pair: [1](#0-0) 

Compare this to `ExchangeTransactionContract`, which explicitly adds an `expected` field used as a slippage floor: [2](#0-1) 

In `ExchangeInjectActuator.doValidate()`/`execute()`, the "another token" amount required to add liquidity is derived from `firstTokenBalance`/`secondTokenBalance` read at validation/execution time, with no user-supplied cap: [3](#0-2) 
and the same unclamped ratio calculation is repeated in `execute()`: [4](#0-3) 

Symmetrically, in `ExchangeWithdrawActuator`, the amount of the paired token returned to the liquidity remover is computed from the pool's live ratio, with no minimum-received guarantee configurable by the caller: [5](#0-4) 

The pool ratio (`firstTokenBalance`/`secondTokenBalance`) is mutated by every `ExchangeTransactionContract` swap that executes in the mempool/block before the inject/withdraw transaction: [6](#0-5) 

This is precisely the bug class described in the report: a liquidity operation ("spot_lp"-equivalent) that computes the counterpart quantity from the mutable pool state (`header.crncy_tokens`/`header.asset_tokens` analog: `firstTokenBalance`/`secondTokenBalance`) with no slippage bound, unlike the swap path which does have one (`tokenExpected` analog: Deriverse's recommended `amountAMin`/`amountBMin`).

### Impact Explanation
- On `ExchangeInjectContract`: if the pool ratio shifts (via intervening swaps) between when the user signs/broadcasts and when the transaction is packed into a block, the actuator will silently require a different (potentially much larger) quantity of the paired asset than the user anticipated when they froze `quant`, causing the user to overpay/deposit an unfavorable ratio with no ability to abort at execution time.
- On `ExchangeWithdrawContract`: the paired-token amount returned on withdrawal can be lower than expected if the ratio moves adversely, and the LP has no way to require a minimum acceptable payout.
- Because ordering of transactions within a block is influenced by SR block producers, this also creates a sandwich/front-running incentive: an attacker (or a malicious/self-interested SR) can execute a swap immediately before a victim's inject/withdraw transaction to shift the ratio unfavorably for the victim, then reverse the swap afterward, extracting value from LPs. This is unauthorized value extraction/accounting corruption reachable purely via broadcast transactions.

### Likelihood Explanation
Both `ExchangeInjectContract` and `ExchangeWithdrawContract` are ordinary, publicly broadcastable transaction types processed by any full node/SR; no special privileges are required to submit them or to submit the preceding `ExchangeTransactionContract` swap that manipulates the ratio. TRC10 exchanges are still an active part of the protocol and swap volume/ratio changes between blocks are routine, making the slippage window realistically exploitable, especially by block-producing validators who fully control intra-block ordering.

### Recommendation
Add explicit slippage-bound fields to `ExchangeInjectContract` and `ExchangeWithdrawContract` (e.g., `expected_another_token_max`/`expected_another_token_min`), and validate the computed `anotherTokenQuant` against these bounds in `ExchangeInjectActuator`/`ExchangeWithdrawActuator`, analogous to the existing `tokenExpected` check in `ExchangeTransactionActuator.doValidate()`.

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) transaction from a victim in the mempool, specifying `token_id = firstTokenID`, `quant = X`.
2. Attacker submits an `ExchangeTransactionContract` swap that shifts `firstTokenBalance`/`secondTokenBalance` unfavorably, and, if desired, colludes with/serves as the block producer to guarantee ordering before the victim's transaction.
3. Victim's `ExchangeInjectActuator.execute()` recomputes `anotherTokenQuant` from the now-shifted balances (`actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java:71-83`), forcing the victim to deposit a worse ratio than intended, with no `expected`-style check to reject the trade.
4. Attacker reverses the initial swap afterward, extracting the ratio difference as profit at the victim's expense — the same value-loss mechanism described in the original `spot_lp` report, applied to java-tron's on-chain AMM inject/withdraw actuators.

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L74-89)
```java
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L71-76)
```java
      if (Arrays.equals(tokenID, firstTokenID)) {
        anotherTokenID = secondTokenID;
      } else {
        anotherTokenID = firstTokenID;
      }

```
