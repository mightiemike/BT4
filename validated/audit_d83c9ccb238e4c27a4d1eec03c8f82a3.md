### Title
Fast-Withdrawal Fee Truncates to Zero for Low-Decimal Tokens — (`core/contracts/BaseWithdrawPool.sol`)

---

### Summary

`fastWithdrawalFeeAmount` converts the native-decimal `amount` to an X18 fixed-point value, computes the fee in X18 space, then divides back by `multiplier = 10^(18 - decimals)`. For tokens with 0 or 1 decimals the multiplier is `10^18` or `10^17`, and for small-enough amounts the integer division `feeX18 / multiplier` truncates to exactly `0`. No `require(fee > 0)` guard exists anywhere in the call path.

---

### Finding Description

`fastWithdrawalFeeAmount` at [1](#0-0)  computes:

```
multiplier          = 10^(18 - decimals)
amountX18           = amount * multiplier
proportionalFeeX18  = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18)
                    = (1e15 * amountX18) / 1e18          // MathSD21x18.mul
                    = amountX18 / 1000
minFeeX18           = 5 * withdrawFeeX18
feeX18              = max(proportionalFeeX18, minFeeX18)
fee (returned)      = feeX18 / multiplier                // integer division
```

`MathSD21x18.mul` is confirmed as `(int256(x) * y) / ONE_X18` with `ONE_X18 = 1e18`. [2](#0-1) 

`FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000` (1e15 = 0.1%). [3](#0-2) 

**Concrete arithmetic for a 0-decimal token with `withdrawFeeX18 = 1e16` and `amount = 500`:**

| Variable | Value |
|---|---|
| `multiplier` | `10^18` |
| `amountX18` | `500 * 10^18` |
| `proportionalFeeX18` | `500 * 10^18 / 1000 = 5 * 10^17` |
| `minFeeX18` | `5 * 1e16 = 5 * 10^16` |
| `feeX18` | `max(5e17, 5e16) = 5e17` |
| `fee` | `5e17 / 10^18 = 0` ← truncates |

The threshold for a non-zero fee on a 0-decimal token is `amount >= 1000` (proportional path) **or** `withdrawFeeX18 >= 2e17` (floor path). Any amount in `[1, 999]` with `withdrawFeeX18 < 2e17` yields `fee = 0`.

`submitFastWithdrawal` then executes with `fee = 0` — no guard rejects it: [4](#0-3) 

- `sendTo == msg.sender` branch: `require(transferAmount > 0)` passes; `transferAmount -= 0`; full amount transferred to attacker.
- `sendTo != msg.sender` branch: `safeTransferFrom(token, msg.sender, 0)` — zero-amount transfer, no compensation collected.

There is no `require(fee > 0)` anywhere in the codebase (confirmed by grep). [5](#0-4) 

---

### Impact Explanation

An attacker who controls a subaccount with a 0-decimal (or 1-decimal) token balance can sign many `WithdrawCollateral` transactions for amounts in `[1, 999]` and call `submitFastWithdrawal` for each, acting as their own LP (`sendTo == msg.sender`). Each call:

1. Pays the attacker the full `transferAmount` from the pool.
2. Collects `fee = 0` — the pool earns nothing.
3. Marks the `idx` as used, so the sequencer will later replenish the pool for the same amount.

The pool is replenished per-withdrawal by the sequencer, so there is no permanent balance drain. The concrete impact is **complete fee evasion**: the fast-withdrawal fee invariant (LP must be compensated) is broken for every such withdrawal. LPs providing liquidity for low-decimal tokens earn zero fees regardless of withdrawal volume, making the pool economically unviable for those assets.

---

### Likelihood Explanation

**Conditional on deployment configuration:**

- Requires a token with 0 or 1 decimals to be listed. Such tokens are uncommon but not prohibited — the only guard is `require(decimals <= MAX_DECIMALS)`. [6](#0-5) 
- Requires `withdrawFeeX18 < 2e17` for 0-decimal tokens. The quote product hardcodes `withdrawFeeX18 = ONE = 1e18` [7](#0-6) , but non-quote products set this freely via `addOrUpdateProduct`. No minimum is enforced.
- The attacker signs their own withdrawal transactions — no external cooperation needed.

If both conditions are met, exploitation is trivial and repeatable.

---

### Recommendation

Add a post-computation guard in `fastWithdrawalFeeAmount` or in `submitFastWithdrawal`:

```solidity
int128 fee = fastWithdrawalFeeAmount(token, productId, transferAmount);
require(fee > 0, "Fast withdrawal fee rounds to zero");
```

Alternatively, enforce a minimum native-decimal fee of 1 inside `fastWithdrawalFeeAmount`:

```solidity
int128 result = feeX18 / int128(multiplier);
return result > 0 ? result : int128(1);
```

Also consider enforcing a protocol-level minimum `withdrawFeeX18` in `addOrUpdateProduct` for tokens with fewer than 6 decimals.

---

### Proof of Concept

```solidity
// Deploy a 0-decimal ERC20 token, list it as productId=42 with withdrawFeeX18=1e16
// Fund the WithdrawPool with 10_000 units of the token

// Attacker signs 10 WithdrawCollateral txs, each with amount=999
for (uint i = 0; i < 10; i++) {
    pool.submitFastWithdrawal(
        minIdx + 1 + i,
        buildWithdrawTx(productId=42, sender=attacker, amount=999),
        validSignatures
    );
}
// After 10 calls: attacker received 9990 tokens, pool collected fee=0 each time
// assert pool.fees(42) == 0   // ← fee invariant broken
```

Each call passes `require(transferAmount > uint128(fee))` as `999 > 0`, and `safeTransferFrom(token, msg.sender, 0)` is a no-op (or succeeds silently). [8](#0-7)

### Citations

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

**File:** core/contracts/BaseWithdrawPool.sol (L134-149)
```text
    function fastWithdrawalFeeAmount(
        IERC20Base token,
        uint32 productId,
        uint128 amount
    ) public view returns (int128) {
        uint8 decimals = token.decimals();
        require(decimals <= MAX_DECIMALS);
        int256 multiplier = int256(10**(MAX_DECIMALS - uint8(decimals)));
        int128 amountX18 = int128(amount) * int128(multiplier);

        int128 proportionalFeeX18 = FAST_WITHDRAWAL_FEE_RATE.mul(amountX18);
        int128 minFeeX18 = 5 * spotEngine().getConfig(productId).withdrawFeeX18;

        int128 feeX18 = MathHelper.max(proportionalFeeX18, minFeeX18);
        return feeX18 / int128(multiplier);
    }
```

**File:** core/contracts/libraries/MathSD21x18.sol (L54-60)
```text
    function mul(int128 x, int128 y) internal pure returns (int128) {
        unchecked {
            int256 result = (int256(x) * y) / ONE_X18;
            require(result >= MIN_X18 && result <= MAX_X18, ERR_OVERFLOW);
            return int128(result);
        }
    }
```

**File:** core/contracts/common/Constants.sol (L25-25)
```text
int128 constant FAST_WITHDRAWAL_FEE_RATE = 1_000_000_000_000_000; // 0.1%
```

**File:** core/contracts/SpotEngine.sol (L29-29)
```text
            withdrawFeeX18: ONE, // 1
```
