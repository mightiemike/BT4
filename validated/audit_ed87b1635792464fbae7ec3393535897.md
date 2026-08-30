### Title
Vault `accrue` pause pass-through lets `system-borrow`/`deposit`/`redeem` proceed on stale indexes without reverting - (File: `mainnet/contracts/vault/v0-vault-stx.clar`, and equivalent `v0-vault-*.clar` files)

### Summary
Every Zest v2 vault contract (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`, mirrored in `local-testing/contracts/vault/*.clar`) implements `accrue` so that when the `accrue` pause flag is set, the function does **not** revert — it silently passes through the old `index`/`lindex` values instead of computing fresh ones:

```clarity
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          ...)))
``` [1](#0-0) 

Every state-mutating entry point (`system-borrow`, `system-repay`, `deposit`, `redeem`, `flashloan`) unconditionally calls `(try! (accrue))` first and then proceeds using `(var-get index)` / `(var-get principal-scaled)` regardless of whether `accrue` actually updated the index or just echoed the stale one:

```clarity
(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      ...
      (idx (var-get index))
      ...)
    (try! (check-caller-auth))
    (asserts! (not (get borrow states)) ERR-PAUSED)
    ...
    (var-set principal-scaled updated-scaled-principal)
    ...))
``` [2](#0-1) 

### Finding Description
`accrue` is the single choke point that is supposed to keep the borrow/liquidity index consistent with elapsed time before any state-changing vault action runs. The pause design deliberately reuses `try!` around `accrue()` on the assumption that a paused-accrual state should still allow (or gracefully block) dependent operations. Instead of reverting the encompassing transaction (`ERR-PAUSED`) when accrual is paused, `accrue` returns `(ok ...)` with the pre-pause `index`/`lindex` unchanged [3](#0-2) . Because the caller only checks the *specific* operation's own pause bit (`borrow`, `deposit`, `redeem`, etc.) — not the `accrue` bit — every other unpaused operation (`system-borrow`, `deposit`, `redeem`) continues to execute normally, minting/burning shares and moving funds using an index that the protocol has explicitly signaled should not be trusted/updated [4](#0-3) .

This is the "pause that passes through instead of reverting" mechanism: the intended safety check (freeze interest-index mutation) is defeated because downstream functions treat the pass-through `ok` result identically to a genuine fresh accrual, so they never see that accrual was actually skipped.

### Impact Explanation
While `accrue` is paused, the treasury reserve-minting logic inside the real accrual branch (`reserve-inc`, `treasury-lp`, minting `zft` shares to `.dao-treasury`) never executes [5](#0-4) , yet `system-borrow` and `system-repay` continue to run using the un-refreshed `index`. Debt taken out or repaid during the pause window is scaled/settled against a stale index, so the interest and protocol reserve fee that should have accrued for that window is permanently skipped rather than merely delayed once the vault is unpaused (the next `accrue` call recomputes `next-index` from `last-update`, and the elapsed pause interval's interest is not retroactively captured because `last-update` is never advanced while paused). This results in **temporary/permanent freezing of unclaimed yield (protocol reserve fee to `dao-treasury` and supplier interest)** for the pause duration — matching the High-severity class of theft/freezing of unclaimed yield or royalties. It does not constitute per-user fund theft, since all users are equally affected by the missing accrual, and the mechanism is a single-transaction/single-block pass-through defect, not a race between two users.

### Likelihood Explanation
This requires the DAO/admin to pause `accrue` on a vault. If pausing `accrue` selectively (rather than pausing `borrow`/`deposit`/`redeem` as well) is a supported/expected combination, then any borrow/repay/deposit/redeem executed during that window silently loses its associated interest/fee accrual — a design flaw reachable without any privileged compromise beyond the normal (documented) pause capability. Likelihood is elevated by the fact that identical pass-through logic is duplicated verbatim across all six vault contracts, so the same missed-accrual behavior occurs system-wide once any vault's `accrue` pause bit is toggled.

### Recommendation
- **Short term:** Make `accrue`'s paused branch either (a) revert with `ERR-PAUSED` so that dependent state-mutating calls (`system-borrow`, `system-repay`, `deposit`, `redeem`, `flashloan`) cannot proceed on a stale index, or (b) if pass-through is intentional to keep read-paths available, gate all state-mutating functions on the `accrue` pause bit explicitly instead of relying on `try!` swallowing the pass-through `ok`.
- **Long term:** Track "accrual owed since `last-update`" independently of the pause flag so that when `accrue` is unpaused, any interest/fee that should have accrued during the paused interval is not permanently lost, and add regression tests asserting that pausing `accrue` alone cannot allow debt/liquidity index drift relative to `dao-treasury` fee capture.

### Proof of Concept
1. DAO (or authorized pauser) sets `pause-states` with `accrue: true` on, e.g., `v0-vault-usdc.clar`, while leaving `borrow`, `deposit`, `redeem` unpaused.
2. A user calls `market.borrow` for USDC; internally this routes to `vault-system-borrow`/`v0-vault-usdc.system-borrow`, which calls `(try! (accrue))` — this returns `(ok {index: idx, lindex: lidx})` with the pre-pause values instead of reverting [3](#0-2) .
3. `system-borrow` proceeds to compute `scaled-amount` from the stale `idx`, updates `principal-scaled`/`total-borrowed`, and transfers underlying to the borrower [6](#0-5) .
4. Time passes while `accrue` remains paused; multiple borrow/repay/deposit/redeem cycles occur, none of them contributing to `reserve-inc`/`treasury-lp` minting since the real accrual branch (which mints fee shares to `.dao-treasury`) is never entered.
5. Once `accrue` is unpaused, the next `accrue()` call computes `next-index` based on `last-update`, which was never advanced during the pause — so the interest/fee corresponding to the entire paused window is unrecoverable, permanently freezing that yield away from `dao-treasury` and liquidity suppliers.

Note: I could not fully verify (within the available context) how `pause-states` is set/authorized (e.g., whether `accrue` can be paused independently of `borrow`/`deposit`/`redeem` in practice, or whether a Devin session with full repo access would show additional guards). This uncertainty affects the exact likelihood assessment.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L833-861)
```text
;; -- Lending operations -----------------------------------------------------

(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
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
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L863-887)
```text
            (ok { index: next, lindex: nliq })))))

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (CAP-DEBT (var-get cap-debt))
      (available-assets (get-available-assets))
      (scaled-principal (var-get principal-scaled))
      (idx (var-get index))
      (debt (total-debt))
      (scaled-amount (mul-div-up amount INDEX-PRECISION idx))
      (updated-scaled-principal (+ scaled-principal scaled-amount)))

    (try! (check-caller-auth))
    (asserts! (not (get borrow states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (<= amount available-assets) ERR-INSUFFICIENT-VAULT-LIQUIDITY)
    (asserts! (<= (+ debt amount) CAP-DEBT) ERR-DEBT-CAP-EXCEEDED)

    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed (+ (var-get total-borrowed) amount))
    (try! (send-underlying amount receiver))

    (print {
```
