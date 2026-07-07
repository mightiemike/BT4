### Title
Fast Withdrawal Front-Running Blocks Provider Transactions - (`File: core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`BaseWithdrawPool.submitFastWithdrawal` is a `public` function with no caller restriction. It sets `markedIdxs[idx] = true` unconditionally before completing its logic. Any observer of a pending fast-withdrawal transaction in the mempool can replay the identical calldata `(idx, transaction, signatures)` to pre-empt the legitimate caller, causing the legitimate transaction to revert with `"Withdrawal already submitted"`.

---

### Finding Description

`submitFastWithdrawal` follows this execution order:

```solidity
// BaseWithdrawPool.sol lines 81-114
function submitFastWithdrawal(
    uint64 idx,
    bytes calldata transaction,
    bytes[] calldata signatures
) public {
    require(!markedIdxs[idx], "Withdrawal already submitted");   // (1) guard
    require(idx > minIdx, "idx too small");
    markedIdxs[idx] = true;                                      // (2) flag set immediately

    Verifier v = Verifier(verifier);
    v.requireValidTxSignatures(transaction, idx, signatures);    // (3) verify sigs

    ...
    if (sendTo == msg.sender) {
        transferAmount -= uint128(fee);                          // (4a) fee deducted
    } else {
        safeTransferFrom(token, msg.sender, uint128(fee));       // (4b) fee pulled
    }
    handleWithdrawTransfer(token, sendTo, transferAmount);       // (5) transfer
}
```

The flag at step (2) is set before any caller-specific check. Because the function is `public` and all three arguments (`idx`, `transaction`, `signatures`) are observable in the mempool, any third party can replay the exact same call.

The critical path for a zero-cost attack: when the attacker **is** the `sendTo` address (i.e., the withdrawal recipient), branch (4a) applies — no `safeTransferFrom` is required. The fee is simply deducted from the transfer amount. The attacker needs no prior token approval and spends nothing beyond gas.

**Attack sequence:**

1. A fast-withdrawal provider broadcasts `submitFastWithdrawal(idx, tx, sigs)`.
2. The user (who is `sendTo`) observes this in the mempool and front-runs with identical calldata.
3. User's transaction executes: `markedIdxs[idx] = true`, user receives `transferAmount − fee`.
4. Provider's transaction reverts: `"Withdrawal already submitted"`.
5. Later, when the sequencer calls `submitWithdrawal` (the clearinghouse path), `markedIdxs[idx]` is already `true`, so it silently returns without transferring — correct behavior, but the provider earned nothing.

The provider's service is permanently disrupted for that `idx`. Because every fast-withdrawal `idx` is unique and observable, this attack can be applied to every single fast-withdrawal a provider attempts to process.

---

### Impact Explanation

**High.** Fast-withdrawal providers can be systematically griefed on every withdrawal they attempt to process. A provider who broadcasts `submitFastWithdrawal` can never guarantee their transaction will land — any user who is the `sendTo` can always pre-empt them. This makes the fast-withdrawal service economically unviable: providers cannot reliably earn fees, so rational providers will stop offering the service. The fast-withdrawal path (`submitFastWithdrawal`) becomes unusable as a provider-operated service.

The corrupted protocol state is `markedIdxs[idx]`, which is set by an unauthorized caller, permanently preventing the legitimate provider's transaction from executing for that `idx`.

---

### Likelihood Explanation

**High.** There are no preconditions for the attack when the attacker is the `sendTo`:
- No token approval needed (fee deducted from transfer, not pulled from attacker).
- No special role or privilege required.
- All calldata is public and replayable.
- The attack applies to every fast-withdrawal `idx` without exception.

Any user who wants to bypass the provider and self-process their fast withdrawal can do so trivially.

---

### Recommendation

Restrict `submitFastWithdrawal` so that only the intended caller can execute it for a given `idx`. The simplest fix — directly analogous to the recommendation in the reference report — is to bind the `markedIdxs` key to `msg.sender`:

```solidity
// Instead of:
markedIdxs[idx] = true;
require(!markedIdxs[idx], "Withdrawal already submitted");

// Use a per-caller mapping:
mapping(address => mapping(uint64 => bool)) public markedIdxs;
require(!markedIdxs[msg.sender][idx], "Withdrawal already submitted");
markedIdxs[msg.sender][idx] = true;
```

This ensures that a front-runner setting their own `markedIdxs[attacker][idx]` does not affect the provider's `markedIdxs[provider][idx]`. Alternatively, add an explicit allowlist of authorized fast-withdrawal callers.

---

### Proof of Concept

```
1. Deploy WithdrawPool with a funded token balance.
2. User submits a WithdrawCollateral transaction to the sequencer (idx = 5).
3. Provider constructs valid (idx=5, transaction, signatures) and broadcasts submitFastWithdrawal.
4. User (sendTo) observes the pending tx and front-runs with identical (5, transaction, signatures).
   - sendTo == msg.sender → branch (4a) → fee deducted from transferAmount, no approval needed.
   - markedIdxs[5] = true. User receives funds.
5. Provider's tx lands: require(!markedIdxs[5]) → reverts "Withdrawal already submitted".
6. Provider earns no fee. Attack cost: gas only.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** core/contracts/BaseWithdrawPool.sol (L81-88)
```text
    function submitFastWithdrawal(
        uint64 idx,
        bytes calldata transaction,
        bytes[] calldata signatures
    ) public {
        require(!markedIdxs[idx], "Withdrawal already submitted");
        require(idx > minIdx, "idx too small");
        markedIdxs[idx] = true;
```

**File:** core/contracts/BaseWithdrawPool.sol (L104-113)
```text
        if (sendTo == msg.sender) {
            require(transferAmount > uint128(fee), "Fee larger than balance");
            transferAmount -= uint128(fee);
        } else {
            safeTransferFrom(token, msg.sender, uint128(fee));
        }

        fees[productId] += fee;

        handleWithdrawTransfer(token, sendTo, transferAmount);
```

**File:** core/contracts/BaseWithdrawPool.sol (L116-132)
```text
    function submitWithdrawal(
        IERC20Base token,
        address sendTo,
        uint128 amount,
        uint64 idx
    ) public {
        require(msg.sender == clearinghouse);

        if (markedIdxs[idx]) {
            return;
        }
        markedIdxs[idx] = true;
        // set minIdx to most recent withdrawal submitted by sequencer
        minIdx = idx;

        handleWithdrawTransfer(token, sendTo, amount);
    }
```
