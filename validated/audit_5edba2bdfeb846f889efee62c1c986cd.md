### Title
Missing recipient validation in vault `deposit`/`redeem` allows shares or assets to be permanently stranded in the vault contract - (File: mainnet/contracts/vault/v0-vault-stx.clar)

### Summary
The Zest vault contracts (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`) implement a `transfer` function that explicitly guards against sending vault shares to the vault's own principal, but the `deposit` and `redeem` entry points — which accept an attacker/user-supplied `recipient` principal and move value (mint shares / send underlying) directly to that address — omit the identical guard. This mirrors the reported RMM `PrimitiveEngine.withdraw` issue: a value-moving operation trusts a caller-supplied recipient without validating it isn't the contract itself, so a single mistaken call permanently strands funds in the vault.

### Finding Description
In `v0-vault-stx.clar`'s `transfer`, the developers explicitly added a precondition: [1](#0-0) 

This shows the team is aware that sending vault tokens/value to `current-contract` (the vault itself) is an invalid, value-destroying operation, and guards against it with `ERR-TOKENIZED-VAULT-PRECONDITIONS`.

However, the same check is missing from `deposit`, which mints shares directly to the caller-supplied `recipient`: [2](#0-1) 

And from `redeem`, which burns the caller's shares and sends underlying to the caller-supplied `recipient`: [3](#0-2) 

The same pattern (guard present only in `transfer`, absent in `deposit`/`redeem`) is replicated identically across the other vaults, e.g.: [4](#0-3) [5](#0-4) 

If a user calls `redeem` with `recipient` set to the vault's own contract principal (`current-contract`), their shares are burned via `ft-burn?` and the vault's internal `assets` var is decremented, but `send-underlying` transfers the underlying asset to the vault contract itself — a no-op transfer that leaves the tokens sitting in the vault while the user's claim (shares) has already been destroyed. The user's funds are irrecoverably lost in a single transaction with no interference from any other party. Similarly, calling `deposit` with `recipient` = vault principal mints shares to an address the depositor (or anyone else) cannot subsequently call `redeem` from, since `redeem`'s `account` is bound to `contract-caller`, permanently locking the deposited principal's claim.

### Impact Explanation
This is a single-transaction, single-user mistake (matches the allowed analog: "a multi-step entry point that strands value on abort" / lack of recipient validation stranding value) — not a case of two users interfering. The impact is permanent freezing of a user's deposited principal or redeemed underlying value, which falls under the accepted "permanent freezing of funds" impact class (High/Critical depending on amounts, matching the classification of the original report where it was flagged as a data-validation gap warranting a short-term fix).

### Likelihood Explanation
Likelihood is driven by user/integrator error (e.g., a frontend or bot misconfiguring the recipient to the vault contract address, or a naive integration passing the wrong principal), exactly the same class of mistake described in the original report's "Alice" exploit scenario. No malicious actor or privileged access is required — a single erroneous call is sufficient.

### Recommendation
Add the same precondition already used in `transfer` to both `deposit` and `redeem` (and any other vault entry point accepting a `recipient`/similar principal), e.g.:
```clarity
(asserts! (not (is-eq current-contract recipient)) ERR-TOKENIZED-VAULT-PRECONDITIONS)
```
placed immediately after other precondition checks in `deposit` and `redeem`, across all affected vault contracts (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`).

### Proof of Concept
1. User calls `(contract-call? .v0-vault-stx deposit amount min-out (as-contract tx-sender))` — i.e., sets `recipient` to `current-contract` (the vault's own principal), instead of their own address.
2. `deposit` executes `receive-underlying` (pulling `amount` from the user) then `(ft-mint? zft inkind recipient)` mints shares to the vault contract itself. [6](#0-5) 
3. No code path allows the vault to call `redeem` on its own held shares (since `redeem`'s `account` binds to `contract-caller`, which would be the vault itself only through an internal call not present in the contract's public interface) — the shares, and thus the underlying deposited amount, are permanently unclaimable.
4. Alternatively, a user calls `redeem` with `recipient` = vault principal: shares are burned via `(ft-burn? zft amount account)` and `assets` decremented, but `(send-underlying inkind recipient)` transfers to the vault itself, leaving the user with neither shares nor underlying. [7](#0-6)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L752-759)
```text
(define-public (transfer (amount uint) (from principal) (to principal) (memo (optional (buff 34))))
  (begin
    (try! (accrue))
    (asserts! (or (is-eq tx-sender from) (is-eq contract-caller from)) (err u4))
    (asserts! (not (is-eq current-contract to)) ERR-TOKENIZED-VAULT-PRECONDITIONS)
    (try! (ft-transfer? zft amount from to))
    (match memo to-print (print to-print) 0x)
    (ok true)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L763-795)
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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L797-831)
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L761-793)
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

**File:** mainnet/contracts/vault/v0-vault-ststx.clar (L811-831)
```text
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
