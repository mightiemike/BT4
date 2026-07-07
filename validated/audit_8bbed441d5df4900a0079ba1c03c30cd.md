### Title
Stale Fast-Withdrawal Signatures Replayable After Signer-Set Reduction Due to Missing Expiry and Non-Sequential `idx` — (`File: core/contracts/Verifier.sol`, `core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`requireValidTxSignatures` in `Verifier.sol` commits only `(chainid, idx, txn)` into the signed digest — no expiry timestamp is included. Separately, `submitFastWithdrawal` in `BaseWithdrawPool.sol` enforces only `idx > minIdx`, not a strict sequential counter. Together these two gaps allow a fast-withdrawal provider to hold pre-collected signatures indefinitely and replay them after the sequencer signer set shrinks, executing a fast withdrawal that was originally approved under a stricter (higher-count) quorum with only the reduced current quorum.

---

### Finding Description

**Root cause 1 — no expiry in the signed digest (`Verifier.sol` line 267–268)**

```solidity
bytes32 data = keccak256(
    abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
);
```

The signed message binds only `chainid`, `idx`, and the raw transaction bytes. There is no `expiredAt` / deadline field. A signature set collected today is cryptographically identical to one submitted a year from now. [1](#0-0) 

**Root cause 2 — `idx` is a lower-bound guard, not a sequential storage nonce (`BaseWithdrawPool.sol` line 87)**

```solidity
require(idx > minIdx, "idx too small");
```

`minIdx` is only updated by the sequencer-gated `submitWithdrawal` path. Any `idx` strictly greater than `minIdx` and not yet in `markedIdxs` is accepted. Multiple signed transactions with different `idx` values can all be valid simultaneously; the submitter chooses which to execute and when. [2](#0-1) 

**Root cause 3 — signer-set reduction silently lowers the effective quorum**

`requireValidTxSignatures` counts non-empty entries in the caller-supplied `signatures[]` array and requires `nSignatures == nSigner`. [3](#0-2) 

`nSigner` is decremented when `deletePubkey` is called. [4](#0-3) 

Because the digest does not commit to the signer set or a timestamp, a provider who collected all three signatures under a 3-of-3 regime can later submit only two of them (omitting the deleted signer's slot) under a 2-of-2 regime. The hash is unchanged; the two remaining signatures are still valid; `nSignatures == nSigner` passes.

---

### Impact Explanation

**Scenario A — Stale withdrawal executed after user liquidation**

1. User requests withdrawal of 1 000 USDC (sequencer assigns `idx = 50`).
2. Sequencer nodes (A, B, C; `nSigner = 3`) sign the fast-withdrawal payload.
3. Provider saves all three signatures but does not submit immediately.
4. User's account is liquidated; their on-chain balance drops to zero.
5. Months later, provider submits `submitFastWithdrawal(50, txn, sigs)`.
6. `markedIdxs[50]` is still `false`; `50 > minIdx`; all three signatures verify → pool transfers 1 000 USDC to the user.
7. When the sequencer later tries to settle `idx = 50` via `submitWithdrawal`, the user has no balance to debit; the pool is never reimbursed → **pool loses 1 000 USDC**.

**Scenario B — Quorum downgrade after signer removal**

1. `nSigner = 3`. Nodes A, B, C sign fast withdrawal `idx = 50`.
2. Node C is removed (`deletePubkey(2)`); `nSigner = 2`.
3. Provider submits with `signatures[0]` = A's sig, `signatures[1]` = B's sig, `signatures[2]` = empty.
4. `nSignatures = 2 == nSigner = 2` → passes. The transaction executes under a 2-of-3 approval that the protocol never explicitly granted.

**Scenario C — Ordering manipulation**

Provider holds signatures for two pending fast withdrawals (`idx = 50`, `idx = 51`). Both are `> minIdx`. Provider submits the more profitable one first, indefinitely deferring the other — directly mirroring the M-02 ordering attack.

---

### Impact

**High** — the fast-withdrawal pool can be drained of real ERC-20 tokens (USDC, etc.) by replaying stale signatures for users whose accounts have since been liquidated. The pool's liquidity providers bear the loss.

---

### Likelihood Explanation

**Low** — requires a provider to deliberately withhold submission, combined with either a signer-set change or a user liquidation event in the intervening period. Both conditions are realistic over a long-running protocol but require coincidence or deliberate timing.

---

### Recommendation

1. **Add an `expiredAt` deadline to the signed digest in `requireValidTxSignatures`:**

```solidity
function requireValidTxSignatures(
    bytes calldata txn,
    uint64 idx,
    uint64 expiredAt,          // new
    bytes[] calldata signatures
) public view {
    require(block.timestamp <= expiredAt, "signature expired");
    bytes32 data = keccak256(
        abi.encodePacked(uint256(block.chainid), uint256(idx), expiredAt, txn)
    );
    ...
}
```

2. **Enforce a sequential storage nonce instead of a lower-bound guard.** Maintain a `uint64 public nextIdx` in `BaseWithdrawPool` and require `idx == nextIdx++`, or at minimum require `idx == minIdx + 1` so only the immediately next expected transaction is accepted.

3. **Commit the current signer-set fingerprint into the digest** (e.g., a hash of all active pubkeys) so that signatures collected under one signer set cannot be replayed after the set changes.

---

### Proof of Concept

```
State:
  nSigner = 3  (pubkeys[0]=A, pubkeys[1]=B, pubkeys[2]=C)
  minIdx  = 0
  markedIdxs[50] = false

Step 1: Sequencer nodes A, B, C each sign:
  data = keccak256(abi.encodePacked(chainid, uint256(50), txn_withdraw_1000_USDC))

Step 2: Provider saves sig_A, sig_B, sig_C but does NOT call submitFastWithdrawal.

Step 3: User account is liquidated → balance = 0.

Step 4: Owner calls deletePubkey(2) → nSigner = 2.

Step 5: Provider calls:
  submitFastWithdrawal(
      50,
      txn_withdraw_1000_USDC,
      [sig_A, sig_B, ""]   // index 2 is empty
  )

  BaseWithdrawPool checks:
    !markedIdxs[50]  → true  ✓
    50 > minIdx(0)   → true  ✓
    markedIdxs[50] = true

  Verifier.requireValidTxSignatures:
    nSignatures = 2 (sig_A valid at index 0, sig_B valid at index 1)
    nSignatures == nSigner (2 == 2)  ✓

  Pool transfers 1 000 USDC to user.

Step 6: Sequencer later calls submitWithdrawal(..., 50):
    markedIdxs[50] == true → returns early, pool never reimbursed.

Result: WithdrawPool loses 1 000 USDC.
``` [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** core/contracts/Verifier.sol (L85-91)
```text
    function deletePubkey(uint256 index) public onlyOwner {
        if (!isPointNone(pubkeys[index])) {
            nSigner -= 1;
            delete pubkeys[index];
        }
        emit DeletePubkey(index);
    }
```

**File:** core/contracts/Verifier.sol (L261-289)
```text
    function requireValidTxSignatures(
        bytes calldata txn,
        uint64 idx,
        bytes[] calldata signatures
    ) public view {
        require(signatures.length <= 256, "too many signatures");
        bytes32 data = keccak256(
            abi.encodePacked(uint256(block.chainid), uint256(idx), txn)
        );
        bytes32 hashedMsg = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", data)
        );

        uint256 nSignatures = 0;
        for (uint256 i = 0; i < signatures.length; i++) {
            if (signatures[i].length > 0) {
                nSignatures += 1;
                require(
                    checkIndividualSignature(
                        hashedMsg,
                        signatures[i],
                        uint8(i)
                    ),
                    "invalid signature"
                );
            }
        }
        require(nSignatures == nSigner, "not enough signatures");
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
