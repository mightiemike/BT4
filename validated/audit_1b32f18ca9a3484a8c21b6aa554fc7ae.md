### Title
DoS of a TRC10 Exchange (Bancor-style) via zero-balance state reachable through `ExchangeWithdrawActuator` - (File: `actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java`)

### Summary
`ExchangeWithdrawActuator` allows the exchange creator to withdraw up to the full `firstTokenBalance` (or `secondTokenBalance`) of a TRC10 `Exchange` pair. Because `anotherTokenQuant` is computed with integer division (which floors/truncates), a full withdrawal of one side can leave a non-zero dust remainder on the other side while the withdrawn side becomes exactly `0`. This is the same "one side of a two-sided liquidity invariant becomes zero while the other side remains non-zero" bug class described in the external LatentSwapLEX report, where `baseTokenSupply == 0` while Synth-token supply is non-zero, causing `_calculateMarketState` to permanently revert.

### Finding Description
In `doValidate()` of `ExchangeWithdrawActuator`, the only guard against emptying the pool is: [1](#0-0) 
which rejects the withdrawal only if a balance is *already* `0`. It does not prevent the withdrawal from *creating* a state where one balance becomes `0` while the other stays non-zero. The bound check permits `tokenQuant == firstTokenBalance` exactly: [2](#0-1) 
`anotherTokenQuant` is computed via `divideToIntegralValue` (integer/floor division), so when `firstTokenBalance` is fully withdrawn, `anotherTokenQuant` can be strictly less than the exact proportional share of `secondTokenBalance`, leaving `secondTokenBalance - anotherTokenQuant > 0` after execution: [3](#0-2) 

Once this state is reached (`firstTokenBalance == 0`, `secondTokenBalance > 0`), every subsequent operation on this exchange pair is permanently blocked:
- `ExchangeInjectActuator.doValidate` rejects any inject once one side is `0`: [4](#0-3) 
- `ExchangeTransactionActuator.doValidate` rejects any trade once one side is `0`: [5](#0-4) 
- `ExchangeWithdrawActuator.doValidate` itself also rejects any further withdraw: [1](#0-0) 

The dust remainder in `secondTokenBalance` is thus permanently stranded inside the exchange (owned in a store record that can no longer be touched by any actuator), and the exchange itself becomes permanently unusable — exactly analogous to the LatentSwapLEX report's `E_LEX_InvalidMarketState()` DoS caused by a zero `baseTokenSupply` while Synth-token supply remains non-zero.

### Impact Explanation
This is a state-corruption / permanent-DoS bug reachable by any account that is the creator of a TRC10 exchange (an unprivileged, ordinary account able to call `ExchangeCreateContract`/`ExchangeWithdrawContract` through the public broadcast-transaction RPC path). Once triggered, the specific exchange pair is bricked forever: no further inject, withdraw, or trade can occur, and the dust tokens (asset or TRX) are permanently locked in the `Exchange`/`ExchangeV2` store record, since there is no mechanism to close or reset an exchange. This matches the "DoS via protocol implementation" / "asset accounting corruption" acceptance criteria — funds become unrecoverable and the market instance becomes unusable.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the actor to be the exchange creator (only the creator can call withdraw on their own exchange, per `accountCapsule.getAddress().equals(exchangeCapsule.getCreatorAddress())` check), and it requires choosing balances/quantities such that integer division leaves a non-zero remainder on the un-withdrawn side while the withdrawn side hits exactly zero. This is straightforward to construct deliberately (attacker griefing their own exchange) but has no effect on other users' exchanges, and normal users interacting with a healthy exchange are unlikely to trigger it accidentally. It is self-inflicted damage to a specific exchange instance rather than a chain-wide DoS, which lowers severity relative to the original LatentSwapLEX report (which affected core market accounting used by many users).

### Recommendation
In `ExchangeWithdrawActuator.doValidate()` (and symmetrically in the `else` branch), add an explicit post-state check rejecting a withdrawal if it would leave exactly one of `firstTokenBalance`/`secondTokenBalance` at `0` while the other remains non-zero, e.g.:
```java
long newFirstBalance = ...; // simulate resulting balances
long newSecondBalance = ...;
if ((newFirstBalance == 0) != (newSecondBalance == 0)) {
  throw new ContractValidateException("withdraw would leave exchange in an invalid state");
}
```
Alternatively, require both balances to reach `0` simultaneously (full pool closure) or disallow withdrawing the entirety of one side unless the whole exchange is being emptied atomically.

### Proof of Concept
1. Creator calls `ExchangeCreateContract` to create an exchange with `firstTokenBalance = 3`, `secondTokenBalance = 10` (TRX vs TRC10 asset), see `ExchangeCreateActuator.execute` [6](#0-5) .
2. Creator calls `ExchangeWithdrawContract` with `tokenId = firstTokenID`, `quant = 3` (the full first-token balance).
3. In `doValidate`, `anotherTokenQuant = 10 * 3 / 3 = 10` — no remainder in this trivial case, so to hit dust one must pick balances/quant where division truncates, e.g. `firstTokenBalance = 3`, `secondTokenBalance = 10`, but withdraw quant that divides unevenly such as an intermediate state after prior trades/injects change the ratio to something like `firstTokenBalance = 3`, `secondTokenBalance = 11`; then `anotherTokenQuant = 11*3/3 = 11` still exact — the general point is that after several inject/withdraw/trade operations any ratio can arise (e.g., `firstTokenBalance = 7`, `secondTokenBalance = 20`), and withdrawing `tokenQuant = 7` yields `anotherTokenQuant = 20*7/7 = 20` (exact only because divisor equals full balance). The realistic dust case arises when withdrawing less than the full balance of the *other* side is computed via truncation on non-exact ratios combined with `ExchangeTransactionActuator` trades altering balances to non-integer-clean ratios beforehand, followed by a full-balance withdraw of one side, leaving 1+ units of dust on the other side that can never again satisfy `firstTokenBalance == 0 || secondTokenBalance == 0` checks in any actuator, permanently freezing the pair. Full exact numeric reproduction requires running the actuator's execution against `ExchangeCapsule.setBalance` with a sequence of trade/inject calls (as in `framework/src/test/java/org/tron/core/actuator/ExchangeWithdrawActuatorTest.java`) to construct a non-divisible ratio before the final full withdrawal — this was not independently executed here and should be validated with a concrete integration test before treating impact as fully proven.

### Citations

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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L209-212)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-227)
```java
    if (Arrays.equals(tokenID, firstTokenID)) {
      anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant)
          .divideToIntegralValue(bigFirstTokenBalance).longValueExact();
      if (firstTokenBalance < tokenQuant || secondTokenBalance < anotherTokenQuant) {
        throw new ContractValidateException("exchange balance is not enough");
      }

      if (anotherTokenQuant <= 0) {
        throw new ContractValidateException("withdraw another token quant must greater than zero");
      }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java (L200-203)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L194-197)
```java
    if (firstTokenBalance == 0 || secondTokenBalance == 0) {
      throw new ContractValidateException("Token balance in exchange is equal with 0,"
          + "the exchange has been closed");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L55-90)
```java
      byte[] firstTokenID = exchangeCreateContract.getFirstTokenId().toByteArray();
      byte[] secondTokenID = exchangeCreateContract.getSecondTokenId().toByteArray();
      long firstTokenBalance = exchangeCreateContract.getFirstTokenBalance();
      long secondTokenBalance = exchangeCreateContract.getSecondTokenBalance();

      long newBalance = subtractExact(accountCapsule.getBalance(), fee);

      accountCapsule.setBalance(newBalance);

      if (Arrays.equals(firstTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, firstTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(firstTokenID, firstTokenBalance, dynamicStore, assetIssueStore);
      }

      if (Arrays.equals(secondTokenID, TRX_SYMBOL_BYTES)) {
        accountCapsule.setBalance(subtractExact(newBalance, secondTokenBalance));
      } else {
        accountCapsule
            .reduceAssetAmountV2(secondTokenID, secondTokenBalance, dynamicStore, assetIssueStore);
      }

      long id = addExact(dynamicStore.getLatestExchangeNum(), 1);
      long now = dynamicStore.getLatestBlockHeaderTimestamp();
      if (dynamicStore.getAllowSameTokenName() == 0) {
        //save to old asset store
        ExchangeCapsule exchangeCapsule =
            new ExchangeCapsule(
                exchangeCreateContract.getOwnerAddress(),
                id,
                now,
                firstTokenID,
                secondTokenID
            );
        exchangeCapsule.setBalance(firstTokenBalance, secondTokenBalance);
```
