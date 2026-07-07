### Title
MintNlp and BurnNlp lack slippage protection and expiration — user receives unbounded unfavorable NLP/quote amounts - (File: `core/contracts/interfaces/IEndpoint.sol`, `core/contracts/Clearinghouse.sol`)

---

### Summary

The `MintNlp` and `BurnNlp` transaction types allow users to exchange quote tokens for NLP tokens (and vice versa) at a price determined entirely by a sequencer-supplied `oraclePriceX18`. This price is **not covered by the user's EIP-712 signature** and there is no minimum-received-amount check. Additionally, neither struct contains an expiration field, so a pending intent can be executed at any future time at an arbitrarily unfavorable price.

---

### Finding Description

The `MintNlp` struct only contains `sender`, `quoteAmount`, and `nonce`: [1](#0-0) 

The `BurnNlp` struct only contains `sender`, `nlpAmount`, and `nonce`: [2](#0-1) 

The EIP-712 type strings used to compute the digest that the user signs confirm that `oraclePriceX18` is **excluded** from the signed payload:

```
"MintNlp(bytes32 sender,uint128 quoteAmount,uint64 nonce)"
"BurnNlp(bytes32 sender,uint128 nlpAmount,uint64 nonce)"
``` [3](#0-2) 

The digest computation for both types only hashes the three fields above, not the oracle price: [4](#0-3) 

The `oraclePriceX18` is instead carried in the outer `SignedMintNlp` / `SignedBurnNlp` wrapper and is supplied unilaterally by the sequencer at execution time: [5](#0-4) 

In `Clearinghouse.mintNlp`, the NLP amount the user receives is computed as:

```solidity
int128 nlpAmount = quoteAmount.div(oraclePriceX18);
``` [6](#0-5) 

There is no check that `nlpAmount >= someUserSpecifiedMinimum`. A higher `oraclePriceX18` directly reduces the NLP minted.

In `Clearinghouse.burnNlp`, the quote amount returned is:

```solidity
int128 quoteAmount = nlpAmount.mul(oraclePriceX18);
int128 burnFee = MathHelper.max(ONE, quoteAmount / 1000);
quoteAmount = MathHelper.max(0, quoteAmount - burnFee);
``` [7](#0-6) 

There is no check that `quoteAmount >= someUserSpecifiedMinimum`. A lower `oraclePriceX18` directly reduces the quote returned.

By contrast, the `Order` struct used for spot/perp trading **does** include a `uint64 expiration` field: [8](#0-7) 

which is enforced via `_expired(expiration)` in `OffchainExchange`. No equivalent protection exists for NLP mint/burn.

---

### Impact Explanation

A user who submits a `MintNlp` intent expecting to receive NLP at price P has no on-chain guarantee about the price at which the sequencer will execute it. If the NLP oracle price rises between submission and execution, the user receives proportionally fewer NLP tokens with no recourse. Symmetrically, a `BurnNlp` user who expects to receive Q quote tokens can receive arbitrarily less if the oracle price falls. Because there is no expiration field, a stale intent can be executed at any future time when market conditions are maximally unfavorable to the user.

---

### Likelihood Explanation

NLP price is a function of the total value of assets in the NLP pools divided by NLP supply. During volatile market conditions this price can move significantly within the time between a user submitting a signed intent and the sequencer including it in a batch. No special attacker capability is required — the gap between intent submission and sequencer execution is a normal operational condition.

---

### Recommendation

1. Add a `minNlpAmount` field to `MintNlp` and enforce `nlpAmount >= txn.minNlpAmount` in `Clearinghouse.mintNlp`.
2. Add a `minQuoteAmount` field to `BurnNlp` and enforce `quoteAmount >= txn.minQuoteAmount` in `Clearinghouse.burnNlp`.
3. Add a `uint64 expiration` field to both `MintNlp` and `BurnNlp` (mirroring `Order`) and include it in the EIP-712 type string and digest, then reject execution if `expiration <= block.timestamp`.
4. Include `oraclePriceX18` in the EIP-712 digest so the user commits to the exact price at which they are willing to transact, or alternatively include a `maxOraclePriceX18` / `minOraclePriceX18` bound.

---

### Proof of Concept

1. User signs a `MintNlp` with `quoteAmount = 1000e18` when NLP price is `1.00` (expecting ~1000 NLP).
2. NLP price rises to `2.00` before the sequencer processes the transaction.
3. Sequencer submits the transaction with `oraclePriceX18 = 2e18`.
4. `Clearinghouse.mintNlp` computes `nlpAmount = 1000e18 / 2e18 = 500` — user receives only 500 NLP.
5. No on-chain check rejects this; the user's signature is valid because `oraclePriceX18` was never part of the signed digest.
6. The same scenario applies to `BurnNlp` in reverse: user burns 1000 NLP expecting 1000 quote, but sequencer supplies a depressed price and user receives 500 quote minus the burn fee. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** core/contracts/interfaces/IEndpoint.sol (L112-116)
```text
    struct MintNlp {
        bytes32 sender;
        uint128 quoteAmount;
        uint64 nonce;
    }
```

**File:** core/contracts/interfaces/IEndpoint.sol (L118-136)
```text
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

**File:** core/contracts/Verifier.sol (L373-398)
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

**File:** core/contracts/Clearinghouse.sol (L453-483)
```text
    function mintNlp(
        IEndpoint.MintNlp calldata txn,
        int128 oraclePriceX18,
        IEndpoint.NlpPool[] calldata nlpPools,
        int128[] calldata nlpPoolRebalanceX18
    ) external onlyEndpoint {
        require(!RiskHelper.isIsolatedSubaccount(txn.sender), ERR_UNAUTHORIZED);

        ISpotEngine spotEngine = _spotEngine();
        spotEngine.updatePrice(NLP_PRODUCT_ID, oraclePriceX18);

        require(txn.quoteAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);
        int128 quoteAmount = int128(txn.quoteAmount);
        int128 nlpAmount = quoteAmount.div(oraclePriceX18);

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] >= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, -nlpAmount);

        spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, -quoteAmount);
        _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);

        require(
            getHealth(txn.sender, IProductEngine.HealthType.INITIAL) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/Clearinghouse.sol (L485-530)
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

        _validateNlpRebalance(nlpPools, nlpPoolRebalanceX18, -quoteAmount);
        for (uint128 i = 0; i < nlpPoolRebalanceX18.length; i++) {
            require(nlpPoolRebalanceX18[i] <= 0, ERR_INVALID_NLP_REBALANCE);
        }

        spotEngine.updateBalance(NLP_PRODUCT_ID, txn.sender, -nlpAmount);
        spotEngine.updateBalance(NLP_PRODUCT_ID, N_ACCOUNT, nlpAmount);

        if (quoteAmount > 0) {
            spotEngine.updateBalance(QUOTE_PRODUCT_ID, txn.sender, quoteAmount);
            _applyNlpRebalance(spotEngine, nlpPools, nlpPoolRebalanceX18);
        }

        require(
            spotEngine.getBalance(NLP_PRODUCT_ID, txn.sender).amount >= 0,
            ERR_SUBACCT_HEALTH
        );
        // Burning NLP can decrease health if the burn fee exceeds the health improvement
        // from the withdrawal. This check prevents malicious actors from deliberately
        // creating unhealthy subaccounts through NLP burns.
        require(
            getHealth(txn.sender, IProductEngine.HealthType.MAINTENANCE) >= 0,
            ERR_SUBACCT_HEALTH
        );
    }
```

**File:** core/contracts/EndpointTx.sol (L534-573)
```text
        } else if (txType == IEndpoint.TransactionType.MintNlp) {
            IEndpoint.SignedMintNlp memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedMintNlp)
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
            clearinghouse.mintNlp(
                signedTx.tx,
                signedTx.oraclePriceX18,
                nlpPools,
                signedTx.nlpPoolRebalanceX18
            );
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
