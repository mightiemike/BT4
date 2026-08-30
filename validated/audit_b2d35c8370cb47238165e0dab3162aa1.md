### Title
`socialize-debt` mutates debt/utilization state without accruing pending interest first, unlike every other lending entrypoint - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and identical logic in `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`)

### Summary
Every state-mutating lending entrypoint in the vault contracts (`deposit`, `redeem`, `system-borrow`, `system-repay`, `flashloan`) begins by calling `(try! (accrue))` to roll the interest `index`/`lindex` forward to the current block *before* changing `principal-scaled`, `total-borrowed`, or `assets`. `socialize-debt` is the one exception: it reads the stale `index`/`lindex`/`total-borrowed`/`assets` directly and mutates them without ever calling `accrue()` first. [1](#0-0) 

### Finding Description
`accrue()` is the function responsible for bringing `index` (debt growth) and `lindex` (liquidity/share growth) up to date with elapsed time and current utilization, and for minting the protocol's reserve share (`treasury-lp`) based on the interest accrued since `last-update`. [2](#0-1) 

`system-borrow` and `system-repay`, which change `total-borrowed`/`principal-scaled` (the values that drive `utilization()` and thus the interest rate), both call `accrue()` as the very first bound value in their `let`, exactly mirroring the pattern the external report recommends (accrue floating debt before any variable that affects utilization is updated): [3](#0-2) [4](#0-3) 

`socialize-debt`, however, binds `idx (var-get index)` — the raw, potentially stale index — and `old-total-assets (total-assets)`, where `total-assets` itself calls `total-debt`, which uses `(var-get index)` rather than `(next-index)`: [5](#0-4) [6](#0-5) 

It then directly writes `lindex`, `principal-scaled`, `total-borrowed`, and `assets` using these un-accrued figures — no call to `accrue()` appears anywhere in the function. This is the exact bug class described in the report: a variable that co-determines the debt/utilization state (`total-borrowed`, `principal-scaled`, `assets`, `lindex`) is updated while skipping the accrual step that should have crystallized pending interest first. The bound value (`idx`/`total-assets` computed from the stale `index`) is never invalidated by an accrual call before the later mutation (`var-set lindex new-lindex`, `var-set total-borrowed …`, `var-set assets …`) is applied — all within the same transaction/block.

### Impact Explanation
Because `socialize-debt` writes down LP share value (`lindex`) and the vault's bookkeeping (`total-borrowed`, `assets`) using pre-accrual figures, any interest that accrued between the last `accrue()`-triggering call and the block in which `socialize-debt` executes is silently dropped from the debt/asset base instead of being crystallized into `assets` and the reserve mint (`treasury-lp`) first. This causes the vault's LP share price (via `lindex`) and its outstanding-debt bookkeeping to diverge from the economically correct, fully-accrued state at the moment bad debt is socialized — the same "unrealized debt manipulated by updating an unaccrued utilization-affecting variable" mechanism as the referenced Exactly report. The result is a temporary freezing/misallocation of protocol-reserve yield (the treasury mint tied to `fee-reserve` that should have occurred in `accrue()` is skipped for that period) and incorrect write-down amounts applied to LPs, i.e., temporary freezing/loss of unclaimed yield for LPs and the DAO treasury.

### Likelihood Explanation
`socialize-debt` is gated by `check-caller-auth`, i.e., only callable by an authorized contract (the market contract), not directly by end users. [7](#0-6) 
I was not able to fully trace, within the available tool budget, the exact conditions in `mainnet/contracts/market/v0-4-market.clar` under which the market invokes `socialize-debt` (e.g., during liquidation of an under-collateralized/bad-debt position) or whether a user can control the timing/size of that call (for instance by choosing when to trigger a liquidation that results in socialization, in a block where accrual has been dormant). This limits confidence in exact exploitability/likelihood; it should be verified against the market contract's liquidation/bad-debt flow before treating this as confirmed-exploitable, though the missing-accrual root cause itself is unambiguous from the vault code alone.

### Recommendation
Add `(try! (accrue))` as the first bound expression in `socialize-debt`, exactly as done in `system-borrow`/`system-repay`, and recompute `idx`/`lindex`/`total-assets` from the post-accrual state before calculating `debt-reduction`, `principal-reduction`, and `new-lindex`.

### Proof of Concept
Code-level proof (root cause), since dynamic PoC execution is outside the scope of this analysis:
1. Time passes with no `deposit`/`redeem`/`system-borrow`/`system-repay` call on a given vault, so `last-update` lags behind `stacks-block-time` and pending interest is unaccrued. [8](#0-7) 
2. The market contract calls `socialize-debt` on that vault (e.g., as part of writing down bad debt from a liquidation).
3. `socialize-debt` computes `idx = (var-get index)` (stale), `old-total-assets = (total-assets)` (also stale, since `total-debt` uses `(var-get index)`), then derives `debt-reduction`, `principal-reduction`, and `new-lindex` from these stale values, and finally mutates `lindex`, `principal-scaled`, `total-borrowed`, and `assets` — all without ever calling `accrue()`. [1](#0-0) 
4. The interest that should have accrued between `last-update` and the current block (and the corresponding treasury reserve mint that `accrue()` would perform) is never applied, so the LP share price (`lindex`) write-down and the vault's `total-borrowed`/`assets` bookkeeping end up inconsistent with the true, fully-accrued debt state at the time of socialization.

### Citations

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L379-404)
```text
(define-private (next-index)
  (let ((states (var-get pause-states))
        (idx (var-get index)))
    (if (get accrue states)
        idx
        (let (
            (rate (interest-rate))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))

(define-private (next-liquidity-index)
  (let ((states (var-get pause-states))
        (lidx (var-get lindex)))
    (if (get accrue states)
        lidx
        (let (
            (rate (interest-rate))
            (liquidity-rate (calc-liquidity-rate rate (utilization) (var-get fee-reserve)))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta liquidity-rate time-delta false))))
          (calc-index-next lidx multiplier)))))
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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L865-900)
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
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L902-942)
```text
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
        principal-repaid: principal-repaid,
        interest-paid: interest-paid,
        principal-scaled: updated-scaled-principal,
        total-borrowed: total-borrowed-new,
        assets: (var-get assets),
        index: idx
      }
    })

    (ok true)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L944-984)
```text
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        (borrowed (var-get total-borrowed))
        (idx (var-get index))
        (current-assets (var-get assets))
        (current-lindex (var-get lindex))
        (old-total-assets (total-assets))
        (debt-reduction (mul-div-down scaled-amount idx INDEX-PRECISION))
        (principal-reduction (if (> scaled-principal u0)
                                (mul-div-down scaled-amount borrowed scaled-principal)
                                u0))
        ;; Write down lindex proportionally to loss in total-assets
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
                       (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
                       u0)))

    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

    (print {
      action: "socialize-debt",
      caller: contract-caller,
      data: {
        scaled-amount: scaled-amount,
        debt-reduction: debt-reduction,
        principal-reduction: principal-reduction,
        old-lindex: current-lindex,
        new-lindex: new-lindex,
        old-total-assets: old-total-assets,
        principal-scaled: (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0),
        total-borrowed: (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0),
        index: idx
      }
    })

    (ok true)))
```
