### Title
`burnNlp` Lacks Minimum Price Protection — User Receives Far Less Quote Than Expected When NLP Pool Is Undercollateralized — (`File: core/contracts/Clearinghouse.sol`)

---

### Summary

The `burnNlp` function computes the quote returned to a user using `oraclePriceX18` supplied by the sequencer at execution time. The user's EIP-712 signature covers only `BurnNlp {sender, nlpAmount, nonce}` — it does **not** commit to any minimum acceptable price. There is no slippage guard. An unprivileged attacker who causes NLP pool losses (by taking profitable trades against the pool) can reduce the NLP oracle price before the user's burn is executed, causing the user to receive far less quote than expected for the same `nlpAmount`.

---

### Finding Description

In `Clearinghouse.burnNlp()`, the quote amount returned to the user is computed entirely from a sequencer-supplied parameter:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
``` [1](#0-0) 

The `oraclePriceX18` arrives from `SignedBurnNlp.oraclePriceX18`, which is a sequencer-appended field:

```solidity
struct SignedBurnNlp {
    BurnNlp tx;               // {sender, nlpAmount, nonce} — what the user signs
    bytes signature;
    int128 oraclePriceX18;    // sequencer-provided, NOT in user's signature
    int128[] nlpPoolRebalanceX18;
}
``` [2](#0-1) 

`validateSignedTx` verifies the user's signature over the inner `BurnNlp` struct (`{sender, nlpAmount, nonce}`). Because `oraclePriceX18` is outside that struct, it is never covered by the user's cryptographic commitment. [3](#0-2) 

The sequencer then passes `signedTx.oraclePriceX18` directly into `clearinghouse.burnNlp()`: [4](#0-3) 

There is no `minQuoteAmount` or `minPriceX18` field anywhere in `BurnNlp` or `burnNlp`. The user has zero slippage protection.

The NLP pool holds quote balances and acts as a market maker. When the pool takes losses from profitable trades by external participants, the NLP oracle price decreases. The sequencer honestly reports this lower price. A user who signed a `burnNlp` transaction expecting price P will receive `nlpAmount × P'` where `P' < P`, with no recourse.

---

### Impact Explanation

A user burning NLP tokens receives far less quote than they committed to. The signed transaction encodes only `nlpAmount` — the user cannot enforce a minimum return. If the NLP pool is undercollateralized at execution time (pool quote balance has been depleted by losses), the user's burn executes at the depressed oracle price and they receive a fraction of the expected quote. The NLP pool's quote balance is decremented by the (lower) `quoteAmount` via `_applyNlpRebalance`, further deepening the pool's undercollateralization. [5](#0-4) 

---

### Likelihood Explanation

The NLP pool is a market maker exposed to directional risk. Pool losses are a normal occurrence. A well-capitalized attacker who observes a pending `burnNlp` in the sequencer pipeline can take large profitable trades against the pool immediately before the burn is sequenced, depressing the oracle price. The attacker profits from the trades; the victim receives less quote for the same NLP burn. The attack requires no privileged access — only trading capital and timing.

---

### Recommendation

Add a `minQuoteAmount` (or `minPriceX18`) field to the `BurnNlp` struct so it is covered by the user's EIP-712 signature:

```solidity
struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64  nonce;
    int128  minQuoteAmount; // NEW: user-enforced slippage floor
}
```

In `Clearinghouse.burnNlp()`, add:

```solidity
require(quoteAmount >= int128(txn.minQuoteAmount), ERR_SLIPPAGE);
```

This mirrors the standard slippage-protection pattern used in AMMs and ensures the user's signed commitment fully constrains the economic outcome of the burn.

---

### Proof of Concept

1. Alice signs `BurnNlp {sender=Alice, nlpAmount=100e18, nonce=5}` expecting ~$10,000 in quote at NLP price = 100.
2. Alice submits the signed transaction to the sequencer.
3. Attacker takes large profitable directional trades against the NLP pool, causing pool losses.
4. NLP oracle price drops from 100 → 50.
5. Sequencer executes Alice's burn with `oraclePriceX18 = 50` (honest price, post-loss).
6. `quoteAmount = 100e18 × 50 = 5,000e18` — Alice receives only $5,000 instead of $10,000.
7. Alice's NLP is permanently burned; she has no mechanism to reject the execution at the lower price.

The root cause — a redemption executing during undercollateralization with no minimum-return guard — is the direct Nado analog of the Reserve Protocol M-03 finding.

### Citations

**File:** core/contracts/Clearinghouse.sol (L485-504)
```text
    function burnNlp(
        IEndpoint.BurnNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

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

**File:** core/contracts/Clearinghouse.sol (L511-517)
```text
        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
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
