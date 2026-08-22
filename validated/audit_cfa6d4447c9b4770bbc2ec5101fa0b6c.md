Confirmed: unlike `ExchangeTransactionContract` (which has an `expected` field enforced by `if (anotherTokenQuant < tokenExpected) throw new ContractValidateException(...)` at [1](#0-0) ), both `ExchangeInjectContract` and `ExchangeWithdrawContract` have no minimum/expected-amount field at all in their protobuf definitions [2](#0-1) , and their actuators compute the "other side" amount purely from the exchange's live ratio at execution time with zero slippage guard [3](#0-2) [4](#0-3) .

### Title
Missing slippage/minimum-amount protection in `ExchangeInjectContract`/`ExchangeWithdrawContract` (no `expected`/min field) - ([File: actuator/src/main/java/org/tron/core/actuator/ExchangeInjectActuator.java], [File: actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java])

### Summary
TRON's built-in Bancor-style token exchange (`ExchangeStore`/`ExchangeV2Store`) supports three user-facing operations that mutate a token pair's constant-product-like reserves: inject liquidity, withdraw liquidity, and swap ("exchange transaction"). Only the swap operation (`ExchangeTransactionContract`) carries an `expected` field that is validated against the computed output amount before execution. The inject and withdraw operations compute the counter-token amount from the current pool ratio at execution time but provide **no analogous minimum/maximum bound field**, so a user broadcasting an inject or withdraw transaction has no on-chain way to bound the ratio used when their transaction actually executes.

### Finding Description
`ExchangeCreateActuator` creates a pool with `first_token_balance`/`second_token_balance` and lets the owner add tokens via `ExchangeInjectContract` or remove via `ExchangeWithdrawContract` [5](#0-4) .

The `ExchangeTransactionContract` swap protobuf message explicitly documents and includes an `expected` field, "expected minimum number of tokens" [6](#0-5) , and `ExchangeTransactionActuator.doValidate()` enforces it: `if (anotherTokenQuant < tokenExpected) { throw new ContractValidateException("token required must greater than expected"); }` [1](#0-0) .

By contrast, `ExchangeInjectContract` and `ExchangeWithdrawContract` only carry `owner_address`, `exchange_id`, `token_id`, and `quant` — no `expected`/min-output field [7](#0-6) . Their actuators compute the paired amount purely from whatever the live pool ratio is at execution time:
- Inject: `anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant).divide(bigFirstTokenBalance)...` with no floor/ceiling check against a user-supplied bound [3](#0-2) .
- Withdraw: same pattern, `anotherTokenQuant = bigSecondTokenBalance.multiply(bigTokenQuant).divideToIntegralValue(bigFirstTokenBalance)...` [4](#0-3) .

Because any anonymous account can broadcast an `ExchangeTransactionContract` swap against the same `exchange_id` (subject only to the `exchangeBalanceLimit` and balance checks), and transaction ordering within a block is controlled by the block-producing SR/witness (and, prior to inclusion, is visible in the mempool/broadcast phase), an attacker can move the pool ratio between the time the victim signs/broadcasts an inject/withdraw transaction and the time it is actually executed — i.e. a sandwich/front-run pattern. Since the inject/withdraw actuators recompute the counter-amount from the *current* ratio rather than the ratio the victim observed, the victim's inject can be forced to consume proportionally more of the "other" token (or receive proportionally less on withdrawal) than they intended, without the transaction reverting, because there is no bound to check against. This mirrors the reported AMM issue where `amount0Min`/`amount1Min` are hard-coded to zero: the analog here is that the corresponding fields don't exist at all in the protocol for these two operations, so there is no protocol-level way to add such protection even client-side.

### Impact Explanation
An attacker (any funded account, no special privileges) can extract value from users adding or removing liquidity from a TRON on-chain exchange pool by manipulating the pool ratio immediately before the victim's inject/withdraw executes, since the victim has no mechanism to cap the counter-token amount consumed or bound the amount received. This is an accounting/economic-loss issue for anyone using the built-in exchange feature, reachable purely via broadcast transactions with no privileged access needed.

### Likelihood Explanation
Medium: it requires the attacker to get a swap transaction into the same block, ordered before the victim's inject/withdraw transaction, and requires an exchange pool with a meaningful reserve ratio to manipulate. This is achievable because transaction ordering in the mempool/block is not controlled by the victim, and swap transactions on public exchange pools are cheap (fee is `0`, see `calcFee()` returning `0` in `ExchangeTransactionActuator`) [8](#0-7) .

### Recommendation
Add a minimum/expected-amount bound to `ExchangeInjectContract` and `ExchangeWithdrawContract` (analogous to `ExchangeTransactionContract.expected`), and enforce it in `ExchangeInjectActuator.doValidate()`/`execute()` and `ExchangeWithdrawActuator.doValidate()`/`execute()` by reverting when the computed `anotherTokenQuant` falls outside the caller-specified bound, mirroring the existing pattern at [1](#0-0) .

### Proof of Concept
1. Attacker observes a pending `ExchangeInjectContract` (or `ExchangeWithdrawContract`) transaction from victim `V` for exchange `id`, injecting `tokenQuant` of `firstTokenID`.
2. Attacker broadcasts an `ExchangeTransactionContract` swap against the same `exchange_id` that shifts `firstTokenBalance`/`secondTokenBalance` significantly, and ensures it lands in the same block before `V`'s transaction (e.g. via fee/ordering or SR collusion).
3. When `V`'s `ExchangeInjectActuator.execute()` runs, `anotherTokenQuant` is computed from the now-manipulated ratio [9](#0-8) , silently debiting `V` a different amount of the counter-token than expected, with no revert possible since no bound field exists in the contract to check against.

### Citations

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L217-221)
```java
    long anotherTokenQuant = exchangeCapsule.transaction(tokenID, tokenQuant,
        dynamicStore.allowStrictMath(), allowHarden());
    if (anotherTokenQuant < tokenExpected) {
      throw new ContractValidateException("token required must greater than expected");
    }
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeTransactionActuator.java (L233-235)
```java
  public long calcFee() {
    return 0;
  }
```

**File:** protocol/src/main/protos/core/contract/exchange_contract.proto (L1-1)
```text
syntax = "proto3";
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

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeWithdrawActuator.java (L218-230)
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
      if (allowHarden) {
        BigDecimal remainder = bigSecondTokenBalance.multiply(bigTokenQuant)
            .divide(bigFirstTokenBalance, 4, RoundingMode.HALF_UP)
```

**File:** actuator/src/main/java/org/tron/core/actuator/ExchangeCreateActuator.java (L50-58)
```java
      final ExchangeCreateContract exchangeCreateContract = this.any
          .unpack(ExchangeCreateContract.class);
      AccountCapsule accountCapsule = accountStore
          .get(exchangeCreateContract.getOwnerAddress().toByteArray());

      byte[] firstTokenID = exchangeCreateContract.getFirstTokenId().toByteArray();
      byte[] secondTokenID = exchangeCreateContract.getSecondTokenId().toByteArray();
      long firstTokenBalance = exchangeCreateContract.getFirstTokenBalance();
      long secondTokenBalance = exchangeCreateContract.getSecondTokenBalance();
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

**File:** Tron protobuf protocol document.md (L1422-1441)
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
