### Title
Paused `accrue` silently passes through stale interest indexes instead of reverting, letting borrow/repay/redeem continue against unaccrued state - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
Every vault contract (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, and their `local-testing` counterparts) implements `accrue` with a pause flag that, when set, **passes through the old index instead of reverting**, exactly matching the analog class called out in the rules ("a pause that passes through instead of reverting"). Because `deposit`, `redeem`, `borrow`, `repay`, and `accrue` each have **independent** pause bits in the same `pause-states` tuple, an operator can pause `accrue` alone while leaving `borrow`/`repay`/`redeem` active. Every one of those operations calls `accrue` first and unconditionally trusts its returned `index`/`lindex` to compute debt/share math, so while `accrue` is paused, principal-changing operations continue to mutate `principal-scaled`/`total-borrowed`/`assets` using a frozen index and a frozen `last-update` timestamp. When `accrue` is unpaused, the next call jumps the index directly from the old (pre-pause) timestamp to the current time using the interest computed over that whole window, but that computation is based on `scaled-principal` that already changed through the paused-accrual borrows/repays that happened in between. This mismatch causes `debt-delta`, `reserve-inc`, and the treasury `treasury-lp` mint to be computed against a fabricated interest window, permanently mis-stating (understating or overstating) the reserve fee actually owed and the yield actually earned by lenders during the paused period.

### Finding Description
In `mainnet/contracts/vault/v0-vault-stx.clar` (and identically in the other vault contracts): [1](#0-0) 

```
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let (...)
            ...
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

When `(get accrue states)` is `true`, the function returns the **stale** `index`/`lindex` and — critically — does **not** update `last-update`, `index`, `lindex`, or mint the treasury fee. It does not `asserts!`/revert; it just passes through as if accrual had "succeeded" with no change.

`system-borrow` calls `accrue` first and then immediately uses the (possibly stale) `idx` and mutates `principal-scaled`: [2](#0-1) 

```
(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      ...
      (idx (var-get index))
      ...
      (scaled-amount (mul-div-up amount INDEX-PRECISION idx))
      (updated-scaled-principal (+ scaled-principal scaled-amount)))
    (try! (check-caller-auth))
    (asserts! (not (get borrow states)) ERR-PAUSED)
    ...
    (var-set principal-scaled updated-scaled-principal)
    ...
```

Note that the guard checked is `(get borrow states)`, a **different** bit from `(get accrue states)` that was already consumed inside `accrue`. So pausing `accrue` alone does not stop `system-borrow`/`system-repay`/`redeem` from executing; it only stops the index/`last-update` from advancing. `redeem` shows the same pattern of calling `accrue`-derived state and mutating `assets`/`total-supply` independent of the `accrue` pause bit (see the redeem block preceding `accrue` in the same file, lines 811-833).

The vulnerable interleaving:
1. Debt/lender index at time T0: `index = I0`, `last-update = T0`.
2. Admin pauses `accrue` only (`pause-states.accrue = true`); `borrow`/`repay` remain unpaused — a normal partial-pause admin action, not a compromise.
3. Users call `borrow`/`repay` multiple times between T0 and T1. Each call invokes `accrue`, which passes through `I0` unchanged (no revert), so `principal-scaled` is updated using stale `I0` while real elapsed time and utilization keep changing.
4. Admin unpauses `accrue` at T1 and any subsequent call triggers real accrual: `next-index()` computes interest for the entire `T1 - T0` window in one shot, applied against whatever `scaled-principal`/utilization exists **at T1** (already mutated by the paused-window borrows/repays), not the value that existed at each intermediate point.
5. `debt-delta`, hence `reserve-inc` and the treasury `treasury-lp` mint, are computed from `old-debt` (using `I0` and the T1 `scaled-principal`) vs `new-debt` (using the new index and the same T1 `scaled-principal`) — this silently absorbs or duplicates interest that should have compounded incrementally against the changing principal during the pause window, permanently skewing how much yield/fee is actually credited to the treasury and lenders for that period.

### Impact Explanation
This lands on **temporary/permanent freezing of unclaimed yield**: lenders' index stops advancing for the duration `accrue` is paused even though borrowing/repaying activity (and therefore real economic interest) continues, and the reserve-fee mint computed on resume does not correctly reconstruct the interest that should have accrued against the intermediate, changing principal. Since the treasury fee mint (`treasury-lp`) and the index jump are one-shot approximations at resume time rather than a revert-and-retry, the discrepancy is baked into `index`/`lindex` permanently — it cannot be corrected after the fact, since `mainnet/contracts/vault/*` derives all future interest and share value entirely from these persisted indexes.

### Likelihood Explanation
This requires only a normal admin operation (pausing `accrue` alone while other operations stay active) — no key compromise or DAO takeover is required, since the pause flags are designed to be toggled independently per the `pause-states` tuple. Any legitimate maintenance pause of the interest-accrual subsystem while leaving borrow/repay open (e.g., to investigate an oracle or IR-model issue without blocking user liquidity) triggers this path. The bug is deterministic and reachable in every vault contract that shares this identical `accrue`/`system-borrow` pattern.

### Recommendation
Make `accrue` revert (rather than pass through) when paused if it is invoked as a prerequisite by state-mutating functions (`system-borrow`, `system-repay`, `redeem`, `deposit`), or alternatively couple the `accrue` pause bit to also gate `borrow`/`repay`/`redeem`/`deposit` so that principal cannot change while the index is frozen. At minimum, `last-update` should still be recorded even when passing through the accrual math (or the interest window should be sliced/replayed) so that resuming accrual does not lump inconsistent interest onto principal that changed during the paused period.

### Proof of Concept
1. Deployer calls the vault's pause-setter to set `pause-states = { deposit: false, redeem: false, borrow: false, repay: false, accrue: true, flashloan: false }`.
2. User A calls `borrow`/`market.clar` `borrow`, which routes to `system-borrow`; `accrue` passes through unchanged (`index` stays `I0`, `last-update` stays `T0`), yet `principal-scaled`/`total-borrowed` are updated as if `I0` were current.
3. Time passes (`T1 > T0`); more borrows/repays happen, each keeping `index` pinned at `I0` while `principal-scaled` keeps changing.
4. Deployer unpauses `accrue`. The next call to any accrual-dependent function computes `next-index()`/`next-liquidity-index()` using `stacks-block-time - last-update (T0)` in one jump, and `debt-delta`/`reserve-inc`/`treasury-lp` are derived from `scaled-principal` as it stands at `T1`, not as it evolved during `[T0, T1]`. The resulting index and treasury mint permanently misstate the interest actually owed and yield actually accrued during the pause window. [3](#0-2)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L98-115)
```text
;; -- Pause states
(define-data-var pause-states
  {
    deposit: bool,
    redeem: bool,
    borrow: bool,
    repay: bool,
    accrue: bool,
    flashloan: bool
  }
  {
    deposit: false,
    redeem: false,
    borrow: false,
    repay: false,
    accrue: false,
    flashloan: false
  })
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-863)
```text
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
                false)
            (ok { index: next, lindex: nliq })))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L865-887)
```text
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
