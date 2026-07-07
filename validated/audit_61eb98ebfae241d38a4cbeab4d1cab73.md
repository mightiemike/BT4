### Title
Linked Signer Authorization Over-Extended to Isolated Subaccounts — (`core/contracts/EndpointTx.sol`)

---

### Summary

A linked signer authorized for a parent subaccount automatically inherits signing authority over every isolated subaccount derived from that parent. This mirrors the ENS M-01 class: an approval granted in one context (the parent subaccount) silently extends to a separate, risk-isolated context (isolated subaccounts) that the user never intended to delegate.

---

### Finding Description

`getLinkedSigner` in `EndpointTx.sol` resolves the effective signer for any subaccount. For isolated subaccounts it unconditionally returns the **parent's** linked signer instead of the isolated subaccount's own entry:

```solidity
// EndpointTx.sol L143-L157
function getLinkedSigner(bytes32 subaccount)
    public view virtual returns (address)
{
    return
        RiskHelper.isIsolatedSubaccount(subaccount)
            ? linkedSigners[
                IOffchainExchange(offchainExchange).getParentSubaccount(
                    subaccount
                )
            ]
            : linkedSigners[subaccount];
}
``` [1](#0-0) 

This linked signer is then passed into every signature-validation call that sets `allowLinkedSigner = true`. The `TransferQuote` transaction type does exactly that:

```solidity
// EndpointTx.sol L593-L614
validateSignedTx(
    signedTx.tx.sender,   // isolated subaccount
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true                  // allowLinkedSigner
);
``` [2](#0-1) 

`Clearinghouse.transferQuote` permits an isolated subaccount to transfer its quote balance back to its parent, and enforces only that the address prefix matches and the recipient is the registered parent:

```solidity
// Clearinghouse.sol L222-L244
require(bytes20(txn.sender) == bytes20(txn.recipient), ERR_UNAUTHORIZED);
...
if (RiskHelper.isIsolatedSubaccount(txn.sender)) {
    require(
        IOffchainExchange(offchainExchange).getParentSubaccount(txn.sender)
            == txn.recipient,
        ERR_UNAUTHORIZED
    );
}
``` [3](#0-2) 

Because `getLinkedSigner` for the isolated subaccount resolves to the parent's linked signer, that linked signer can produce a valid signature for a `TransferQuote` originating from the isolated subaccount — an action the user never authorized when they set the linked signer on their parent subaccount.

Additionally, `WithdrawCollateral` (V1) also passes `allowLinkedSigner = true`, so after the quote is moved to the parent the same linked signer can sign a withdrawal from the parent:

```solidity
// EndpointTx.sol L418-L436
validateSignedTx(
    signedTx.tx.sender,
    signedTx.tx.nonce,
    transaction,
    signedTx.signature,
    true   // allowLinkedSigner
);
``` [4](#0-3) 

---

### Impact Explanation

A malicious or compromised linked signer can:

1. Sign a `TransferQuote` transaction whose `sender` is any isolated subaccount of the parent, draining its quote balance to the parent.
2. Sign a `WithdrawCollateral` transaction for the parent subaccount, withdrawing the drained funds to the parent's wallet address.

The isolated subaccount, stripped of its margin, becomes undercollateralized and subject to liquidation, destroying the user's risk-isolated position. The concrete corrupted state is the isolated subaccount's quote balance and the parent's collateral balance.

---

### Likelihood Explanation

Setting a linked signer is a standard operation for users who delegate order-signing to a trading bot or hot wallet. Any user who has both (a) set a linked signer on their parent subaccount and (b) created isolated subaccounts is exposed. The linked signer need only be malicious or compromised — no admin access, governance capture, or sequencer compromise is required. The entry path is fully unprivileged and reachable through the normal `submitTransactionsChecked` → `processTransactionImpl` → `TransferQuote` flow.

---

### Recommendation

`getLinkedSigner` should not inherit the parent's linked signer for isolated subaccounts. Isolated subaccounts should either have no linked signer at all, or require an explicit, separate `LinkSigner` registration scoped to the isolated subaccount. The fix is to remove the inheritance branch:

```solidity
function getLinkedSigner(bytes32 subaccount)
    public view virtual returns (address)
{
    // Do NOT inherit parent's linked signer for isolated subaccounts
    if (RiskHelper.isIsolatedSubaccount(subaccount)) {
        return address(0);
    }
    return linkedSigners[subaccount];
}
``` [1](#0-0) 

---

### Proof of Concept

1. Alice sets `linkedSigners[aliceMain]` = `bot` via a `LinkSigner` transaction.
2. Alice creates isolated subaccount `aliceIso` (derived from `aliceMain`).
3. Alice deposits margin into `aliceIso` and opens a leveraged perp position.
4. `bot` (malicious or compromised) constructs a `TransferQuote` transaction with `sender = aliceIso`, `recipient = aliceMain`, `amount = aliceIso.quoteBalance`.
5. `bot` signs the transaction. `getLinkedSigner(aliceIso)` returns `bot` (the parent's linked signer), so `validateSignature` passes.
6. `aliceIso`'s quote balance is transferred to `aliceMain`; `aliceIso` is now undercollateralized and liquidatable.
7. `bot` signs a `WithdrawCollateral` for `aliceMain`, withdrawing the transferred funds to Alice's address — but the isolated position is destroyed. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** core/contracts/EndpointTx.sol (L143-157)
```text
    function getLinkedSigner(bytes32 subaccount)
        public
        view
        virtual
        returns (address)
    {
        return
            RiskHelper.isIsolatedSubaccount(subaccount)
                ? linkedSigners[
                    IOffchainExchange(offchainExchange).getParentSubaccount(
                        subaccount
                    )
                ]
                : linkedSigners[subaccount];
    }
```

**File:** core/contracts/EndpointTx.sol (L418-436)
```text
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
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

**File:** core/contracts/EndpointTx.sol (L593-614)
```text
        } else if (txType == IEndpoint.TransactionType.TransferQuote) {
            IEndpoint.SignedTransferQuote memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedTransferQuote)
            );
            _recordSubaccount(signedTx.tx.recipient);
            validateSignedTx(
                signedTx.tx.sender,
                signedTx.tx.nonce,
                transaction,
                signedTx.signature,
                true
            );
            if (
                RiskHelper.isIsolatedSubaccount(signedTx.tx.recipient) ||
                RiskHelper.isIsolatedSubaccount(signedTx.tx.sender)
            ) {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE / 10);
            } else {
                chargeFee(signedTx.tx.sender, HEALTHCHECK_FEE);
            }
            clearinghouse.transferQuote(signedTx.tx);
```

**File:** core/contracts/Clearinghouse.sol (L222-244)
```text
        require(
            bytes20(txn.sender) == bytes20(txn.recipient),
            ERR_UNAUTHORIZED
        );
        address offchainExchange = IEndpoint(getEndpoint())
            .getOffchainExchange();
        if (RiskHelper.isIsolatedSubaccount(txn.sender)) {
            // isolated subaccounts can only transfer quote back to parent
            require(
                IOffchainExchange(offchainExchange).getParentSubaccount(
                    txn.sender
                ) == txn.recipient,
                ERR_UNAUTHORIZED
            );
        } else if (RiskHelper.isIsolatedSubaccount(txn.recipient)) {
            // regular subaccounts can transfer quote to active isolated subaccounts
            require(
                IOffchainExchange(offchainExchange).isIsolatedSubaccountActive(
                    txn.sender,
                    txn.recipient
                ),
                ERR_UNAUTHORIZED
            );
```
