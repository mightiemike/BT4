### Title
Sanctioned Accounts Can Receive Collateral via `submitFastWithdrawal` Without Sanctions Check — (`core/contracts/BaseWithdrawPool.sol`)

---

### Summary

The `submitFastWithdrawal` function in `BaseWithdrawPool.sol` transfers collateral to a `sendTo` address without any call to `requireUnsanctioned`. This is asymmetric with the deposit path, which enforces sanctions checks on both `msg.sender` and the subaccount owner address. A sanctioned user can receive collateral through the fast-withdrawal path even after being added to the sanctions list.

---

### Finding Description

`depositCollateralWithReferral` in `Endpoint.sol` enforces sanctions on both the caller and the subaccount owner before accepting any funds: [1](#0-0) 

`submitSlowModeTransactionImpl` in `EndpointTx.sol` also enforces sanctions before queuing any slow-mode transaction: [2](#0-1) 

However, `submitFastWithdrawal` in `BaseWithdrawPool.sol` is a public function that verifies sequencer signatures and then immediately transfers tokens to `sendTo` with no sanctions check at any point: [3](#0-2) 

The `sendTo` address is resolved from the signed transaction payload. For `WithdrawCollateral` it resolves to the subaccount owner's address; for `WithdrawCollateralV2` it can be an **arbitrary address** supplied by the user: [4](#0-3) 

Neither branch checks `requireUnsanctioned` on the resolved `sendTo`.

The `requireUnsanctioned` helper performs a live, real-time lookup against the sanctions registry: [5](#0-4) 

Because the check is live, a user who was sanctioned *after* the sequencer signed their withdrawal transaction is still caught by the deposit-side check — but is **not** caught by `submitFastWithdrawal`, which never calls it.

---

### Impact Explanation

A sanctioned user retains the ability to receive collateral from the protocol through the fast-withdrawal path. The protocol's on-chain sanctions enforcement is structurally inconsistent: it blocks sanctioned addresses from depositing but does not block them from withdrawing via `submitFastWithdrawal`. This directly undermines the regulatory compliance guarantee that the sanctions integration is intended to provide, allowing a blacklisted address to extract funds from the protocol.

For `WithdrawCollateralV2`, the impact extends further: any user (sanctioned or not) can set `sendTo` to a sanctioned address, routing collateral to a blacklisted party through a publicly callable function.

---

### Likelihood Explanation

`submitFastWithdrawal` is a public, permissionless function. The only prerequisite is a valid set of sequencer-side signatures over the withdrawal transaction. A realistic trigger is:

1. User submits a withdrawal; the sequencer signs it.
2. User (or the `sendTo` address in a V2 withdrawal) is subsequently added to the sanctions list.
3. Anyone — including the sanctioned user themselves — calls `submitFastWithdrawal` with the pre-signed transaction.
4. Tokens are transferred to the sanctioned address with no on-chain check.

This scenario requires no privileged access, no governance capture, and no admin cooperation. The sequencer signature was legitimately obtained before the sanction event.

---

### Recommendation

Add a `requireUnsanctioned(sendTo)` call inside `submitFastWithdrawal` immediately before the token transfer, mirroring the pattern used in `depositCollateralWithReferral`:

```solidity
// In BaseWithdrawPool.submitFastWithdrawal, before handleWithdrawTransfer:
requireUnsanctioned(sendTo);
handleWithdrawTransfer(token, sendTo, transferAmount);
```

Because `BaseWithdrawPool` does not currently inherit `EndpointStorage`, the sanctions registry reference and `requireUnsanctioned` logic should either be pulled in via inheritance or duplicated as a local check against the same `ISanctionsList` instance used by `Endpoint`.

---

### Proof of Concept

1. Alice deposits collateral via `depositCollateralWithReferral` — sanctions check passes.
2. Alice submits a `WithdrawCollateralV2` transaction with `sendTo = aliceAddress`; the sequencer signs it and returns the signature set.
3. Alice is added to the OFAC sanctions list (her address is now `isSanctioned == true`).
4. Bob (or Alice herself) calls `BaseWithdrawPool.submitFastWithdrawal(idx, transaction, signatures)`.
5. `requireValidTxSignatures` passes (signatures are valid).
6. `resolveFastWithdrawal` returns `sendTo = aliceAddress`.
7. `handleWithdrawTransfer` transfers tokens to Alice — **no sanctions check is ever performed**.
8. Alice, a sanctioned address, successfully receives protocol collateral. [3](#0-2) [6](#0-5)

### Citations

**File:** core/contracts/Endpoint.sol (L123-135)
```text
    function depositCollateralWithReferral(
        bytes32 subaccount,
        uint32 productId,
        uint128 amount,
        string memory
    ) public {
        require(!RiskHelper.isIsolatedSubaccount(subaccount), ERR_UNAUTHORIZED);

        address sender = address(bytes20(subaccount));

        // depositor / depositee need to be unsanctioned
        requireUnsanctioned(msg.sender);
        requireUnsanctioned(sender);
```

**File:** core/contracts/EndpointTx.sol (L375-376)
```text
        requireUnsanctioned(sender);
        slowModeTxs[_slowModeConfig.txCount++] = IEndpoint.SlowModeTx({
```

**File:** core/contracts/BaseWithdrawPool.sol (L56-77)
```text
        if (txType == IEndpoint.TransactionType.WithdrawCollateral) {
            IEndpoint.SignedWithdrawCollateral memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateral)
            );
            return (
                signedTx.tx.productId,
                address(uint160(bytes20(signedTx.tx.sender))),
                signedTx.tx.amount
            );
        }
        if (txType == IEndpoint.TransactionType.WithdrawCollateralV2) {
            IEndpoint.SignedWithdrawCollateralV2 memory signedTx = abi.decode(
                transaction[1:],
                (IEndpoint.SignedWithdrawCollateralV2)
            );
            // V2 appendix is intentionally ignored until fast-withdraw features use it.
            address resolvedSendTo = signedTx.tx.sendTo == address(0)
                ? address(uint160(bytes20(signedTx.tx.sender)))
                : signedTx.tx.sendTo;
            return (signedTx.tx.productId, resolvedSendTo, signedTx.tx.amount);
        }
```

**File:** core/contracts/BaseWithdrawPool.sol (L81-114)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;

        Verifier v = Verifier(verifier);
        v.requireValidTxSignatures(transaction, idx, signatures);

        (
            uint32 productId,
            address sendTo,
            uint128 transferAmount
        ) = resolveFastWithdrawal(transaction);
        IERC20Base token = getToken(productId);

        require(transferAmount <= INT128_MAX, ERR_CONVERSION_OVERFLOW);

        int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);

        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
    }
```

**File:** core/contracts/EndpointStorage.sol (L121-123)
```text
    function requireUnsanctioned(address sender) internal view virtual {
        require(!sanctions.isSanctioned(sender), ERR_WALLET_SANCTIONED);
    }
```
