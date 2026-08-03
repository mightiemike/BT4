[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L391-395)
```text
            (&MultisigAccountTimeLock[multisig_account]).timelock_period
        } else {
            0
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L1130-1158)
```text
        let old_metadata = multisig_account_resource.metadata;
        multisig_account_resource.metadata = simple_map::create<String, vector<u8>>();
        let metadata = &mut multisig_account_resource.metadata;
        let i = 0;
        while (i < num_attributes) {
            let key = keys[i];
            let value = values[i];
            assert!(
                !metadata.contains_key(&key),
                error::invalid_argument(EDUPLICATE_METADATA_KEY),
            );

            metadata.add(key, value);
            i += 1;
        }  spec {
            invariant i <= num_attributes;
            invariant forall k in 0..i: simple_map::spec_contains_key(metadata, keys[k]);
            invariant forall m in 0..i, n in 0..i: m != n ==> keys[m] != keys[n];
        };

        if (emit_event) {
            emit(
                MetadataUpdated {
                    multisig_account: multisig_address,
                    old_metadata,
                    new_metadata: multisig_account_resource.metadata,
                }
            )
        };
```
