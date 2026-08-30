## Analysis

I found a direct analog of the Paladin `_pledge` precision-loss bug class in `system-borrow` across the vault contracts (`v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, and their siblings). The mechanism matches exactly: the cap check is performed using the *nominal* `amount`, while the value actually recorded into storage (`scaled-amount`) is computed with round-up division, so the real recorded debt can end up strictly greater than what the check verified — but in the *opposite* direction of the original finding (here it causes the cap to be under-protective rather than under-delivering to a receiver). [1](#0-0) 

### Title
Debt cap check uses un-rounded `amount` while stored `scaled-amount` is rounded up, allowing `CAP-DEBT` to be silently exceeded — (File: `mainnet/contracts/vault/v0-vault-usdc.clar` and sibling vaults)

### Summary
`system-borrow` validates `(<= (+ debt amount) CAP-DEBT)` using the raw `amount` parameter, but the value that is actually persisted to `principal-scaled` is `scaled-amount`, computed via `mul-div-up amount INDEX-PRECISION idx` — a round-*up* conversion. [2](#0-1)  Because the guard is evaluated against the pre-rounding `amount` while the state mutation persists the rounded-up scaled value, the actual on-chain debt that later `total-debt()` reconstructions read back out (via `mul-div-down scaled-principal idx INDEX-PRECISION`, see `accrue`) can be marginally larger than the amount that was checked against the cap. [3](#0-2) 

### Finding Description
1. `debt` is read from `total-debt()` (derived from `scaled-principal * idx / INDEX-PRECISION`, rounded down).
2. `scaled-amount` for the new borrow is computed as `mul-div-up amount INDEX-PRECISION idx` — rounded **up**. [4](#0-3) 
3. The cap guard `(asserts! (<= (+ debt amount) CAP-DEBT) ERR-DEBT-CAP-EXCEEDED)` compares `debt + amount` (the caller-supplied nominal amount) against `CAP-DEBT`, not `debt + (scaled-amount converted back to underlying)`. [5](#0-4) 
4. `principal-scaled` is then updated with the rounded-up `scaled-amount`, permanently baking in the rounding excess into the debt ledger. [6](#0-5) 
5. On any subsequent read (e.g. the next `system-borrow`, `accrue`, or liquidation call), `total-debt()`/`new-debt` recomputes the debt as `scaled-principal * idx / INDEX-PRECISION` (round down), which reflects the compounded rounding-up effect from step 2 applied at the moment of mutation, and this can be strictly greater than the `debt + amount` figure that satisfied the guard in step 3.

This is single-transaction/single-call: the "check" (`amount`) and the "committed mutation" (`scaled-amount`, rounded differently) diverge within the same function body — mirroring the Paladin `_pledge` pattern where the check used `amount` but the actually-applied effect used `bias` (a different rounding of the same value).

### Impact Explanation
The divergence is bounded by the rounding granularity of `mul-div-up` (at most `idx/INDEX-PRECISION` worth of underlying units per call, i.e. sub-unit rounding), so each individual violation is minor. Over many borrow calls the cap can be exceeded by an accumulating but still small margin. This does not constitute direct theft or insolvency by itself — it is a soft violation of a risk parameter (`CAP-DEBT`) rather than a fund-safety invariant, so it does not clearly rise to the Critical/High impact bar (theft, permanent freezing, or insolvency) required by the rules; the effect is a rounding-scale cap overshoot, not a value-stranding or fund-loss event.

### Likelihood Explanation
Reaching the divergence requires only a single, ordinary `system-borrow` call near the cap boundary — no privileged access, no multi-block timing, and no cross-user interference is needed. However, the magnitude of the divergence is limited to rounding-unit scale per call, so an attacker cannot leverage this to extract meaningful value; its only effect is a cap parameter being marginally unenforced.

### Recommendation
Compute the guard using the same rounding basis as what is committed to storage: convert `scaled-amount` back to underlying units (round up) before comparing to `CAP-DEBT`, i.e. `(asserts! (<= (+ debt (mul-div-up scaled-amount idx INDEX-PRECISION)) CAP-DEBT) ERR-DEBT-CAP-EXCEEDED)`, ensuring the check bounds the same rounded value that is actually persisted.

### Proof of Concept
Given `idx` and `INDEX-PRECISION = 1e12`, choose `amount` such that `amount * INDEX-PRECISION` is not evenly divisible by `idx` (trivial for any incumbent non-power-of-ten index). `scaled-amount = mul-div-up(amount, INDEX-PRECISION, idx)` rounds up by up to `idx/INDEX-PRECISION` underlying units worth of value. Call `system-borrow(amount, receiver)` repeatedly at `debt` values just below `CAP-DEBT` — each call passes the `(<= (+ debt amount) CAP-DEBT)` check using the exact `amount`, yet the stored `principal-scaled` reflects the rounded-up `scaled-amount`; on the next call's `total-debt()` recomputation, `debt` read back can exceed what the previous checks nominally allowed, letting cumulative debt creep past `CAP-DEBT` without ever failing the guard.

**Caveat on confidence:** this finding is a rounding-direction/parameter-enforcement bug rather than a fund-loss bug, and I am not fully confident it clears the stated Critical/High impact bar (theft, permanent freezing, or insolvency) since the magnitude is sub-unit per call. I'm flagging it as the closest legitimate analog found in-scope, but its impact classification is borderline.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L841-845)
```text
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L863-898)
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
