### Title
Burn Fee Exceeds NLP Value for Sub-$1 Positions — (`core/contracts/Clearinghouse.sol`)

### Summary

In `Clearinghouse.burnNlp`, the minimum burn fee is hardcoded to `ONE = 1e18` ($1). When a user burns NLP whose total value (`quoteAmount`) is less than `ONE`, the fee exceeds the position value, `quoteAmount` is clamped to zero, and the user's NLP balance is fully decremented while they receive zero quote in return.

### Finding Description

`ONE` is confirmed as `10**18` in `Constants.sol`: [1](#0-0) 

The burn fee logic in `Clearinghouse.burnNlp`: [2](#0-1) 

When `quoteAmount < ONE` (i.e., the NLP position is worth less than $1):
- `quoteAmount / 1000 < 1e15`, so `MathHelper.max(ONE, quoteAmount/1000) = ONE`
- `burnFee = 1e18 > quoteAmount`
- `quoteAmount - burnFee < 0`, so `MathHelper.max(0, quoteAmount - burnFee) = 0`

The NLP is then unconditionally decremented from the user and credited to `N_ACCOUNT`: [3](#0-2) 

But the quote credit is gated on `quoteAmount > 0`, which is now false: [4](#0-3) 

There is **no minimum `nlpAmount` check** in `burnNlp` beyond the unlocked balance check: [5](#0-4) 

Similarly, `mintNlp` has no minimum `quoteAmount` guard: [6](#0-5) 

The `MIN_DEPOSIT_AMOUNT` and `MIN_FIRST_DEPOSIT_AMOUNT` constants apply to collateral deposits, not NLP minting: [7](#0-6) 

The trailing health check does not prevent this outcome — it only rejects burns that push the user's maintenance health negative. A user with sufficient other collateral passes the check while still losing their entire sub-$1 NLP value: [8](#0-7) 

### Impact Explanation

A user who holds NLP worth less than $1 (reachable by minting with a small `quoteAmount`, or by NLP price depreciation after minting) will:
1. Have their full `nlpAmount` transferred to `N_ACCOUNT`
2. Receive zero quote credit
3. Net loss equals the full pre-fee value of the NLP burned

The "fee" is not redistributed to a fee account — it is silently absorbed because no quote accounting occurs when `quoteAmount = 0`. The user's asset value is consumed without compensation.

### Likelihood Explanation

The path is reachable through the normal `BurnNlp` signed transaction flow processed by `EndpointTx.processTransactionImpl`: [9](#0-8) 

Preconditions are mild:
- User mints NLP with any `quoteAmount < 1e18` (no minimum enforced), **or**
- User minted at a higher price and the NLP oracle price later drops such that their remaining position is worth < $1
- User waits out the 4-day `NLP_LOCK_PERIOD` for the balance to unlock

### Recommendation

Add a guard in `burnNlp` that reverts when the computed `burnFee` would exceed `quoteAmount`:

```solidity
require(quoteAmount >= burnFee, "ERR_BURN_FEE_EXCEEDS_VALUE");
```

Alternatively, enforce a minimum `nlpAmount` at burn time such that `nlpAmount.mul(oraclePriceX18) >= ONE` always holds. A symmetric minimum should also be added to `mintNlp` to prevent sub-$1 positions from being created in the first place.

### Proof of Concept

```
Setup:
  oraclePriceX18 = 1e18  ($1 per NLP token)
  User mints with quoteAmount = 0.5e18 ($0.50)
  nlpAmount minted = 0.5e18 / 1e18 = 0.5e18 (in fixed-point)

After 4-day lock period, user calls BurnNlp with nlpAmount = 0.5e18:
  quoteAmount = 0.5e18 * 1e18 / 1e18 = 0.5e18
  burnFee     = max(1e18, 0.5e18/1000) = max(1e18, 5e14) = 1e18
  quoteAmount = max(0, 0.5e18 - 1e18) = max(0, -0.5e18) = 0

Result:
  spotEngine.updateBalance(NLP_PRODUCT_ID, sender, -0.5e18)  ← user loses NLP
  spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, +0.5e18) ← protocol gains NLP
  quoteAmount == 0 → no quote credited to user
  User net: -$0.50 in NLP, +$0.00 in quote = -$0.50 loss
```

### Citations

**File:** core/contracts/common/Constants.sol (L17-17)
```text
int128 constant ONE = 10**18;
```

**File:** core/contracts/common/Constants.sol (L40-42)
```text
int256 constant MIN_DEPOSIT_AMOUNT = ONE / 10; // $0.1

int256 constant MIN_FIRST_DEPOSIT_AMOUNT = 5 * ONE; // $5
```

**File:** core/contracts/Clearinghouse.sol (L464-466)
```text
        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L496-501)
```text
        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Clearinghouse.sol (L511-512)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);
```

**File:** core/contracts/Clearinghouse.sol (L514-517)
```text
        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }
```

**File:** core/contracts/Clearinghouse.sol (L526-529)
```text
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
```

**File:** core/contracts/EndpointTx.sol (L554-573)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedBurnNlp)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```
