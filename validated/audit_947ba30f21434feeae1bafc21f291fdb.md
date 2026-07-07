### Title
`oraclePriceX18` Excluded from User EIP-712 Signature Allows Sequencer to Inject Arbitrary NLP Mint/Burn Price — (`core/contracts/Verifier.sol`, `core/contracts/EndpointTx.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The `MintNlp` and `BurnNlp` EIP-712 type signatures do not commit to `oraclePriceX18`. The sequencer appends this field to the `SignedMintNlp`/`SignedBurnNlp` struct after the user signs, and no on-chain validation bounds it against any reference price. Because NLP share issuance and quote redemption are computed directly from this unconstrained value, the sequencer can silently set any price, causing users to receive far fewer NLP shares on mint or far less quote on burn.

---

### Finding Description

**Step 1 — What the user signs.**

`Verifier.computeDigest` for `MintNlp` hashes only `(sender, quoteAmount, nonce)`:

```
MINT_NLP_SIGNATURE = "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)"
``` [1](#0-0) 

The digest is built from only those three fields: [2](#0-1) 

`oraclePriceX18` and `nlpPoolRebalanceX18` — both present in `SignedMintNlp` — are **not hashed**. The same omission applies to `BurnNlp`: [3](#0-2) [4](#0-3) 

**Step 2 — The struct layout confirms the gap.**

`SignedMintNlp` carries `oraclePriceX18` and `nlpPoolRebalanceX18` as sequencer-appended fields outside the signed `tx` body: [5](#0-4) [6](#0-5) 

**Step 3 — The unsigned price is used directly for share calculation.**

`EndpointTx.processTransactionImpl` writes `signedTx.oraclePriceX18` into the global price map and forwards it to `clearinghouse.mintNlp` without any bounds check: [7](#0-6) [8](#0-7) 

**Step 4 — NLP amount is a direct function of that price.**

In `Clearinghouse.mintNlp`, the NLP shares issued equal `quoteAmount / oraclePriceX18`: [9](#0-8) 

In `Clearinghouse.burnNlp`, the quote returned equals `nlpAmount * oraclePriceX18`: [10](#0-9) 

There is no comparison against `priceX18[NLP_PRODUCT_ID]`, no TWAP check, and no maximum-deviation guard anywhere in either path.

---

### Impact Explanation

**MintNlp theft:** If the sequencer inflates `oraclePriceX18` by 100×, the user deposits `quoteAmount` USDC but receives `1/100` of the expected NLP shares. The remaining value accrues to existing NLP holders (the N_ACCOUNT pool).

**BurnNlp theft:** If the sequencer deflates `oraclePriceX18` by 100×, the user burns their NLP shares but receives `1/100` of the expected USDC back. The shortfall stays in the pool.

In both cases the user's deposited or redeemed value is silently redirected with no on-chain recourse. The corrupted state delta is `spotEngine.balances[NLP_PRODUCT_ID][sender]` (too few shares minted) or `spotEngine.balances[QUOTE_PRODUCT_ID][sender]` (too little quote returned).

---

### Likelihood Explanation

Exploitation requires the sequencer to submit a batch containing a manipulated `oraclePriceX18`. The sequencer is the only entity permitted to call `submitTransactionsChecked`: [11](#0-10) 

This constrains the attack to a malicious or compromised sequencer. However, the design flaw is independently harmful even with an honest sequencer: users have **no slippage protection** because they cannot commit to a minimum NLP price or maximum burn price in their signature. Any sequencer-side latency, reordering, or off-chain price feed error silently changes the user's economic outcome with no on-chain check to catch it.

---

### Recommendation

Include `oraclePriceX18` in the EIP-712 type string and digest for both `MintNlp` and `BurnNlp`:

```
MintNlp(bytes32 sender,uint128 quoteAmount,int128 oraclePriceX18,uint64 nonce)
BurnNlp(bytes32 sender,uint128 nlpAmount,int128 oraclePriceX18,uint64 nonce)
```

Additionally, add an on-chain deviation guard in `mintNlp`/`burnNlp` that compares `oraclePriceX18` against the last stored `priceX18[NLP_PRODUCT_ID]` and reverts if the delta exceeds a configurable threshold (analogous to the IchiVault spot/TWAP delta check recommended in the source report).

---

### Proof of Concept

1. User signs `MintNlp { sender, quoteAmount: 1000e18, nonce: N }` — the signature covers only those three fields per `MINT_NLP_SIGNATURE`.
2. Sequencer constructs `SignedMintNlp { tx: <above>, signature: <user sig>, oraclePriceX18: 1000e18, nlpPoolRebalanceX18: [...] }` where the true NLP price is `1e18`.
3. Sequencer includes this in a `submitTransactionsChecked` batch.
4. `EndpointTx` validates the user's signature (passes — price not in digest), then calls `clearinghouse.mintNlp(..., 1000e18, ...)`.
5. `nlpAmount = 1000e18 / 1000e18 = 1` — user receives 1 unit of NLP instead of the expected `1000` units.
6. The 999 units of NLP value remain in the pool, benefiting existing holders.

The inverse applies to `BurnNlp` with a deflated `oraclePriceX18`.

### Citations

**File:** core/contracts/Verifier.sol (L26-27)
```text
    string internal constant MINT_NLP_SIGNATURE =
        "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L28-29)
```text
    string internal constant BURN_NLP_SIGNATURE =
        "BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)";
```

**File:** core/contracts/Verifier.sol (L373-385)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedMintNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(MINT_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.quoteAmount,
                    signedTx.tx.nonce
                )
            );
```

**File:** core/contracts/Verifier.sol (L386-398)
```text
        } else if (txType == IEndpoint.TransactionType.BurnNlp) {
            IEndpoint.SignedBurnNlp memory signedTx = abi.decode(
                transactionBody,
                (IEndpoint.SignedBurnNlp)
            );
            digest = keccak256(
                abi.encode(
                    keccak256(bytes(BURN_NLP_SIGNATURE)),
                    signedTx.tx.sender,
                    signedTx.tx.nlpAmount,
                    signedTx.tx.nonce
                )
            );
```

**File:** core/contracts/interfaces/IEndpoint.sol (L118-123)
```text
    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
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

**File:** core/contracts/EndpointTx.sol (L547-553)
```text
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/EndpointTx.sol (L567-573)
```text
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.burnNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/Clearinghouse.sol (L464-466)
```text
        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);
```

**File:** core/contracts/Clearinghouse.sol (L502-504)
```text
        int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
        int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
        quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
```

**File:** core/contracts/Endpoint.sol (L278-279)
```text
        validateSubmissionIdx(idx);
        require(msg.sender == sequencer);
```
