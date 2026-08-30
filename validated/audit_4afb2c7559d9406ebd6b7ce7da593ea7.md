### Title
Pausing `accrue` silently freezes the interest index instead of reverting, letting `deposit`/`redeem`/`system-borrow` execute against a stale price/index - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar`)

### Summary
`accrue()` in the vault contracts (`v0-vault-sbtc.clar`, and identically in `v0-vault-stx.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) is designed to be called at the top of every state-changing entry point (`deposit`, `redeem`, `system-borrow`, `system-repay`, `set-fee-reserve`, `set-points-util`, etc.) to bring `index`/`lindex` up to date before those functions read them. When the `accrue` pause flag is set, `accrue()` does not revert - it silently returns the **stale** `{index, lindex}` pair (`ok { index: idx, lindex: lidx }`) as if accrual had succeeded normally [1](#0-0) .

### Finding Description
The intended invariant is: every caller of `accrue` gets an up-to-date `index`/`lindex` reflecting elapsed time, which downstream logic (share pricing, debt accounting, market notional valuation) depends on. When `pause-states.accrue` is true, this invariant is broken but the function still returns `(ok ...)` — a pause that passes through instead of reverting.

The bound value is the pair `(idx, lidx)` = `(var-get index, var-get lindex)` captured *before* the pause check [2](#0-1) . This value is then treated by every caller as the fresh, current index:

- `deposit` uses `convert-to-shares-preview` (which reads `lindex`) right after calling `(try! (accrue))`, so a paused accrual means shares are minted using an index that no longer reflects the true value of `assets` outstanding, decoupling minted shares from real economic backing [3](#0-2) .
- `redeem` similarly converts shares to assets via a stale `lindex` immediately after `(try! (accrue))` [4](#0-3) .
- `system-borrow` reads `idx` right after `(try! (accrue))` to compute new scaled debt / caps, using the stale, pre-pause index instead of the correct interest-accrued index [5](#0-4) .
- The market layer (`v0-4-market.clar`) also relies on `vault-accrue`/`accrue-and-cache` to obtain "current" indexes for pricing zTokens and computing debt notional values used in health checks (`get-notional-evaluation`, `calculate-asset-notional-value`) [6](#0-5) . If the vault's accrual is paused, the market's cached index simply freezes at whatever value existed when the pause began, and every health/LTV computation downstream continues to treat it as authoritative rather than failing safe.

Contrast this with the explicit design pattern used elsewhere in the codebase for the *liquidation* pause, which uses a grace period and is not silently pass-through but is checked and asserted against with `is-liquidation-paused` before allowing the action [7](#0-6) . The `accrue` pause has no equivalent guard on the calling side - callers of `accrue` never check whether accrual was actually paused; they simply proceed with whatever `{index, lindex}` was returned, `ok` or not.

Note that `set-pause-states` does contain some mitigation: it forces an `accrue()` call before pausing (to "capture pending interest") and jumps `last-update` to `stacks-block-time` on unpause (to "skip paused period") [8](#0-7) . This correctly prevents interest from *silently accruing* across the pause window. However, it does not close the root-cause gap: for the entire duration the pause is active, every `deposit`, `redeem`, and `system-borrow` call still succeeds using the frozen index instead of failing, and the market's own index cache (`accrue-and-cache`) will likewise return the same stale value for the whole block/period, since `vault-accrue` (which the market calls) returns `ok` unconditionally regardless of the pause.

### Impact Explanation
This lands on the "temporary freezing of funds" / accounting-desync impact class: while `accrue` is paused, deposits and redemptions continue to be priced off a frozen index rather than being blocked, and borrow-side debt/cap accounting in `system-borrow` is computed from the same frozen index. Legitimate users depositing/redeeming/borrowing during a pause window get share/debt conversions that don't reflect the economic reality the pause was presumably meant to protect (e.g., an emergency pause due to a suspected accounting bug or oracle issue), rather than the transaction reverting as a genuine pause should. This can misprice vault shares relative to underlying assets for the duration of the pause, causing value transfer between depositors/redeemers and freezing the correct value for other participants until accrual is resumed.

### Likelihood Explanation
This does not require any privileged compromise beyond the DAO's *legitimate, intended* use of the pause switch (`set-pause-states`) — a normal operational action, not a DAO compromise. Any time an operator pauses `accrue` (e.g., during an incident response), the pass-through silently continues to let `deposit`/`redeem`/`system-borrow` succeed against a stale index for as long as the pause remains active, so the bug reliably triggers on every relevant call made during that window - it is not a two-actor race or MEV-dependent scenario.

### Recommendation
Make `accrue()` revert (or make it distinguishable to its callers) when `pause-states.accrue` is true, and have `deposit`, `redeem`, `system-borrow`, `system-repay`, and any market-side callers (`vault-accrue`/`accrue-and-cache`) explicitly check for and reject on a paused-accrual state instead of silently proceeding with the frozen `{index, lindex}`. At minimum, callers that mutate share/debt balances (`deposit`, `redeem`, `system-borrow`) should assert accrual actually happened before using `index`/`lindex` for conversions.

### Proof of Concept
1. DAO calls `set-pause-states` with `accrue: true`. Per the pre-pause handling, a final real `accrue()` runs and captures pending interest, then `pause-states` is set [8](#0-7) .
2. Time passes (multiple blocks) while `accrue` remains paused.
3. A user calls `deposit(amount, min-out, recipient)`. Inside, `(try! (accrue))` hits the `PAUSED` branch and returns `(ok { index: idx, lindex: lidx })` using the *pre-pause* `lindex` [9](#0-8) .
4. `convert-to-shares-preview(amount)` is computed using that stale `lindex`, and shares are minted at `deposit` (line 761-793) as if no time/interest had passed, even though multiple blocks have elapsed since the pause began.
5. Meanwhile `system-borrow` calls made during the same pause window use the identical stale `idx` to compute new scaled debt and check `CAP-DEBT`, so debt accounting also freezes at the pre-pause index rather than the transaction reverting.
6. Because `accrue()` returns `ok` unconditionally, none of these call sites detect or reject the paused/stale condition — the pause "passes through" rather than reverting, and this persists for the entire duration the DAO leaves `accrue` paused.

I was unable to fully trace every downstream consumer of `vault-accrue`/`accrue-and-cache` in the market contract within the available context (e.g., all liquidation paths and socialize-debt interactions with a paused accrual state), so there may be additional or mitigating checks elsewhere in `v0-4-market.clar` that I could not confirm from the indexed content.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L721-746)
```text
(define-public (set-pause-states (states {deposit: bool, redeem: bool, borrow: bool, repay: bool, accrue: bool, flashloan: bool}))
  (begin
    (try! (check-dao-auth))
    (let ((current (var-get pause-states))
          (was-paused (get accrue current))
          (now-paused (get accrue states)))
      ;; When pausing accrue, accrue first to capture pending interest
      (if (and (not was-paused) now-paused)
          (begin (try! (accrue)) false)
          false)
      ;; When unpausing accrue, jump last-update to now to skip paused period
      (if (and was-paused (not now-paused))
          (var-set last-update stacks-block-time)
          false)
      (var-set pause-states states)
      
      (print {
        action: "vault-set-pause-states",
        caller: tx-sender,
        data: {
          vault: UNDERLYING,
          states: states
        }
      })
      
      (ok true))))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L761-793)
```text
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (account contract-caller)
      (CAP-SUPPLY (var-get cap-supply))
      (current-assets (var-get assets))
      (inkind (convert-to-shares-preview amount)))

    (asserts! (not (get deposit states)) ERR-PAUSED)
    (asserts! (var-get initialized) ERR-INIT)
    (asserts! (not (var-get in-flashloan)) ERR-REENTRANCY)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (>= inkind min-out) ERR-SLIPPAGE)
    (asserts! (<= (+ current-assets amount) CAP-SUPPLY) ERR-SUPPLY-CAP-EXCEEDED)

    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))

    (print {
      action: "deposit",
      caller: contract-caller,
      data: {
        depositor: account,
        recipient: recipient,
        amount: amount,
        shares-minted: inkind,
        assets: (+ current-assets amount)
      }
    })

    (ok inkind)))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L795-829)
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

  (print {
    action: "redeem",
    caller: contract-caller,
    data: {
      redeemer: account,
      recipient: recipient,
      shares-burned: amount,
      amount-received: inkind,
      assets: (- current-assets inkind)
    }
  })

  (ok inkind)))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L833-861)
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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L863-870)
```text
(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (CAP-DEBT (var-get cap-debt))
      (available-assets (get-available-assets))
      (scaled-principal (var-get principal-scaled))
      (idx (var-get index))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L544-574)
```text
(define-private (calculate-asset-notional-value
          (asset-entry {
              id: uint, addr: principal, decimals: uint,
              oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
              collateral: bool, debt: bool, price: uint })
          (acc { clist: (list 64 { aid: uint, amount: uint }),
                  dlist: (list 64 { aid: uint, scaled: uint }),
                  coll-total: uint,
                  debt-total: uint }))
  (let ((asset-id (get id asset-entry))
        (price (get price asset-entry))
        (decimals (get decimals asset-entry))
        (collateral-list (get clist acc))
        (debt-list (get dlist acc))
        (coll-amount (find-collateral-amount collateral-list asset-id))
        (coll-notional (if (> coll-amount u0)
                           (normalize (* coll-amount price) decimals false)
                           u0))

        (debt-scaled   (find-debt-scaled debt-list asset-id))
        (debt-notional (if (> debt-scaled u0) ;; use cache instead here
                           (let ((cached (unwrap-panic (accrue-and-cache asset-id)))
                                 (ib (get index cached))
                                 (actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                             (normalize (* actual price) decimals true))
                           u0)))

    { clist: collateral-list,
      dlist: debt-list,
      coll-total: (+ (get coll-total acc) coll-notional),
      debt-total: (+ (get debt-total acc) debt-notional) }))
```

**File:** local-testing/contracts/market/market.clar (L711-719)
```text
;; -- Liquidation: pause check -----------------------------------------------

(define-private (is-liquidation-paused (asset-id uint))
  (let ((manual-pause (var-get pause-liquidation))
        (global-grace-end (default-to u0 (map-get? liquidation-grace-periods GLOBAL-LIQUIDATION-GRACE-ID)))
        (asset-grace-end (default-to u0 (map-get? liquidation-grace-periods asset-id)))
        (global-grace-active (< stacks-block-time global-grace-end))
        (asset-grace-active (< stacks-block-time asset-grace-end)))
    (or manual-pause global-grace-active asset-grace-active)))
```
