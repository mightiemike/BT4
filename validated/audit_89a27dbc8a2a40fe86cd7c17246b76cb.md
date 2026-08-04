[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-dkg/src/pvss/low_degree_test.rs (L207-213)
```rust
        // Compute $f(\omega^i)$ for all $i \in [0, n)$
        let dom = self.batch_dom.get_subdomain(fft_size);
        let mut f_evals = self.f;
        fft_assign(&mut f_evals, &dom);
        f_evals.truncate(fft_size);

        let v = all_lagrange_denominators(&self.batch_dom, fft_size, self.includes_zero);
```

**File:** crates/aptos-dkg/src/weighted_vuf/pinkas/mod.rs (L314-338)
```rust
        let mut k = 0;
        for (player, share) in proof {
            let w = wc.get_player_weight(player)?;
            for j in 0..w {
                sub_player_ids.push(
                    wc.get_virtual_player(player, j)
                        .expect("j < weight holds by construction")
                        .id,
                );
            }

            let apk = apks[player.id]
                .as_ref()
                .ok_or_else(|| anyhow!("Missing APK for player {}", player.get_id()))?;

            rks.push(&apk.0.rks);
            shares.push(share);

            ranges.push(k..k + w);
            k += w;
        }

        // Compute the Lagrange coefficients associated with those evaluation points
        let batch_dom = wc.get_batch_evaluation_domain();
        let lagr = lagrange_coefficients(batch_dom, &sub_player_ids[..], &Scalar::ZERO);
```

**File:** consensus/src/rand/secret_sharing/verifier.rs (L39-51)
```rust
    fn verify_structural(&self, share: &SecretShare) -> anyhow::Result<()> {
        let author = share.author();
        let index = self.config.get_id(author)?;
        // The Player id embedded in the share must match the author's validator index.
        // Without this check a malicious validator could declare any player id, leading
        // to incorrect reconstruction or out-of-bounds access during aggregation.
        ensure!(
            share.share.0.id == index,
            "Player id {} does not match expected index {} for author {}",
            share.share.0.id,
            index,
            author
        );
```
