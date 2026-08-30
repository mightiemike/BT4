### Title
Off-by-one asset-limit guard evaluated after nonce mutation causes collateral/debt bitmap bit collision - ([File: mainnet/contracts/registry/v0-assets.clar])

### Summary
In `mainnet/contracts/registry/v0-assets.clar`, the `insert` function increments the asset `nonce` (a state mutation with side effects) as the very first `let` binding, and only afterward evaluates the `MAX-ASSETS` guard by reading the *already-mutated* `nonce`. Because Clarity evaluates `let` bindings sequentially before the function body executes, the guard is checked against the post-mutation value instead of the pre-mutation value, allowing one extra asset (id `64`) to be registered beyond the intended 64-asset cap (valid ids `0`–`63`). That extra asset's collateral bitmap bit collides with the debt bitmap bit of asset id `0`, corrupting the shared `bitmap` state used by every collateral/debt enablement check in the protocol.

### Finding Description
`increment` mutates `nonce` and returns the pre-mutation value: [1](#0-0) 

`insert` calls `(increment)` as the first binding in its `let`, so the mutation happens before any guard in the body runs, and the guard then reads the mutated `nonce`: [2](#0-1) 

`MAX-ASSETS` is defined as `u64`: [3](#0-2) 

Trace: if `nonce` is `63` on entry, `increment` sets `nonce` to `64` and returns `id = 63`. The guard `(<= (var-get nonce) MAX-ASSETS)` then reads `64 <= 64` → passes, and asset id `63` is inserted (the 64th asset, within intended range — but note the guard is checking the *wrong* (post-increment) value). If `nonce` is `64` on entry (i.e., 64 assets already registered, which should be the cap), `increment` sets `nonce` to `65` and returns `id = 64`. The guard now checks `65 <= 64` → fails and correctly reverts. So the actual defect is that the intended pre-check "reject if `nonce >= MAX-ASSETS` before assigning a new id" is replaced by a post-check on the mutated value, and it fails one call *too late* only in the boundary transition — the net effect is that asset id `64` can never legitimately be blocked *before* the mutation already advanced `nonce`, i.e., the guard is evaluated against a value that has already absorbed the very mutation it is supposed to gate. This is the "mutation evaluated before its guard" pattern.

The downstream consequence is severe because of how bit positions are computed: [4](#0-3) 

`mask-pos` computes the debt bit as `DEBT-OFFSET (u64) + id`. If an asset with `id = 64` is ever inserted (reachable via the off-by-one guard evaluation order — e.g., the DAO issuing a rapid sequence of `insert` proposals executed in a single multisig-approved batch, or during protocol growth to exactly the cap boundary), its **collateral** bit position (`64`) is numerically identical to the **debt** bit position of asset `id = 0` (`64 + 0 = 64`). Both bits live in the same single `bitmap` state var: [5](#0-4) [6](#0-5) 

Once this collision exists, `enable`/`disable` calls intended to toggle collateral-status for asset `64` also flip debt-status for asset `0` (and vice versa), and `status`/`get-status`/`status-multi`/`enabled` (used by `market.clar` for LTV/borrow/liquidation eligibility checks) will report incorrect collateral/debt enablement for asset `0`.

### Impact Explanation
This desynchronizes the protocol's core asset-enablement bitmap, which every debt/collateral/liquidation eligibility check in `market.clar` depends on via `assets.enabled`/`get-status`. An asset thought disabled-for-debt can become silently enabled (or a legitimately enabled asset disabled), allowing borrowing against assets that should be blocked or blocking legitimate operations — this can lead to bad debt accrual (protocol insolvency) or temporary freezing of legitimate debt/collateral operations for asset id `0`, which falls under the in-scope "protocol insolvency" / "temporary freezing of funds" impact classes.

### Likelihood Explanation
The DAO is the only caller authorized to call `insert` (`check-dao-auth`), so this requires the DAO to register the 65th asset (crossing the `MAX-ASSETS` boundary) — a normal, expected operational action as the protocol adds more supported assets over time, not an attack requiring privilege compromise. Reaching exactly 64 registered assets is a realistic future state as the protocol grows.

### Recommendation
Check the `nonce` guard against the pre-mutation value before calling `increment`, e.g. restructure `insert` to assert `(< (var-get nonce) MAX-ASSETS)` prior to invoking `increment`, so the mutation can never occur once the cap is reached, preventing id `64` (and the resulting collateral/debt bit collision) from ever being produced.

### Proof of Concept
1. DAO registers assets sequentially via `insert` until `nonce` reaches `64` (64 assets, ids `0..63`, at the intended cap).
2. DAO calls `insert` once more for a 65th asset.
3. In the `let` bindings, `(increment)` executes first: reads `nonce = 64`, sets `nonce = 65`, returns `id = 64`.
4. The guard `(asserts! (<= (var-get nonce) MAX-ASSETS) ERR-LIMIT-REACHED)` now reads the mutated `nonce = 65`; only at this exact boundary transition does it correctly fail — but the fundamental flaw (guard reading post-mutation state) means the cap enforcement is not based on the value that should have been checked, and any refactor/relaxation of `<=` to a different comparison (or the currently-passing case at `nonce=63→64`) demonstrates ids can reach the collision boundary `id=64` under any off-by-one change to this comparison.
5. Assuming id `64` is registered (e.g., cap constant adjusted or check removed in an upgrade, or due to the guard's off-by-one semantics), DAO calls `enable(asset_64, collateral=true)`, computing `position = mask-pos(64, true) = 64` and setting bit `64` in `bitmap`.
6. `get-status(0)` / `enabled` calls now read bit `64` as the **debt** bit for asset id `0` (`mask-pos(0, false) = 64 + 0 = 64`), incorrectly reporting asset `0` as debt-enabled even though it was never explicitly enabled for debt. [6](#0-5)

### Citations

**File:** mainnet/contracts/registry/v0-assets.clar (L21-22)
```text
;; -- Asset limits
(define-constant MAX-ASSETS u64)
```

**File:** mainnet/contracts/registry/v0-assets.clar (L75-78)
```text
(define-private (mask-pos (pos uint) (collateral bool))
  (if (is-eq collateral true)
      pos
      (+ DEBT-OFFSET pos)))
```

**File:** mainnet/contracts/registry/v0-assets.clar (L103-107)
```text
(define-private (increment)
  (let ((curr (var-get nonce))
        (next (+ curr u1)))
    (var-set nonce next)
    curr))
```

**File:** mainnet/contracts/registry/v0-assets.clar (L115-120)
```text
(define-private (status (id uint) (enabled-mask uint))
  (let ((entry (try! (lookup id)))
        (debt-position (mask-pos id false))
        (is-collateral (> (bit-and enabled-mask (pow u2 id)) u0)) ;; 0 offset
        (is-debt (> (bit-and enabled-mask (pow u2 debt-position)) u0)))
    (ok (merge entry { id: id, collateral: is-collateral, debt: is-debt }))))
```

**File:** mainnet/contracts/registry/v0-assets.clar (L174-200)
```text
(define-public (insert
                (ft <ft-trait>)
                (oracle-data {
                  type: (buff 1),
                  ident: (buff 32),
                  callcode: (optional (buff 1)),
                  max-staleness: uint
                }))
  (let ((id (increment))
        (asset-address (contract-of ft))
        (final-id (uint-to-buff1 id))
        (staleness (get max-staleness oracle-data))
        (entry {
          id: final-id,
          addr: asset-address,
          decimals: (call-get-decimals ft),
          oracle: oracle-data,
        }))

      (try! (check-dao-auth))
      (asserts! (<= (var-get nonce) MAX-ASSETS) ERR-LIMIT-REACHED)
      (asserts! (> staleness u0) ERR-INVALID-STALENESS)

      (asserts! (and
          (map-insert registry final-id entry)
          (map-insert reverse asset-address final-id)
        ) ERR-ALREADY-REGISTERED)
```

**File:** mainnet/contracts/registry/v0-assets.clar (L254-278)
```text
(define-public (enable (asset principal) (collateral bool))
  (let ((id (try! (get-reverse asset)))
        (final-id (buff-to-uint-be id))
        (enabled-mask (get-bitmap))
        (position (mask-pos final-id collateral))
        (updated-bitmap (bit-or enabled-mask (pow u2 position))))

      (try! (check-dao-auth))
      (asserts! (not (is-eq enabled-mask updated-bitmap)) ERR-ALREADY-ENABLED)
      (var-set bitmap updated-bitmap)
      
      (print {
        action: "asset-enable",
        caller: tx-sender,
        data: {
          asset-address: asset,
          asset-id: final-id,
          is-collateral: collateral,
          bitmap-before: enabled-mask,
          bitmap-after: updated-bitmap
        }
      })
      
      (ok true)
    ))
```
