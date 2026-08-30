## Analysis

I found a viable single-transaction analog matching the "pause that passes through instead of reverting" bug class, in the vault `accrue` function used across all six vault contracts.

### Title
Independently-pausable `accrue` lets `system-borrow`/`system-repay` use a stale interest index while debt/asset accounting proceeds normally - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
Each vault (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) stores a granular `pause-states` tuple with an independent `accrue` flag, separate from the `borrow`/`repay` flags [1](#0-0) . `accrue` checks only its own pause bit; when paused it "passes through" and returns the current `index`/`lindex` state variables **without recomputing or persisting anything**, instead of reverting [2](#0-1) .

`system-borrow` and `system-repay` call `(try! (accrue))` but then bind `idx` via a fresh `(var-get index)` right after, and use it (and `total-debt`, which itself is a function of `index`) to compute scaled-debt deltas [3](#0-2) , [4](#0-3) . Only the `borrow`/`repay` pause bits gate execution — the `accrue` bit gates only whether interest is rolled forward, and is checked independently.

### Finding Description
1. The admin/DAO sets `pause-states.accrue = true` on a vault (e.g. for planned maintenance) while leaving `borrow`/`repay` bits `false` — a legitimate, narrower-than-intended use of the granular pause bitmap.
2. Time passes; the vault's true `next-index`/`next-liquidity-index` (computed from `points-ir` and elapsed time) would normally increase, but because `accrue` is paused it keeps returning the **old** cached `index`/`lindex` without ever calling `var-set index next` [2](#0-1) .
3. A user calls `market.clar`'s `borrow`/`repay` (which route to `vault-system-borrow`/`vault-system-repay`), which call the vault's `system-borrow`/`system-repay`. These functions call `accrue` (which is a no-op pass-through), then read `(var-get index)` directly to compute `scaled-amount`/`principal-reduction` — i.e., the mutation (debt scaling) is evaluated using a value whose invalidating event (interest accrual) never fired, even though the guard (`asserts! (not (get borrow states)))`) checks a *different* pause bit and passes.
4. Debt is now scaled using a stale index for the whole duration `accrue` remains paused, while collateral valuations in `market.clar` for ztokens (which read the same cached indexes via `get-cached-indexes`) also silently use the frozen value [5](#0-4) .
5. This lets borrowers accrue debt interest-free (freezing the accrued yield the protocol/vault depositors should be earning) or lets repayers pay down debt without factoring in owed interest, for the duration of the mis-scoped pause — an unbounded single/multi-block window bounded only by how long the `accrue` flag stays set relative to `borrow`/`repay`.

### Impact Explanation
This falls under **High** — temporary freezing of unclaimed yield: interest that should accrue to lenders (via the `index`/`lindex` growth and the `treasury-lp` fee-reserve mint path, which is also skipped while `accrue` is paused [6](#0-5) ) is silently withheld from lenders and the treasury while active borrows continue to be serviced at a stale rate.

### Likelihood Explanation
Likelihood depends entirely on an operational/admin action (setting the `accrue` bit without also pausing `borrow`/`repay`), which is a plausible but not routine misconfiguration of the pause bitmap rather than something any external attacker can trigger unilaterally. I could not fully verify from the indexed code whether any admin runbook or DAO proposal script ever sets `accrue` alone (this would require inspecting DAO proposal scripts, which were only partially covered in my search), so likelihood is uncertain and leans toward the "deliberate design decision" exclusion if the granular pause flags are intentionally independent for operational flexibility.

### Recommendation
Either (a) make `system-borrow`/`system-repay` (and any ztoken price resolution path) revert when `accrue` is paused instead of silently reusing stale state, or (b) tie the `accrue` pause bit to also imply pausing `borrow`/`repay`/ztoken-price-resolution so debt/collateral math can never proceed against an un-rolled-forward index.

### Proof of Concept
Conceptual sequence (single block is sufficient, no multi-block wait required to demonstrate the mechanism, though real-world profit requires elapsed time before the pause):
1. Admin sets `pause-states` on `v0-vault-stx.clar` with `accrue: true`, `borrow: false`, `repay: false`.
2. Time elapses (blocks pass) such that `next-index` > current `index` if accrual ran normally.
3. User calls `market.clar` `borrow`, which calls `vault-system-borrow` → `v0-vault-stx.clar` `system-borrow`.
4. Inside `system-borrow`, `(try! (accrue))` returns old `{index, lindex}` unchanged (pass-through) [7](#0-6) ; `idx` is bound from the same stale `(var-get index)` and used to compute `scaled-amount`, understating the real debt scaling factor relative to what true accrual would have produced.
5. Debt is recorded scaled to the stale index; when `accrue` is later unpaused and index jumps to catch up, the position's real debt appears smaller than it should have accumulated during the paused interval, at the expense of lender yield.

**Uncertainty note**: I could not verify in the indexed code (due to index size limits, some DAO proposal/admin script contents were not available) whether the protocol's operational tooling ever pauses `accrue` independently of `borrow`/`repay`, or whether this granular independence is a deliberate design choice reviewed and accepted by the team. If confirmation of actual independent-pause operational use is needed, a Devin session with full repository access would be required to check `dao-executor.clar` proposal scripts and any pause-management contracts.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1313-1319)
```text
      
      (ok true)))))

(define-public (repay (ft <ft-trait>) (amount uint) (on-behalf-of (optional principal)))
  (let ((address (contract-of ft))
        (asset (try! (get-asset address)))
        (asset-id (get id asset))
```
