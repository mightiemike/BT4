### Title
Withdrawal Fee Fully Bypassed via `WithdrawCollateralV2` User-Controlled `feeX18 = 0` — (`File: core/contracts/EndpointTx.sol`)

---

### Summary

`EndpointTx.processTransactionImpl` handles `WithdrawCollateralV2` by charging a user-supplied `feeX18` value that is only validated to be `>= 0` and `<= currentFeeX18`. Because the lower bound is zero, any user can sign a `WithdrawCollateralV2` transaction with `feeX18 = 0` and pay no withdrawal fee at all. The sequencer cannot modify the signed field without invalidating the EIP-712 signature, so it must submit the transaction exactly as signed.

---

### Finding Description

`WithdrawCollateral` (V1) in the fast path always charges the full configured fee:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
```

`WithdrawCollateralV2` instead charges a user-supplied value:

```solidity
int128 currentFeeX18 = spotEngine
    .getConfig(signedTx.tx.productId)
    .withdrawFeeX18;
require(signedTx.feeX18 >= 0);
require(signedTx.feeX18 <= currentFeeX18);
chargeFee(
    signedTx.tx.sender,
    signedTx.feeX18,       // <-- user-controlled, can be 0
    signedTx.tx.productId
);
```

The only constraints are `feeX18 >= 0` and `feeX18 <= currentFeeX18`. Setting `feeX18 = 0` satisfies both. The user signs the full transaction struct including `feeX18`; the sequencer cannot alter this field without breaking the EIP-712 signature validated by `validateSignedTx`. Therefore the sequencer is forced to submit the transaction exactly as signed, and the on-chain code accepts it with zero fee. [1](#0-0) 

The `chargeFee` function deducts the fee from the sender's spot balance and credits `sequencerFee`: [2](#0-1) 

When `feeX18 = 0`, neither deduction nor credit occurs, and the protocol collects nothing.

---

### Impact Explanation

The protocol's withdrawal fee (`withdrawFeeX18`) is entirely bypassable for any user who uses `WithdrawCollateralV2`. The `sequencerFee` accumulator receives zero, and the fee revenue that would normally be claimed via `DumpFees`/`claimSequencerFees` is lost. Every withdrawal that should have paid a fee pays nothing. This is a direct, repeatable accounting loss with no cap. [3](#0-2) 

---

### Likelihood Explanation

Exploitation requires only that a user sign a `WithdrawCollateralV2` transaction with `feeX18 = 0`. This is trivially achievable by any user who constructs their own signed transaction payload. The sequencer cannot reject it on-chain and cannot modify the signed field off-chain without breaking the signature. Any user aware of the V2 transaction type can exploit this on every withdrawal. [4](#0-3) 

---

### Recommendation

Remove the user-controlled `feeX18` field from `WithdrawCollateralV2` and always charge the full configured fee, mirroring the V1 behavior:

```solidity
chargeFee(
    signedTx.tx.sender,
    spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
    signedTx.tx.productId
);
```

If a discounted fee is intentionally desired for V2, enforce a non-zero minimum (e.g., `require(signedTx.feeX18 >= currentFeeX18)` or a protocol-defined floor), so the fee cannot be reduced to zero.

---

### Proof of Concept

1. User holds collateral in product `P` with configured `withdrawFeeX18 = F > 0`.
2. User constructs a `WithdrawCollateralV2` transaction with `feeX18 = 0`, `sendTo = <their address>`, and signs it with their EIP-712 key.
3. Sequencer receives the signed transaction and submits it via `processTransactionImpl`. It cannot alter `feeX18` without invalidating the signature.
4. On-chain: `require(0 >= 0)` passes; `require(0 <= F)` passes; `chargeFee(sender, 0, P)` is called — zero fee deducted.
5. `clearinghouse.withdrawCollateral(...)` executes normally, transferring the full requested amount.
6. The user has withdrawn collateral paying zero fee. Repeating this for all withdrawals eliminates all withdrawal fee revenue for the protocol. [5](#0-4)

### Citations

**File:** core/contracts/EndpointTx.sol (L134-141)
```text
    function chargeFee(
        bytes32 sender,
        int128 fee,
        uint32 productId
    ) internal {
        spotEngine.updateBalance(productId, sender, -fee);
        sequencerFee[productId] += fee;
    }
```

**File:** core/contracts/EndpointTx.sol (L425-436)
```text
            chargeFee(
                signedTx.tx.sender,
                spotEngine.getConfig(signedTx.tx.productId).withdrawFeeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                address(0),
                nSubmissions
            );
```

**File:** core/contracts/EndpointTx.sol (L437-465)
```text
        } else if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                signedTx.tx.sendTo == address(0)
            );
            int128 currentFeeX18 = spotEngine
                .getConfig(signedTx.tx.productId)
                .withdrawFeeX18;
            require(signedTx.feeX18 >= 0);
            require(signedTx.feeX18 <= currentFeeX18);
            chargeFee(
                signedTx.tx.sender,
                signedTx.feeX18,
                signedTx.tx.productId
            );
            clearinghouse.withdrawCollateral(
                signedTx.tx.sender,
                signedTx.tx.productId,
                signedTx.tx.amount,
                signedTx.tx.sendTo,
                nSubmissions
            );
```
