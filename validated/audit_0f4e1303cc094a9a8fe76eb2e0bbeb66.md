### Title
`accrue()` pause pass-through skips treasury fee-share minting while deposit/redeem still use live (unpaused) exchange-rate previews - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
Each Zest v0 vault (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) implements `accrue()` so that when the `accrue` pause flag is set, it does not revert — it silently passes through, returning the *current* `index`/`lindex` without updating them and, critically, without minting the protocol's `treasury-lp` fee shares to `.dao-treasury`. However, the share-price preview functions used by `deposit`/`redeem` (`total-assets-preview`, `convert-to-shares-preview`, `convert-to-assets-preview`) are pure functions of elapsed time and rate — they are not gated by the `accrue` pause flag and still compute the "as-if-accrued" interest. This mismatch lets a redeemer cash out at the live, interest-inflated exchange rate in the same transaction that the protocol's fee-share mint is bypassed by the pause.

### Finding Description
`accrue()`'s branch logic is: [1](#0-0) 

When `(get accrue states)` is `true`, the function returns `(ok { index: idx, lindex: lidx })` immediately — a pass-through instead of an `ERR-PAUSED` revert. In the non-paused branch, note that the treasury fee (`treasury-lp`) is only minted here, inside `accrue()`: [2](#0-1) 

Both `deposit` and `redeem` call `(try! (accrue))` first, then independently compute `inkind` via `convert-to-shares-preview`/`convert-to-assets-preview`, which in turn call `total-assets-preview`: [3](#0-2) [4](#0-3) 

`total-assets-preview` derives its result from `debt-preview`, which calls `next-index()` — a pure function of `principal-scaled`, `index`, `last-update`, and the current interest rate, independent of the `accrue` pause flag: [5](#0-4) 

So when `pause-states.accrue = true`:
1. `redeem` calls `accrue()`, which pauses-through: `index`/`lindex` stay stale and no `treasury-lp` shares are minted to `.dao-treasury` for the reserve fee that would normally accompany this accrual.
2. `redeem` then computes `inkind = convert-to-assets-preview(amount)`, which uses `total-assets-preview` → `debt-preview` → `next-index()`, all of which still fully reflect the interest that has accrued since `last-update` (since these preview helpers never check `pause-states`).
3. The redeemer burns shares and receives underlying assets priced at the *live* (unpaused) exchange rate, which already embeds the freshly accrued interest.
4. Because the real `accrue()` never ran its accrual branch, the treasury's proportional fee-share mint (`reserve-inc` → `treasury-lp` shares to `.dao-treasury`) for that same accrued interest never happens.

This is a single-transaction/single-call inconsistency between the pause's intended effect (freeze state changes) and its actual effect (state frozen, but pricing that consumes the frozen state's would-be update is not frozen), exactly matching the "a pause that passes through instead of reverting" analog class.

### Impact Explanation
Every time a user calls `redeem` (or `deposit`, though deposit's effect is smaller/opposite direction) while `accrue` is paused, the protocol's reserve fee normally captured as treasury-lp shares on that increment of interest is permanently lost — the interest is paid out to the withdrawing user in full, and no compensating mint occurs later because `last-update` was not advanced, but only the *next* real accrual (post-unpause) will mint fee shares proportional to the *remaining, not-yet-paid-out* interest delta computed at that later point, not for the interest fraction already extracted by the redeemer during the paused window. This is a permanent loss of protocol/treasury fee revenue (unclaimed yield), which falls under High impact: "theft of unclaimed yield or royalties, permanent freezing of unclaimed yield or royalties."

### Likelihood Explanation
This requires the DAO/admin to have set `pause-states.accrue = true` on the vault (a supported, documented pause lever available via `check-dao-auth`-gated setters), and then any regular user to call `redeem` in that state — no special privilege or multi-party collusion is needed once the pause is active, and it is reachable in a single transaction. It is present identically in all six vault contracts (`v0-vault-stx`, `v0-vault-sbtc`, `v0-vault-ststx`, `v0-vault-usdc`, `v0-vault-usdh`, `v0-vault-ststxbtc`) because they share the same generated logic.

### Recommendation
Either (a) make `accrue()` revert with `ERR-PAUSED` when the `accrue` pause flag is set rather than passing through silently, or (b) make the preview functions (`total-assets-preview`, `convert-to-shares-preview`, `convert-to-assets-preview`) pause-aware so they freeze at the last committed `index`/`lindex` whenever `accrue` is paused, ensuring redemption pricing and actual fee accrual bookkeeping stay consistent.

### Proof of Concept
1. DAO calls the vault's pause-setter to set `pause-states.accrue = true` (existing DAO-authorized functionality).
2. Time passes so that `next-index()` would compute a materially higher index than the stored `index` (interest has accrued).
3. A user calls `redeem(amount, min-out, recipient)`:
   - `(try! (accrue))` executes the paused pass-through branch — no `treasury-lp` mint, `index`/`lindex`/`last-update` unchanged.
   - `inkind = convert-to-assets-preview(amount)` is computed using `total-assets-preview`, which uses `debt-preview` → `next-index()`, yielding the interest-inflated redemption amount.
   - User receives `inkind` underlying assets that include the just-accrued interest in full, while the treasury never receives its proportional fee share for that same interest increment because the true accrual branch never ran.
4. Repeat for every redeemer during the pause window: the DAO's reserve-fee revenue on the interest realized during the entire paused period is permanently lost.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L308-325)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))

(define-private (convert-to-assets-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ta u0)
        u0
        (if (is-eq ts u0)
            u0
            (mul-div-down amount ta ts)))))

```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L328-339)
```text
(define-private (total-debt)
  (calc-cumulative-debt (var-get principal-scaled) (var-get index)))

(define-private (debt-preview)
  (calc-cumulative-debt (var-get principal-scaled) (next-index)))

(define-private (total-assets)
  (let ((current-assets (var-get assets))
        (debt (total-debt))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L341-346)
```text
(define-private (total-assets-preview)
  (let ((current-assets (var-get assets))
        (debt (debt-preview))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))
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
