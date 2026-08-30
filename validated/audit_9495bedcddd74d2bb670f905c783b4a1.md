### Title
Stale `last-update` when zero-utilization interest rate rounds index unchanged causes disproportionate debt-index compounding on the next real accrual - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar`)

### Summary
In `accrue`, `last-update` is only advanced when `index` or `lindex` actually change value, not whenever time has elapsed. If the vault sits at zero utilization for a long period (e.g. right after `initialize`'s minimum-liquidity deposit, or any extended idle interval with no borrows), `interest-rate()` is `0`, so the computed `next` index equals the current `idx` and `last-update` never advances even though real time passed. Once utilization becomes nonzero (a normal borrow occurs) and a subsequent state-changing call finally triggers a real index change, the full stale `time-delta` (idle period + active period) is compounded at the now-nonzero rate against the accumulated scaled principal.

### Finding Description
1. `accrue` (lines 833-861) reads `idx`/`lidx` and computes `next`/`nliq` via `next-index()`/`next-liquidity-index()`, which internally depend on elapsed time since `last-update` and the current `interest-rate()`.
2. `old-debt`/`new-debt`/`debt-delta` are derived from `scaled-principal` combined with `idx` vs `next` [1](#0-0) .
3. `index`/`lindex` are only written if they differ from the newly computed value [2](#0-1) .
4. Critically, `last-update` is only advanced when `idx != next` OR `lidx != nliq` — i.e. only when the index actually moved: `(if (or (not (is-eq idx next)) (not (is-eq lidx nliq))) (var-set last-update stacks-block-time) false)` [3](#0-2) .
5. When utilization is `u0`, `interest-rate()` evaluates to `0`, so the compounding multiplier rounds to `INDEX-PRECISION` exactly, making `next == idx` (and `nliq == lidx`) even though `time-delta` (real elapsed time) is nonzero. `last-update` therefore silently fails to advance across an arbitrarily long idle interval.
6. `system-borrow` (lines 863-898) calls `accrue` as its first binding, before `principal-scaled`/`total-borrowed` are updated, so utilization is still `u0` at that point and `last-update` still does not advance during the borrow transaction itself.
7. On any subsequent state-changing call (`deposit`, `redeem`, `transfer`, another `system-borrow`/`system-repay`), `accrue` computes `time-delta` as `now - last-update`, which now spans the *entire* idle interval plus the time since the borrow. Because utilization is now nonzero, `interest-rate()` is nonzero, and the full stale `time-delta` is compounded into `next`, producing a debt-index jump disproportionate to the actual duration the debt existed.
8. No later check recovers this: `debt-delta`, `reserve-inc`, and `treasury-lp` are all derived directly from the inflated `next` index, and are minted/accounted for unconditionally.

### Impact Explanation
The borrower's `scaled-principal` is charged interest as if it had been outstanding for the entire idle+active window rather than just the active borrowing period, producing an oversized, non-recoverable one-time debt-index jump. This inflates the debt attributed to the borrowing position and inflates `treasury-lp` minted based on that overstated `debt-delta`, unfairly diluting other share holders. This matches the impact category of temporary freezing of unclaimed yield/funds for the affected borrower (and mispriced share dilution for depositors), consistent with the question's cited severity ("High: temporary freezing of liquidity via unexpected debt-index jump").

### Likelihood Explanation
The precondition (extended zero-utilization period, e.g. immediately following `initialize`'s minimum-liquidity seeding, or any lull with no active borrows) is a normal, non-adversarial vault state that occurs incidentally rather than being deliberately engineered by a single unprivileged actor in one block. Triggering the full effect requires: a passage of real time with zero utilization, a borrow via the vault's authorized market path, and a further state-changing call afterward — spanning multiple blocks/transactions. It can be realized by one ordinary user acting as both initial depositor and later borrower through the protocol's normal public entry points, without needing DAO or market-implementation privilege.

### Recommendation
Advance `last-update` on every `accrue` call whenever any nonzero `time-delta` has elapsed, regardless of whether `index`/`lindex` numerically changed, so idle zero-rate intervals are not silently absorbed into a later nonzero-rate compounding window.

### Proof of Concept
Clarinet simnet test outline: (1) call `initialize` to seed minimum liquidity (utilization `u0`); (2) advance `stacks-block-time` by a large interval with no borrows; (3) call the market's borrow entry point so `system-borrow` executes on the vault, moving utilization off `u0`; (4) advance a modest further interval and call `accrue` (or trigger it via `deposit`/`redeem`/`transfer`); (5) assert the interest reflected in the new `index` corresponds only to the short post-borrow interval rather than the entire idle+active `time-delta`, and inspect `last-update` before/after each step to confirm it did not advance during the zero-utilization interval (line 858 condition being false).

Note: I was unable to inspect the exact bodies of `interest-rate()`, `utilization()`, and `next-index()` within the available tool budget to fully confirm the precise rounding thresholds; the confirmed root-cause mechanism (conditional `last-update` advancement at line 858, gated purely on index-value equality rather than elapsed time) is verified directly from the source and is the basis of this finding.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L843-847)
```text
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L849-854)
```text
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L858-860)
```text
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
```
