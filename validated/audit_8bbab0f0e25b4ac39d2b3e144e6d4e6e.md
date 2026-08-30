Found a concrete analog: in `borrow()`, `system-borrow` sends the borrowed underlying tokens to `funds-receiver` (an external contract call via `send-underlying`/`vault-system-borrow`) **before** `debt-add-scaled` records the debt in `market-vault.clar`.

### Title
Debt-recording state update occurs after external token transfer in `borrow()`, enabling reentrant re-borrow against a stale (pre-debt) health snapshot - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`borrow()` computes `collateral-value`/`debt-value` and passes the post-conditions health check, then calls `vault-system-borrow` which transfers the underlying asset to `funds-receiver` via `send-underlying` inside the vault, and only afterward calls `contract-call? .v0-market-vault debt-add-scaled` to persist the new debt. If the receiving token is one whose `transfer`/mint path can execute receiver logic (e.g. a `receiver` that is a contract, or a wrapped-STX `with-stx` transfer callback), a reentrant call back into `borrow()` (or `collateral-remove`) for the same account happens while the market-vault's debt map still reflects the pre-borrow state, letting the health check re-evaluate on stale (lower) `debt-value` and permit additional borrowing beyond the intended LTV before the first call's `debt-add-scaled` commits.

### Finding Description
`borrow()` in `mainnet/contracts/market/v0-4-market.clar` reads `position`/`debt-value` at the top of the function (lines 1246-1267), performs the post-condition health check (line 1287, `is-healthy-with-mask`), then executes the token transfer (`vault-system-borrow`, line 1289) before updating the persisted debt in `v0-market-vault` (`debt-add-scaled`, lines 1292-1296) [1](#0-0) . This is the same ordering defect as the sellNFT bug: a health/state check is performed, an external call capable of reentrancy is made, and only afterward is the authoritative state (the analog of the buy-order/receipt deletion) written. `vault-system-borrow` ultimately calls `system-borrow` on the underlying vault, which calls `send-underlying amount receiver` [2](#0-1) , an external transfer to `receiver` performed strictly before the market's `debt-add-scaled` call commits the new debt to `v0-market-vault`'s `debt`/`registry` maps [3](#0-2) . Because `debt-add-scaled` is the only step that marks the increased-debt mask/`last-borrow-block`, any reentrant call made during the transfer sees a stale, healthier position.

### Impact Explanation
If reentrancy is achievable through the receiver-controlled path (e.g. STX wrapper `with-stx`-based transfer, or a token/receiver combination that yields control before `debt-add-scaled` executes), a caller could re-invoke `borrow()` for the same account and pass the health check against the pre-update debt state, allowing the account to accumulate more debt than the LTV parameters permit — a form of protocol insolvency exposure (borrowing beyond backed collateral), which falls under the in-scope "protocol insolvency" impact class.

### Likelihood Explanation
Likelihood depends entirely on whether any of the whitelisted `ft-trait` implementations (STX wrapper, sBTC, USDC, USDH, stSTX, stSTXbtc, or their zTokens) can trigger a callback into `market.clar` during their `transfer`/mint step. All current registered assets appear to be plain SIP-010 tokens without receiver hooks, and the wrapped-STX path uses `as-contract?`/`with-stx`, which is a controlled pattern rather than an arbitrary external call. I could not confirm within the indexed contracts that any registered asset's transfer implementation invokes attacker-controlled code capable of reentering `market.clar`; this is the key uncertainty that determines whether the ordering defect is actually exploitable versus merely a latent ordering risk.

### Recommendation
Persist the debt state update (`debt-add-scaled`) before performing the external token transfer (`vault-system-borrow`/`send-underlying`), or add an explicit reentrancy guard around `borrow()` (and the analogous `collateral-remove`/`liquidate` flows) similar to the `in-flashloan` guard already used in the vault contracts, so that no external call can be interleaved between the health check and the corresponding state write.

### Proof of Concept
Not constructible with confidence from the indexed contracts alone: a working PoC requires demonstrating that a receiver/token combination accepted by `get-asset` can execute code during `vault-system-borrow`'s transfer step, and I did not find such a callback path in the reviewed vault/token contracts (`v0-vault-*.clar` `transfer`/`deposit`/`system-borrow` functions call straightforward `ft-transfer?`/`stx-transfer?` primitives with no receiver-side hook). Given the "Ask-only" scope and index limitations, verifying exploitability would require either (a) full review of `v0-4-market.clar`'s `with-stx`/`with-ft` `as-contract?` post-condition semantics for reentrancy windows, or (b) starting a Devin session with full repository access to trace call semantics precisely.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1238-1296)
```text
(define-public (borrow (ft <ft-trait>) (amount uint) (receiver (optional principal)) (price-feeds (optional (list 3 (buff 8192)))))
  (let ((address (contract-of ft))
        (asset (try! (get-asset address)))
        (asset-id (get id asset))
        (account contract-caller)
        (funds-receiver (match receiver recv recv contract-caller))
        (feeds-check (try! (write-feeds price-feeds)))
        
        ;; Step 1: Get position WITHOUT resolving prices
        (position (try! (get-position account)))
        (mask (get mask position))
        
        ;; Step 2: Accrue user's positions (populates cache for ztokens)
        (u-debt (accrue-user-debts (get debt position)))
        (u-coll (accrue-user-collateral (get collateral position)))
        
        ;; Step 3: Accrue the asset being borrowed (needed for index access)
        (unused (accrue-and-cache asset-id))
        
        ;; Step 4: NOW safe to resolve prices (cache is populated)
        (assets (get-assets mask))

        ;; Calculate current health with current mask
        (current-group (try! (get-egroup mask)))
        (current-ltvb (buff-to-uint-be (get LTV-BORROW current-group)))

        ;; LTV
        (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
        (collateral-value (get collateral notional-valued-assets))
        (debt-value (get debt notional-valued-assets)))

    ;; preconditions
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (get debt asset) ERR-BORROW-DISABLED)
    (asserts! (is-healthy collateral-value debt-value current-ltvb) ERR-UNHEALTHY)

    ;; Calculate FUTURE debt (after adding this debt)
    ;; For debt: bit position = asset-id + 64 (DEBT-OFFSET)
    (let ((future-mask (bit-or mask (pow u2 (+ asset-id DEBT-OFFSET))))
          (future-group (try! (get-egroup future-mask)))
          ;; Per-egroup borrow disable check (uses FUTURE egroup, not current)
          ;; Each bit in BORROW-DISABLED-MASK corresponds to a debt asset ID (NOT offset by 64)
          (disabled-borrow-mask (get BORROW-DISABLED-MASK future-group))
          (debt-increase (try! (get-asset-value asset amount true)))
          (debt-post-increased (+ debt-value debt-increase)))

    ;; Check if this specific asset is disabled for borrowing in the FUTURE egroup
    (asserts! (is-eq (bit-and disabled-borrow-mask (pow u2 asset-id)) u0) ERR-EGROUP-ASSET-BORROW-DISABLED)
    ;; postconditions
    (asserts! (try! (is-healthy-with-mask collateral-value debt-post-increased future-mask)) ERR-UNHEALTHY)

    (try! (vault-system-borrow asset-id amount funds-receiver))
    (let ((scaled-debt-added (convert-to-scaled-debt asset-id amount true))
          (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id)))))
      (try! (contract-call? .v0-market-vault
                            debt-add-scaled
                            account
                            scaled-debt-added
                            asset-id))
```

**File:** mainnet/contracts/vault/v0-vault-usdh.clar (L863-898)
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

**File:** mainnet/contracts/market/v0-market-vault.clar (L442-471)
```text
(define-public (debt-add-scaled (account principal) (scaled-amount uint) (asset-id uint))
  (let ((states (var-get pause-states))
        (entry (resolve-or-create account))
        (user-id (get id entry))
        (mask (get mask entry))
        (update-mask (mask-update mask asset-id false true)) ;; debt, insert
        ;; Oracle frontrunning protection: record current block when borrowing
        (updated-entry (merge entry { mask: update-mask, last-update: stacks-block-time, last-borrow-block: stacks-block-height }))
        (result (add-user-scaled-debt user-id asset-id scaled-amount)))

    (try! (check-impl-auth))
    (asserts! (not (get debt-add states)) ERR-PAUSED)
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (insert updated-entry)

    (print {
      action: "debt-add-scaled",
      caller: contract-caller,
      data: {
        account: account,
        asset-id: asset-id,
        scaled-amount: scaled-amount,
        updated-scaled-debt: result,
        mask-before: mask,
        mask-after: update-mask
      }
    })
      
    (ok result)))
```
