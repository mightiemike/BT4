### Title
`accrue` pause is a pass-through, not a halt, so debt/interest mutation continues on a frozen index — (File: `local-testing/contracts/vault/vault-*.clar` / `mainnet/contracts/vault/v0-vault-*.clar`, function `accrue`)

### Summary
Each Zest vault contract (`vault-stx`, `vault-sbtc`, `vault-ststx`, `vault-usdc`, `vault-usdh`, `vault-ststxbtc`, and their mainnet `v0-vault-*` counterparts) gates interest accrual with a `pause-states.accrue` flag. When that flag is set, the private `accrue` function does **not** halt the caller and does **not** revert — it simply returns the last cached `index`/`lindex` unchanged and skips updating `last-update`. However, `system-borrow` and `system-repay` (and the analogous supply/redeem paths) each call `(try! (accrue))` first and then proceed to mutate `principal-scaled`/`total-borrowed`/`assets` using whatever index `accrue` handed back — without ever checking whether `accrue` itself was paused. The `accrue` pause is therefore silently bypassed by every state-mutating operation that depends on it, exactly the pattern in the reference report where a listing/availability flag (`sell.islisted`) is set by the intent-holder but never consulted by the function that executes the economically significant action (`setbidtobuy`).

### Finding Description
In every vault contract, `accrue` is structured as:

```
(if (get accrue states)
    ;; PAUSED: Pass-through without reverting
    (ok { index: idx, lindex: lidx })
    ;; NOT PAUSED: Normal accrual logic
    ...)
``` [1](#0-0) 

This same pattern is duplicated verbatim across `vault-ststx.clar`, `vault-ststxbtc.clar`, `vault-stx.clar`, `vault-usdc.clar`, `vault-usdh.clar`, and the mainnet `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`. [2](#0-1) 

`system-borrow` immediately calls `accrue`, binds its result to `u`, and never inspects `u` (or the `accrue` sub-flag of `pause-states`) before mutating `principal-scaled`, `total-borrowed`, and sending funds:

```
(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      ...)
    (try! (check-caller-auth))
    (asserts! (not (get borrow states)) ERR-PAUSED)
    ...
    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed (+ (var-get total-borrowed) amount))
    (try! (send-underlying amount receiver))
    ...))
``` [3](#0-2) 

`system-repay` follows the identical structure, calling `accrue` first and then mutating `principal-scaled`/`total-borrowed`/`assets` based on whatever (possibly stale, pause-frozen) `index`/`total-debt()` it received: [4](#0-3) 

Only `borrow`/`repay`-specific booleans inside `pause-states` are asserted (`(asserts! (not (get borrow states)) ERR-PAUSED)` / `(asserts! (not (get repay states)) ERR-PAUSED)`); the `accrue` boolean is read purely to decide whether `accrue` itself updates `index`/`lindex`/`last-update`, and its "paused" outcome is never propagated as an error to the caller. This is the root cause: the value bound in `system-borrow`/`system-repay` is the cached debt index (`idx`, and the reserve-fee/treasury mint amounts derived from `debt-delta`), the invalidating event is a DAO/admin call that sets `pause-states.accrue = true` (freezing further index growth and reserve-fee minting to `dao-treasury`), and the later use is the immediate, unguarded continuation of borrow/repay accounting on that frozen index, exactly as `setbidtobuy` continued to consume `sell.price`/`sell.denom`/`sell.auto_approve` after `sell.islisted` was flipped false without ever checking `islisted`.

### Impact Explanation
While `accrue` is paused, `debt-delta`, `reserve-inc`, and the resulting `treasury-lp` mint to `.dao-treasury` are never computed/executed, because that logic only runs inside the "NOT PAUSED" branch: [5](#0-4) 

Yet `system-borrow`/`system-repay` are not blocked by this flag and keep operating on the frozen `index`. This means the protocol operator's intent in pausing accrual (e.g., to freeze interest growth/fee minting during an incident, oracle issue, or upgrade) is defeated: borrowers can continue to open and close debt positions during the pause window while interest and the protocol's treasury reserve fee do not accrue for that period, and — because `last-update` is not advanced while paused — once unpaused, the next real `accrue` call compounds the entire elapsed span (pre-pause + paused + post-pause) onto whatever `principal-scaled` exists **at that later moment**, not the principal that actually existed during the frozen window. Debt opened and fully repaid entirely inside the pause window accrues no interest and generates no `treasury-lp` reserve fee at all, permanently denying that yield to zToken suppliers/`dao-treasury` — this lands on the in-scope "theft/permanent freezing of unclaimed yield or royalties" impact bucket, since the fee-reserve share of interest that should have accrued on activity occurring during the paused window is silently and unrecoverably lost.

### Likelihood Explanation
This requires only a single admin action (pausing `accrue` via the vault's pause-setter, which is a legitimate, documented operational control) followed by ordinary user calls to `system-borrow`/`system-repay` — no cross-user interference or privileged compromise is needed beyond the DAO's own intended pause action, and the bypass is triggered purely by the existing, unmodified logic. Any borrow/repay cycle fully contained inside a legitimate `accrue`-pause window (which is entirely plausible for maintenance/incident windows) triggers the missing-check.

### Recommendation
`system-borrow`, `system-repay`, and any other function that calls `accrue` and then mutates debt/supply state should assert that `(not (get accrue states))` (or equivalently check the `accrue` result for a "paused" sentinel) before proceeding, mirroring the existing `(asserts! (not (get borrow states)) ERR-PAUSED)` checks — i.e., either make `accrue` return an explicit paused-error that its callers propagate with `try!`, or add an explicit `ERR-PAUSED` assertion on the `accrue` flag in every function that depends on a freshly-accrued index.

### Proof of Concept
1. DAO/admin calls the vault's pause setter to set `pause-states.accrue = true` (leaving `borrow`/`repay` flags `false`).
2. Attacker/user calls `system-borrow` — `accrue` takes the "PAUSED: Pass-through" branch and returns the stale `{index, lindex}` without minting any `treasury-lp` reserve fee and without advancing `last-update`; `system-borrow` proceeds to update `principal-scaled`/`total-borrowed` and transfer funds normally, since only the `borrow` pause bit is checked. [6](#0-5) 
3. User calls `system-repay` shortly after, fully repaying the borrowed amount — again `accrue` no-ops (paused), so `debt-delta`/`reserve-inc`/`treasury-lp` for the elapsed time is never computed or minted, and the borrow-and-repay cycle completes with zero interest and zero reserve fee credited to `dao-treasury`, despite real elapsed time and real debt having existed. [7](#0-6) 
4. Because `last-update` was never advanced during the pause, the loss of interest/reserve-fee accrual for this borrow/repay cycle is not merely deferred — it is permanently unrecoverable once the position is closed, since the follow-up `accrue` (after unpause) computes growth only against whatever `principal-scaled` remains at that time, which no longer includes the already-repaid debt.

I was not able to directly inspect the full bodies of `next-index`/`next-liquidity-index`/`total-debt`/`get-available-assets` in the final iteration (only their call sites), so the exact numeric magnitude of lost interest per pause window is not independently verified here — this should be confirmed by a Devin session with full file access if precise loss quantification is required.

### Citations

**File:** local-testing/contracts/vault/vault-sbtc.clar (L841-865)
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
```

**File:** local-testing/contracts/vault/vault-sbtc.clar (L867-928)
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
      action: "system-borrow",
      caller: contract-caller,
      data: {
        receiver: receiver,
        amount: amount,
        scaled-amount: scaled-amount,
        principal-scaled: updated-scaled-principal,
        total-borrowed: (var-get total-borrowed),
        index: idx
      }
    })

    (ok true)))

(define-public (system-repay (amount uint))
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
        (total-borrowed-amount (var-get total-borrowed))
        (capped-amount (if (> amount debt) debt amount))
        (principal-reduction (calc-principal-ratio-reduction capped-amount scaled-principal debt))
        (capped-reduction (if (> principal-reduction scaled-principal) scaled-principal principal-reduction))
        (updated-scaled-principal (- scaled-principal capped-reduction))
        (principal-repaid (mul-div-down capped-amount total-borrowed-amount debt))
        (interest-paid (- capped-amount principal-repaid))
        (total-borrowed-new (if (> total-borrowed-amount principal-repaid) (- total-borrowed-amount principal-repaid) u0)))

    (try! (check-caller-auth))
    (asserts! (not (get repay states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (try! (receive-underlying capped-amount tx-sender))
    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed total-borrowed-new)
    (var-set assets (+ (var-get assets) interest-paid))

```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L871-930)
```text
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
      action: "system-borrow",
      caller: contract-caller,
      data: {
        receiver: receiver,
        amount: amount,
        scaled-amount: scaled-amount,
        principal-scaled: updated-scaled-principal,
        total-borrowed: (var-get total-borrowed),
        index: idx
      }
    })

    (ok true)))

(define-public (system-repay (amount uint))
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
        (total-borrowed-amount (var-get total-borrowed))
        (capped-amount (if (> amount debt) debt amount))
        (principal-reduction (calc-principal-ratio-reduction capped-amount scaled-principal debt))
        (capped-reduction (if (> principal-reduction scaled-principal) scaled-principal principal-reduction))
        (updated-scaled-principal (- scaled-principal capped-reduction))
        (principal-repaid (mul-div-down capped-amount total-borrowed-amount debt))
        (interest-paid (- capped-amount principal-repaid))
        (total-borrowed-new (if (> total-borrowed-amount principal-repaid) (- total-borrowed-amount principal-repaid) u0)))

    (try! (check-caller-auth))
    (asserts! (not (get repay states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (try! (receive-underlying capped-amount tx-sender))
    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed total-borrowed-new)
    (var-set assets (+ (var-get assets) interest-paid))

    (print {
      action: "system-repay",
      caller: contract-caller,
      data: {
        amount-requested: amount,
        amount-repaid: capped-amount,
```
