### Title
Division by zero in vault `accrue` treasury-lp minting can permanently freeze all vault operations - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar` and identical logic in `v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`)

### Summary
Every vault's `accrue` function computes a treasury LP mint amount by dividing by `(- (total-assets-preview) reserve-inc)`. If `total-assets-preview` ever equals `reserve-inc`, this denominator becomes zero, and Clarity's native `/` operator aborts the transaction. Since `accrue` is invoked via `try!` at the start of every deposit, redeem, borrow, repay, and transfer, hitting this exact-equality state permanently freezes all vault activity.

### Finding Description
`mul-div-down` is defined as a raw division with no zero-guard: [1](#0-0) 

In `accrue`, the treasury LP shares minted to `.dao-treasury` are computed as: [2](#0-1) 

Here `reserve-inc` is the fee-reserve share of this call's debt delta, and the denominator passed to `mul-div-down` is `(- (total-assets-preview) reserve-inc)`. The code only guards against `reserve-inc` being zero (`(if (> reserve-inc u0) ... u0)`), but there is no guard ensuring `total-assets-preview` is strictly greater than `reserve-inc`. If they become numerically equal, the denominator is `u0`, and `mul-div-down` executes `(/ (* x y) u0)`, which in Clarity is a runtime division-by-zero error that aborts the entire transaction — this is the exact bug class in the reported `_onEarnings` analog, where `sharesToMint` divides by `((_assetBalance() * BASE) - (_amount * globalFee))`.

`total-assets-preview` is `current-assets + interest` (interest being cumulative unclaimed debt above `total-borrowed`), while `reserve-inc` is a fixed fee-reserve fraction (`fee-reserve/BPS`, up to 100%) of the *incremental* debt growth (`debt-delta`) since the last accrual. In a vault state where on-chain liquid `assets` is near zero (most liquidity borrowed out) and `fee-reserve` is configured near `BPS` (100%), `reserve-inc` approaches the full incremental interest, and it becomes numerically possible for `reserve-inc` to exactly equal `total-assets-preview` at some accrual call (e.g., right after nearly all liquid assets have been withdrawn and only accrued interest remains as the asset balance, and the reserve factor captures effectively all of that increment). Because `accrue` is re-executed and re-evaluated on every subsequent call (`next-index`/`next-liquidity-index` recompute state each time from `stacks-block-time`), an attacker or even organic protocol usage pattern (sequence of borrow/repay/withdraw operations) can be timed to land on this exact equality.

### Impact Explanation
Once the division-by-zero state is reached, `accrue` reverts, and because `deposit`, `redeem`, `system-borrow`/borrow, `repay`, and `transfer` all call `(try! (accrue))` first, every subsequent user-facing operation on that vault reverts as well. This permanently freezes the unclaimed treasury yield mint (the LP shares that should have been minted to `.dao-treasury`) and, more critically, permanently freezes all depositor funds/redemptions in that vault since no further state-changing call can proceed past the failing `accrue` call — landing this squarely on permanent freezing of funds.

### Likelihood Explanation
The trigger requires a specific numeric coincidence between accumulated interest/fee-reserve parameters and the vault's current liquid asset balance. This is more constrained than the original Solidity report (which could be hit by ordinary economic activity), but is still reachable purely through normal protocol operations (deposits, borrows, repays, accrual timing) without any privileged action, DAO compromise, or oracle manipulation — it is a pure arithmetic edge case reachable by any user driving the vault into a near-fully-utilized state combined with a configured `fee-reserve` value, and once reached it is permanent (self-reinforcing, since `accrue` itself can never succeed again to change state).

### Recommendation
Add an explicit check in `accrue` (and any other call site using `mul-div-down`/`mul-div-up` with a derived, potentially-zero denominator) guarding against the denominator being zero, e.g. compute `treasury-lp` as `u0` whenever `(<= (total-assets-preview) reserve-inc)`, mirroring the fix applied in the referenced River.1.sol commit (adding a denominator check before dividing).

### Proof of Concept
1. Vault is driven (via normal `system-borrow`) into a state where `total-borrowed` consumes nearly all `assets`, leaving `current-assets` at or near zero.
2. Time passes (or blocks advance) so that `next-index` computes a `debt-delta` (accrued interest) at the next `accrue` call.
3. `fee-reserve` is set (by DAO config, a normal parameter not requiring compromise) such that `reserve-inc = mul-div-down(debt-delta, fee-reserve, BPS)` numerically equals `total-assets-preview = current-assets + interest` for that call.
4. Any user calls `deposit`, `redeem`, `repay`, or `transfer`, which internally calls `(try! (accrue))`.
5. Inside `accrue`, `(mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc))` evaluates `(- (total-assets-preview) reserve-inc)` to `u0`, causing `/` to abort the transaction.
6. Every future call to any vault entry point that invokes `accrue` now reverts identically, permanently freezing deposits/redemptions and unminted treasury yield for that vault. [3](#0-2)

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L147-148)
```text
(define-private (mul-div-down (x uint) (y uint) (z uint))
  (/ (* x y) z))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L837-865)
```text
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
```
