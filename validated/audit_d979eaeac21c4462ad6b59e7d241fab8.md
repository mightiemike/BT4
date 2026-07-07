### Title
Missing Deadline in `MintNlp`/`BurnNlp` Signed Transactions Allows Execution at Unfavorable Oracle Prices — (File: `core/contracts/interfaces/IEndpoint.sol`)

---

### Summary

The `MintNlp` and `BurnNlp` signed transaction structs in `IEndpoint.sol` contain no expiration or deadline field. Their EIP-712 digests, computed in `Verifier.sol`, cover only `sender`, `quoteAmount`/`nlpAmount`, and `nonce`. Critically, the `oraclePriceX18` field — which determines the NLP token exchange rate at execution time — is **not included in the signed digest** and is supplied unilaterally by the sequencer. Together, the absence of a deadline and the unsigned oracle price mean a user who signs a mint or burn request has no temporal or price protection: the sequencer can execute the transaction at any future time using any oracle price it chooses.

---

### Finding Description

`IEndpoint.MintNlp` and `IEndpoint.BurnNlp` are defined as:

```solidity
struct MintNlp {
    bytes32 sender;
    uint128 quoteAmount;
    uint64 nonce;
}

struct BurnNlp {
    bytes32 sender;
    uint128 nlpAmount;
    uint64 nonce;
}
```

Neither struct contains an `expiration` or `deadline` field. [1](#0-0) 

The EIP-712 type strings and digest construction in `Verifier.sol` confirm that only `sender`, `quoteAmount`/`nlpAmount`, and `nonce` are signed:

```
"MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)"
"BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)"
``` [2](#0-1) 

The digest computation for `MintNlp` encodes only those three fields:

```solidity
digest = keccak256(
    abi.encode(
        keccak256(bytes(MINT_NLP_SIGNATURE)),
        signedTx.tx.sender,
        signedTx.tx.quoteAmount,
        signedTx.tx.nonce
    )
);
``` [3](#0-2) 

The `SignedMintNlp` and `SignedBurnNlp` structs carry `oraclePriceX18` and `nlpPoolRebalanceX18` as sequencer-supplied fields outside the signed body:

```solidity
struct SignedMintNlp {
    MintNlp tx;
    bytes signature;
    int128 oraclePriceX18;
    int128[] nlpPoolRebalanceX18;
}
``` [4](#0-3) 

In `EndpointTx.processTransactionImpl`, the sequencer-supplied `oraclePriceX18` is passed directly to `clearinghouse.mintNlp`/`burnNlp` after signature validation, with no check that the price falls within any user-specified bound and no check that the transaction is being executed within a user-specified time window:

```solidity
validateSignedTx(signedTx.tx.sender, signedTx.tx.nonce, transaction, signedTx.signature, true);
chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
clearinghouse.mintNlp(signedTx.tx, signedTx.oraclePriceX18, nlpPools, signedTx.nlpPoolRebalanceX18);
``` [5](#0-4) 

By contrast, `IEndpoint.Order` — used for spot and perp trading — does include an `expiration` field that is both signed and enforced via `_expired()` in `OffchainExchange._validateOrder()`:

```solidity
struct Order {
    bytes32 sender;
    int128 priceX18;
    int128 amount;
    uint64 expiration;   // <-- present for orders
    uint64 nonce;
    uint128 appendix;
}
``` [6](#0-5) 

```solidity
function _expired(uint64 expiration) internal view returns (bool) {
    return expiration <= getOracleTime();
}
``` [7](#0-6) 

The protection that exists for order matching is entirely absent for NLP mint/burn.

---

### Impact Explanation

A user who signs a `MintNlp` request for `quoteAmount` Q at time T₀ intends to receive NLP tokens at the prevailing oracle price at T₀. Because no deadline is encoded in the signed data and `oraclePriceX18` is not signed, the sequencer may execute the transaction at time T₁ >> T₀ using an oracle price that is significantly higher. The user receives proportionally fewer NLP tokens for the same Q quote spent — a direct, quantifiable asset loss. The symmetric case applies to `BurnNlp`: delayed execution at a lower oracle price yields less quote for the same NLP burned. The corrupted state is the user's on-chain balance in `SpotEngine` (quote) and the NLP token balance, both of which are permanently settled by `clearinghouse.mintNlp`/`burnNlp` with no recourse.

---

### Likelihood Explanation

The sequencer is a trusted entity in the Nado architecture, which reduces the probability of deliberate exploitation. However, the vulnerability does not require the sequencer to be "compromised" in the traditional sense: operational delays (maintenance windows, congestion, prioritization), a partially-compromised sequencer key, or a sequencer operator acting in self-interest are all sufficient to trigger the impact. Because the user has no on-chain mechanism to cancel a pending signed transaction (doing so requires burning the nonce with a different transaction, which itself requires sequencer cooperation), the user is fully exposed for the lifetime of the pending request. The likelihood is **medium-low** given the trusted sequencer model, but the impact is **high** and the root cause is a concrete, fixable design gap.

---

### Recommendation

1. Add an `expiration` (or `deadline`) field to `MintNlp` and `BurnNlp` structs in `IEndpoint.sol`.
2. Include `expiration` in the EIP-712 type strings and digest computation in `Verifier.sol` (analogous to how `Order.expiration` is included).
3. In `EndpointTx.processTransactionImpl`, enforce `require(block.timestamp <= signedTx.tx.expiration, "expired")` before executing the mint/burn.
4. Optionally, include `oraclePriceX18` in the signed digest so users can specify a maximum acceptable price for `MintNlp` and a minimum acceptable price for `BurnNlp`, providing slippage protection equivalent to the `priceMarginBps` check in the referenced report.

---

### Proof of Concept

1. User signs `MintNlp{sender: alice, quoteAmount: 10_000e18, nonce: 5}` when NLP oracle price is 1.00 USDC/NLP. Expected receipt: ~10,000 NLP tokens.
2. User submits the signed transaction to the sequencer.
3. Sequencer holds the transaction for 48 hours. NLP oracle price rises to 2.00 USDC/NLP.
4. Sequencer calls `processTransactionImpl` with `oraclePriceX18 = 2e18`.
5. `validateSignedTx` passes (nonce and signature are valid; no expiration check exists).
6. `clearinghouse.mintNlp` executes at the 2.00 price: alice receives ~5,000 NLP tokens instead of ~10,000.
7. Alice has lost 5,000 NLP tokens of value with no on-chain recourse. The nonce is consumed, so the original intent cannot be re-executed at the original price.

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L112-136)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }

    struct SignedMintNlp {
        MintNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }

    struct BurnNlp {
        bytes32 sender;
        uint128 nlpAmount;
        uint64 nonce;
    }

    struct SignedBurnNlp {
        BurnNlp tx;
        bytes signature;
        int128 oraclePriceX18;
        int128[] nlpPoolRebalanceX18;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L261-268)
```text
    struct Order {
        bytes32 sender;
        int128 priceX18;
        int128 amount;
        uint64 expiration;
        uint64 nonce;
        uint128 appendix;
    }
```

**File:** core/contracts/Verifier.sol (L26-29)
```text
    string internal constant MINT_NLP_SIGNATURE =
        "MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)";
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

**File:** core/contracts/EndpointTx.sol (L539-553)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            priceX18[NLP_PRODUCT_ID] = signedTx.oraclePriceX18;
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
```

**File:** core/contracts/OffchainExchange.sol (L345-347)
```text
    function _expired(uint64 expiration) internal view returns (bool) {
        return expiration <= getOracleTime();
    }
```
