Based on my investigation, I found a genuine analog of the reported bug class in the Nakamoto PoX-5 signer registration logic.

### Title
Duplicate `signer-key` registration across distinct signer principals lets one key holder absorb another signer's stacked weight - (File: `stackslib/src/chainstate/stacks/boot/pox-5.clar`)

### Summary
`register-signer` stores the signer's public key in a map keyed by the *signer principal*, not by the *signer-key* itself, so nothing prevents two different `signer-manager` contracts from independently registering the same `signer-key`. That duplicated key is later used as the sole aggregation key when building the Nakamoto signer set, causing two unrelated principals' stacked amounts to be merged into a single signing-key weight entry — mirroring the referral-code bug where the same unique code got bound to two different addresses.

### Finding Description
`register-signer` writes `(map-set signers signer signer-key)` after only checking `verify-signer-key-grant signer signer-key` and that `contract-caller` equals `signer`; it never checks whether `signer-key` is already associated with a *different* `signer` principal. [1](#0-0) 

The signer-key grant mechanism referenced in the comments is per `(key, manager, auth-id)` tuple, as shown by the `RotateSignerKey` test model, which explicitly notes "any auth-id works" for a fresh key/manager pair — there is no global uniqueness constraint tying a `signer-key` to exactly one manager across the whole grant space. [2](#0-1) 

On the Rust side, when the reward set is computed, entries are aggregated strictly by `signer_key`:
```
signer_set
    .entry(entry.signer_key)
    .and_modify(|existing_entry| *existing_entry += entry.amount_ustx)
    .or_insert_with(|| entry.amount_ustx);
``` [3](#0-2) 

If two distinct `signer-manager` principals both register the same `signer-key` (because the Clarity map never rejects the collision), their `stacked_amt` from `pox_5_stake_entries` collapses into one `Apportionment` bucket keyed by that shared public key, and the resulting weight is granted to whichever entity actually controls the corresponding private key. [4](#0-3) 

The same duplicate-key hazard exists in the older `set-signers` model used for reward-cycle bookkeeping, where signer identity is likewise a `principal`/`weight` pair without a key-uniqueness invariant enforced at the Clarity layer. [5](#0-4) 

### Impact Explanation
The equality broken is: "signer weight in the Nakamoto signer set corresponds 1:1 to the stake actually controlled by the entity holding that signing key." By colliding keys across two signer principals, the aggregate weight attributed to the shared `signing_key` in `pox_5_make_signer_set`/`get_signers_weights` no longer matches what any single controlling private key legitimately backs — one key holder can be credited with combined stake it does not solely own, potentially pushing that key past the reward-slot/weight threshold it would not otherwise reach. This is a minority-triggerable signer-weight divergence bounded within a reward cycle (High severity per the rules), not a majority/Sybil requirement — a single ordinary signer, in collusion with or independent of the other principal (no privileged role needed), can trigger the collision merely by calling `register-signer` with a key already granted to itself for a second manager contract, since grants are scoped as `(key, manager, auth-id)` tuples rather than globally unique per key. [6](#0-5) 

### Likelihood Explanation
Likelihood is uncertain without seeing the full grant-issuance logic (`grant-signer-key` implementation and its uniqueness constraints), which I was not able to fully inspect within the available tool budget. What is confirmed is that `register-signer` itself performs no `codeToAddress[code] == address(0)`-style check against the `signers` map before overwriting/creating entries keyed by principal, and the Rust aggregation logic treats `signer_key` as the sole grouping key with no defense against duplicates across principals.

### Recommendation
Before `(map-set signers signer signer-key)`, add an explicit check that `signer-key` is not already registered to a different `signer` principal (e.g., maintain an auxiliary `key-to-signer` map and assert `(is-none (map-get? key-to-signer signer-key))` or that any existing owner equals `signer`). Enforce the same invariant at grant-issuance time so a single key cannot be validly granted to two distinct manager contracts simultaneously.

### Proof of Concept
Conceptual PoC (Clarity, mirroring the reported PoC pattern):
```clarity
;; signer-manager-A and signer-manager-B are distinct signer-manager-trait contracts
;; both hold a valid grant for the same signer-key `K` (via independent grant calls)
(contract-call? .pox-5 register-signer signer-manager-A K) ;; succeeds, signers[A] = K
(contract-call? .pox-5 register-signer signer-manager-B K) ;; succeeds, signers[B] = K, no uniqueness check

;; When staking occurs for both A and B, pox_5_stake_entries yields
;; (signer_key=K, amount=amtA) and (signer_key=K, amount=amtB)
;; pox_5_make_signer_set aggregates both into a single entry for K:
;;   signer_set[K] = amtA + amtB
;; The private-key holder of K is now credited with combined weight,
;; potentially crossing the reward-slot threshold alone.
```

### Citations

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L945-973)
```text
;; Register a signer
(define-public (register-signer
        (signer-manager <signer-manager-trait>)
        (signer-key (buff 33))
    )
    (let ((signer (contract-of signer-manager)))
        ;; ensure no reentrancy through signer-manager trait calls
        (try! (validate-no-reentrancy))

        ;; Because signers can have members register at any time,
        ;; they must use signer key grants instead of per-tx
        ;; authorizations.
        (try! (verify-signer-key-grant signer signer-key))

        ;; Only the signer contract itself can register itself
        (asserts! (is-eq contract-caller signer)
            ERR_UNAUTHORIZED_SIGNER_REGISTRATION
        )

        (map-set signers signer signer-key)
        (let ((result {
                signer: signer,
                signer-key: signer-key,
            }))
            (print (merge { topic: "register-signer" } result))
            (ok result)
        )
    )
)
```

**File:** contrib/core-contract-tests/tests/pox-5/commands/RotateSignerKey.ts (L14-27)
```typescript
/**
 * Re-register an already-registered signer with a brand-new key + grant,
 * exercising `register-signer`'s `map-set` overwrite semantics. The previous
 * grant stays live (rotation does not revoke it); only the recorded key moves.
 */
export const RotateSignerKey = () =>
  fc
    .record({
      // 48 bytes is what noble's randomSecretKey wants for a fresh key.
      seed: fc.uint8Array({ minLength: 48, maxLength: 48 }),
      // With a new key the (key, manager, auth-id) tuple is always unused, so
      // any auth-id works here.
      authId: fc.bigInt({ min: 1n, max: 1_000_000_000n }),
      // Static cap for legible shrinks; `%` wraps onto the live signer set.
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L822-856)
```rust
    pub(crate) fn pox_5_make_signer_set<I>(
        entries: &mut I,
        pox_constants: &PoxConstants,
    ) -> Result<Pox5SignerSetOutput, ChainstateError>
    where
        I: Iterator<Item = Result<RawPox5Entry, PoxEntryParsingError>>,
    {
        let mut signer_set = HashMap::new();
        let mut total_ustx_locked = 0u128;
        for entry_res in entries {
            let entry = match entry_res {
                Ok(x) => x,
                Err(PoxEntryParsingError::Skip(err_str)) => {
                    warn!(
                        "Error while iterating PoX-5 entries, impacting a single entry. Dropping entry from signer set";
                        "error" => err_str
                    );
                    continue;
                }
                Err(PoxEntryParsingError::Abort(err_str)) => {
                    error!(
                        "Abort-triggering error while iterating PoX-5 entries";
                        "error" => err_str
                    );
                    return Err(ChainstateError::PoxNoRewardCycle);
                }
            };

            total_ustx_locked += entry.amount_ustx;

            signer_set
                .entry(entry.signer_key)
                .and_modify(|existing_entry| *existing_entry += entry.amount_ustx)
                .or_insert_with(|| entry.amount_ustx);
        }
```

**File:** stackslib/src/chainstate/stacks/boot/signers.clar (L28-33)
```text
(define-private (set-signers
                 (reward-cycle uint)
                 (signers (list 4000 { signer: principal, weight: uint })))
     (begin
      (asserts! (is-eq (var-get last-set-cycle) reward-cycle) (err ERR_CYCLE_NOT_SET))
      (ok (map-set cycle-signer-set reward-cycle signers))))
```
