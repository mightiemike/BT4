### Title
Vault `accrue` pause pass-through allows interest-accrual freeze while `deposit`/`redeem`/`borrow`/`repay` remain active, letting a depositor front-run the accrual resume - (File: `local-testing/contracts/vault/vault-usdc.clar` / `mainnet/contracts/vault/v0-vault-usdc.clar`, function `accrue`)

### Summary
Each of the six spoke vaults (`vault-stx.clar`, `vault-sbtc.clar`, `vault-ststx.clar`, `vault-usdc.clar`, `vault-usdh.clar`, `vault-ststxbtc.clar`, and their mainnet `v0-vault-*.clar` twins) implements `accrue` with an explicit pass-through when the `accrue` flag of `pause-states` is set: it returns the currently stored `index`/`lindex` unchanged instead of reverting, and skips updating `index`, `lindex`, and `last-update` [1](#0-0) . Because `pause-states` stores independent booleans per operation (`accrue`, `borrow`, `repay`, `deposit`, `redeem`), an admin/governance action can pause only `accrue` while leaving `deposit`, `redeem`, `borrow`, and `repay` active [2](#0-1) . All state-mutating vault entry points (`system-borrow`, `system-repay`, `deposit`, `redeem`) unconditionally call `(try! (accrue))` and then use its return value (or the vault's cached `index`) as if it reflects the current, economically-correct exchange rate [3](#0-2) .

### Finding Description
`accrue` normally recomputes `next-index`/`next-liquidity-index` from elapsed time (via `stacks-block-time`) and mints treasury fee shares for the interest delta, then commits the new index/lindex/`last-update` [4](#0-3) . When `(get accrue states)` is true, none of this happens — the function just returns the stale `{index: idx, lindex: lidx}` pair and `ok`s [1](#0-0) .

Crucially, this pass-through is silent to the callers: `deposit`, `redeem`, `system-borrow`, and `system-repay` all begin with `(u (try! (accrue)))` and then immediately read `(var-get index)` / call `convert-to-assets-preview` / `total-debt` using the (unchanged) stored state, with no branch distinguishing "accrual actually ran" from "accrual was paused and skipped" [2](#0-1) [5](#0-4) . In other words, the guard (`accrue`) that is supposed to keep the share price/borrow index current is bypassed, but the mutation (deposit mint, redeem burn+payout, borrow debt-add, repay debt-remove) proceeds anyway using the pre-pause value as though it were fresh — this is the "pause that passes through instead of reverting" pattern feeding directly into value-moving operations rather than blocking them.

Because economic interest continues to be owed by borrowers in real terms (the underlying debt/interest model is purely a function of elapsed time once accrual resumes), freezing only the `accrue` flag creates a window where the vault's `index` (and hence the zToken share price derived from `total-assets-preview`) is understated relative to the fair value it will jump to the moment `accrue` is unpaused and a state-changing call runs the real `next-index()`/`next-liquidity-index()` calculation.

Exploit sequence:
1. Governance/admin sets `pause-states.accrue = true` on a vault (e.g. `vault-usdc`) for maintenance, while leaving `deposit`/`redeem` unpaused.
2. Attacker calls `deposit` during the pause window. `accrue` pass-through returns the stale (pre-freeze) `index`; `convert-to-shares`/mint logic prices the attacker's deposit using this stale, lower exchange rate rather than the true rate that should already reflect additional accrued interest owed by existing borrowers.
3. Governance unpauses `accrue`. The next state-changing call (anyone's `borrow`/`repay`/`deposit`/`redeem`) runs the real `next-index()` calculation, which — because `last-update` was never advanced during the pause — computes interest for the entire elapsed pause duration in one shot, jumping `index`/`lindex` upward.
4. Attacker immediately calls `redeem`, capturing the newly-recognized interest that should have accrued proportionally to pre-existing depositors, diluting them.

### Impact Explanation
This allows an attacker to time deposits/redemptions around an `accrue` pause/unpause boundary to capture interest that should belong to existing depositors, i.e., theft of unclaimed yield from other vault-share holders — falls under the in-scope "High: theft of unclaimed yield" impact class. It can also be used in the opposite direction (redeeming before resume, depositing after) to extract more underlying per share than is fair, effectively a temporary freezing/misallocation of protocol funds across all six vault instances (`vault-stx`, `vault-sbtc`, `vault-ststx`, `vault-usdc`, `vault-usdh`, `vault-ststxbtc`) since they all share this identical `accrue` implementation.

### Likelihood Explanation
Likelihood depends on: (a) the DAO/governance actually pausing only `accrue` while leaving deposit/redeem active — a plausible maintenance/incident scenario rather than a full vault pause, and (b) an attacker monitoring the pause/unpause transactions (all `pause-states` are public/read-only-visible on-chain) and front-running the unpause tx or timing deposit/redeem around it. Since pausing is a privileged, intentional operation, the root cause is the missing invalidation/consistency check between the paused accrual and the dependent deposit/redeem/borrow/repay math, not the privileged action itself.

### Recommendation
When `accrue` is paused, either:
- Also pause `deposit` and `redeem` (and ideally `borrow`/`repay`) for that vault so no share-price-sensitive operation can execute against a stale index, or
- Have `deposit`/`redeem`/`system-borrow`/`system-repay` explicitly check whether `accrue` actually ran (e.g., compare `last-update` to `stacks-block-time`) and revert if it is stale beyond a tolerance, instead of silently trusting the pass-through result.

### Proof of Concept
Not independently executed; derived from static analysis of the shared `accrue`/`deposit`/`redeem`/`system-borrow`/`system-repay` code across `local-testing/contracts/vault/vault-usdc.clar` (and its mainnet counterpart `mainnet/contracts/vault/v0-vault-usdc.clar`), plus confirmation that `pause-states` is a per-operation, independently-settable structure. Concrete numeric conditions for `next-index()`'s jump magnitude versus `total-assets-preview`'s share-price sensitivity were not directly inspected in this pass — a Devin session with Clarinet/Clarigen test execution would be needed to run the sequence above (pause accrue → deposit → unpause → redeem) and measure the actual yield captured, since the exact share-price formula (`convert-to-assets-preview`) was not fully retrieved in this investigation.

### Citations

**File:** local-testing/contracts/vault/vault-usdc.clar (L838-843)
```text
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
```

**File:** local-testing/contracts/vault/vault-usdc.clar (L867-886)
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
```

**File:** local-testing/contracts/vault/vault-stx.clar (L797-817)
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

**File:** local-testing/contracts/vault/vault-stx.clar (L843-863)
```text
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
