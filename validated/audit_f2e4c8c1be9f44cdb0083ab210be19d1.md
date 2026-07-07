### Title
Missing Slippage Protection on `burnNlp()` — (File: `core/contracts/Clearinghouse.sol`)

---

### Summary

The `BurnNlp` struct that users sign contains no `minQuoteAmount` field. When a user burns NLP tokens, the quote they receive is computed entirely from a sequencer-supplied `oraclePriceX18` that the user never commits to. There is no on-chain check that the received quote meets any user-specified minimum, leaving users exposed to receiving materially less quote than expected.

---

### Finding Description

The `BurnNlp` struct — the payload the user actually signs — contains only three fields:

```solidity
struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64 nonce;
}
``` [1](#0-0) 

The `oraclePriceX18` used to price the burn is **not** part of what the user signs. It is injected by the sequencer into the outer `SignedBurnNlp` wrapper at execution time:

```solidity
struct SignedBurnNlp {
    BurnNlp tx;
    bytes signature;
    int128 oraclePriceX18;       // sequencer-supplied, not user-signed
    int128[] nlpPoolRebalanceX18;
}
``` [2](#0-1) 

Inside `burnNlp()`, the quote the user receives is computed directly from this sequencer-supplied price with no floor check:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [3](#0-2) 

The function then credits the user with `quoteAmount` and returns — there is no comparison against any user-specified minimum: [4](#0-3) 

`ERR_SLIPPAGE_TOO_HIGH` is defined in `Errors.sol` but is **never referenced** in any production contract — confirming the slippage guard was never wired into the NLP burn path: [5](#0-4) 

---

### Impact Explanation

A user who signs a `BurnNlp` transaction when the NLP oracle price is `P` has no on-chain guarantee that the burn will execute at or near `P`. If the oracle price falls between signing and sequencer execution — whether due to legitimate market movement or sequencer-controlled oracle updates — the user receives fewer quote tokens than intended, with no ability to revert the transaction based on a minimum output. The NLP tokens are already burned; the loss is final.

**Impact: Medium** — direct, irreversible loss of quote value for the burning user.

---

### Likelihood Explanation

NLP oracle price (`oraclePriceX18`) is updated by the sequencer and reflects the NAV of the NLP pool, which fluctuates with market conditions. During volatile periods, the price between a user signing the transaction and the sequencer processing it can differ meaningfully. The sequencer processes transactions in a queue; a user's `BurnNlp` can sit pending while the oracle price moves adversely. No malicious actor is required — normal market volatility is sufficient to trigger the loss.

**Likelihood: Medium**

---

### Recommendation

Add a `minQuoteAmount` field to the `BurnNlp` struct so it is part of the user's signed payload:

```solidity
struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64 nonce;
    uint128 minQuoteAmount; // user-specified minimum output
}
```

In `burnNlp()`, after computing `quoteAmount`, enforce:

```solidity
require(quoteAmount >= int128(txn.minQuoteAmount), ERR_SLIPPAGE_TOO_HIGH);
```

This mirrors the already-defined `ERR_SLIPPAGE_TOO_HIGH` error constant that currently goes unused.

---

### Proof of Concept

1. Alice observes NLP oracle price = `10e18` (i.e., $10/NLP).
2. Alice signs `BurnNlp{sender: alice, nlpAmount: 1000e18, nonce: 5}`, expecting ≈ `9990e18` quote (after 0.1% burn fee).
3. Before the sequencer processes Alice's transaction, the NLP oracle price drops to `7e18` (e.g., due to a large perp loss in the pool).
4. Sequencer executes `burnNlp` with `oraclePriceX18 = 7e18`.
5. `quoteAmount = 1000e18 * 7e18 / 1e18 = 7000e18`; after fee ≈ `6993e18`.
6. Alice receives `6993e18` quote — a ~30% shortfall — with no on-chain protection and no ability to revert. [6](#0-5)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L125-129)
```text
    struct BurnNlp {
        bytes32 sender;
        uint128 nlpAmount;
        uint64 nonce;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L131-136)
```text
    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/Clearinghouse.sol (L496-504)
```text
        require(txn.nlpAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 nlpAmount = int128(txn.nlpAmount);
        require(
            spotEngine.getNlpUnlockedBalance(txn.sender).amount >= nlpAmount,
            ERR_UNLOCKED_NLP_INSUFFICIENT
        );
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Clearinghouse.sol (L514-516)
```text
        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
```

**File:** core/contracts/common/Errors.sol (L90-90)
```text
string constant ERR_SLIPPAGE_TOO_HIGH = "STH";
```
