### Title
`accrue` pause silently returns stale indexes instead of reverting, letting borrow/deposit/redeem proceed against outdated accounting state - (File: mainnet/contracts/vault/v0-vault-stx.clar and sibling vault contracts)

### Summary
Every vault contract (`v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) implements `accrue` with a pause branch that returns `(ok { index: idx, lindex: lidx })` — the *unchanged* cached index values — when the `accrue` pause flag is set, instead of reverting the call. All state-mutating vault entry points (`system-borrow`, `deposit`, `redeem`) unconditionally call `(try! (accrue))` and treat a successful `ok` as "indexes are current," then proceed to mutate `principal-scaled`, `total-borrowed`, `assets`, and mint zTokens using those (possibly stale) indexes. This mirrors the reported analog class of "a pause that passes through instead of reverting": the pause is supposed to gate an operation, but instead of blocking downstream logic it hands back a value that downstream logic consumes as if nothing were wrong.

### Finding Description
`accrue` in the vault contracts is structured as: [1](#0-0) 

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
```

When `(get accrue states)` is `true`, the function does not compute `next-index`/`next-liquidity-index`, does not mint treasury reserve shares, and does not update `last-update` — but it still returns `(ok ...)` rather than an `err`. This is a design choice that treats "accrual paused" as a no-op success rather than a hard stop.

The problem is that every caller of `accrue` in the same file relies on this call succeeding as a precondition-satisfying step, then continues to mutate protocol state using the (frozen) `idx`/`lidx` and other live variables read afterward, e.g. `system-borrow`: [2](#0-1) 

`redeem` similarly calls `accrue` via `try!` and then immediately burns shares and sends underlying using `current-assets`/`inkind` computed from the (potentially stale) conversion functions: [3](#0-2) 

Because `try!` only short-circuits on `err`, and the paused branch returns `ok`, none of these downstream operations are blocked. The pause flag for `accrue` is therefore purely cosmetic with respect to borrow/deposit/redeem: an admin (or any caller) can be under the impression that pausing "accrue" halts interest-index updates protocol-wide, while in reality borrow/deposit/redeem keep executing normally against the last-known index, silently deferring (not skipping — since `last-update` is also frozen, a later un-paused `accrue` call will apply interest for the entire elapsed window) the interest computation.

This is the same mechanical bug class as the reported issue: a guard/pause is expected to prevent an operation from completing when a precondition is not met, but the code returns success and passes through, letting the caller's logic execute as if the precondition held.

### Impact Explanation
Because `last-update` is not advanced while `accrue` is paused, a subsequent successful `accrue` call recomputes `next-index`/`next-liquidity-index` for the *entire* elapsed interval (pause window included) in one jump. Standalone, this does not cause permanent loss of interest — it is deferred. However, this pass-through behavior means the pause does not actually stop new debt/deposit/redeem positions from being opened against inconsistent internal state (frozen `index`/`lindex` while wall-clock time and, e.g., an on-chain oracle-derived `stSTX`/`sBTC` ratio used elsewhere in the market, may have moved). Positions opened or closed during the pause window are recorded using indexes that do not reflect the true economic state at execution time, and this reconciliation gap is realized only when accrual resumes — at which point the compounding jump in `index`/`lindex` is applied uniformly to `principal-scaled`/`total-borrowed`, potentially disadvantaging whichever side (borrowers vs. suppliers) transacted during the paused window relative to what should have accrued moment-by-moment. This falls under temporary freezing/misallocation of unclaimed yield distribution among lenders/borrowers during the pause window, which lands in the "temporary freezing of funds" / "theft of unclaimed yield" impact classes.

I was not able to fully verify, within the available exploration, whether any *other* contract state read during the pause (e.g., `total-assets-preview`, `convert-to-shares-preview`) is computed off a real-time (non-frozen) index versus the frozen `var-get index`, which would determine whether an attacker could extract value directly (e.g., mint underpriced shares) during the pause versus merely experiencing deferred/redistributed interest. This distinction materially changes severity and could not be confirmed with the tool budget available.

### Likelihood Explanation
Triggering the pass-through requires only that the DAO/owner (or whoever controls `pause-states`) sets the `accrue` pause flag to `true` — a normal operational action (e.g., during an incident response). Once set, *any* user can call `deposit`, `redeem`, or `system-borrow`/`borrow` (via market) during the pause window without restriction, since these functions do not independently check the `accrue` pause flag — they only rely on `accrue`'s return value, which is always `ok`. No special permissions or unusual conditions are needed beyond the pause being active, making the interleaving straightforward to trigger in a single transaction sequence once the pause is toggled.

### Recommendation
Change the paused branch of `accrue` to return an error (e.g. `ERR-PAUSED`) instead of `(ok { index: idx, lindex: lidx })`, so that all `(try! (accrue))` call sites in `deposit`, `redeem`, and `system-borrow` correctly halt when accrual is paused, consistent with how other pause flags (`redeem`, `borrow`, `collateral-remove`) are enforced via explicit `asserts! (not (get X states)) ERR-PAUSED` checks elsewhere in the same contracts.

### Proof of Concept
Conceptual reproduction based on the in-repo pattern (exact Clarinet/Vitest harness not verified due to tool budget):
1. DAO calls the vault's pause-setter to set `pause-states.accrue = true` on `v0-vault-stx.clar` (or any sibling vault).
2. A user calls `system-borrow` (via market) or `deposit`/`redeem` directly on the vault.
3. Inside the function, `(u (try! (accrue)))` is evaluated: since `accrue` is paused, `accrue` returns `(ok { index: idx, lindex: lidx })` using the pre-pause `idx`/`lidx`, and `try!` does not short-circuit.
4. The function proceeds to mutate `principal-scaled`/`assets`/`total-borrowed` and transfer funds, completing successfully — demonstrating that setting the `accrue` pause does not block borrow/deposit/redeem, contrary to what a "pause" is expected to do.
5. When the DAO later un-pauses `accrue`, the next `accrue` call jumps `index`/`lindex` forward by the entire elapsed pause duration in one step, retroactively applying interest to all positions opened/closed during the window uniformly, regardless of when in that window they actually occurred.

### Citations

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L797-817)
```text
(define-public (redeem (amount uint) (min-out uint) (recipient principal))
  (let (
    (states (var-get pause-states))
    (u (try! (accrue)))
    (account contract-caller)
    (current-assets (var-get assets))
    (balance (get-balance-internal account))
    (balance-check (asserts! (>= balance amount) ERR-INSUFFICIENT-BALANCE))
    (available-assets (get-available-assets))
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)

  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
  (var-set assets (- current-assets inkind))
```

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L835-867)
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

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
```

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L869-900)
```text
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
```
