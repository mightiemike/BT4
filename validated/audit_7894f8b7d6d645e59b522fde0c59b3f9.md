No vulnerability found for this question.

**Reasoning:** The exploit hypothesis rests on a misunderstanding of how `hashbrown::HashMap` resolves entries. `StructKey`'s `Hash` impl derives the hash from `idx` and `ty_args`, and separately `StructKeyRef`'s `Hash` impl is defined to always match `StructKey`'s hash for the *same* logical `(idx, ty_args)` pair [1](#0-0) . Even if two genuinely *different* `(StructNameIndex, ty_args)` pairs happened to produce a colliding hash value, that alone cannot cause `get_struct_tag`/`insert_struct_tag` to conflate them, because `hashbrown::HashMap::get`/`entry` always performs a secondary equality check via the `Equivalent` trait after locating a bucket by hash [2](#0-1) . That `equivalent` implementation compares the full `idx` and `ty_args` slice content (not just the hash), so a hash collision only causes an extra probe within the bucket, not an incorrect match [3](#0-2) .

Concretely:
- `get_struct_tag` looks up `StructKeyRef { idx, ty_args }` and only returns an entry whose `idx`/`ty_args` are `Equivalent` (i.e., structurally equal) to the query, not merely hash-equal [4](#0-3) .
- `insert_struct_tag` uses `cache.entry(key)` with the real `StructKey` (owned `idx` + `ty_args.to_vec()`), which again relies on full `Eq`/`Hash` correctness of `StructKey`, not raw hash values, to decide vacant vs. occupied slots [5](#0-4) .

This is exactly what standard Rust/`hashbrown` hash maps guarantee: hash values are only used to bucket entries for performance; correctness of key identity is always enforced by the full `Eq`/`Equivalent` comparison, regardless of hash collisions. The existing unit test `test_struct_key_equivalence_and_hash` already exercises this hash/equivalence consistency for several distinct keys, including nested generic structs, and confirms hash equality between `StructKey` and its `StructKeyRef` view for the same logical key — it does not (and cannot meaningfully) demonstrate that colliding hashes cause incorrect lookups, since `Type`'s `PartialEq`/`Eq` (derived, structural) is what ultimately gates correctness [6](#0-5) .

Therefore, no attacker-controlled `Vec<Type>` can cause `struct_name_idx_to_struct_tag_impl` to return a `StructTag` for the wrong `(StructNameIndex, ty_args)` pair purely via hash collision, because the cache path is protected by full structural equality checks independent of the hash value. This does not lead to a wrong resource `StateKey` or any corrupted committed state, proof, or accumulator root.

### Citations

**File:** third_party/move/move-vm/runtime/src/storage/ty_tag_converter.rs (L90-100)
```rust
impl<'a> hashbrown::Equivalent<StructKeyRef<'a>> for StructKey {
    fn equivalent(&self, other: &StructKeyRef<'a>) -> bool {
        &self.idx == other.idx && self.ty_args.as_slice() == other.ty_args
    }
}

impl hashbrown::Equivalent<StructKey> for StructKeyRef<'_> {
    fn equivalent(&self, other: &StructKey) -> bool {
        self.idx == &other.idx && self.ty_args == other.ty_args.as_slice()
    }
}
```

**File:** third_party/move/move-vm/runtime/src/storage/ty_tag_converter.rs (L102-116)
```rust
// Ensure hash is the same as for StructKeyRef.
impl Hash for StructKey {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.idx.hash(state);
        self.ty_args.hash(state);
    }
}

// Ensure hash is the same as for StructKey.
impl Hash for StructKeyRef<'_> {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.idx.hash(state);
        self.ty_args.hash(state);
    }
}
```

**File:** third_party/move/move-vm/runtime/src/storage/ty_tag_converter.rs (L175-184)
```rust
    pub(crate) fn get_struct_tag(
        &self,
        idx: &StructNameIndex,
        ty_args: &[Type],
    ) -> Option<PricedStructTag> {
        self.cache
            .read()
            .get(&StructKeyRef { idx, ty_args })
            .cloned()
    }
```

**File:** third_party/move/move-vm/runtime/src/storage/ty_tag_converter.rs (L186-218)
```rust
    /// Inserts the struct tag and its pseudo-gas cost ([PricedStructTag]) into the cache. Returns
    /// true if the tag was not cached before, and false otherwise.
    pub(crate) fn insert_struct_tag(
        &self,
        idx: &StructNameIndex,
        ty_args: &[Type],
        priced_struct_tag: &PricedStructTag,
    ) -> bool {
        // Check if already cached.
        if self
            .cache
            .read()
            .contains_key(&StructKeyRef { idx, ty_args })
        {
            return false;
        }

        let key = StructKey {
            idx: *idx,
            ty_args: ty_args.to_vec(),
        };
        let priced_struct_tag = priced_struct_tag.clone();

        // Otherwise, we need to insert. We did the clones outside the lock, and also avoid the
        // double insertion.
        let mut cache = self.cache.write();
        if let Entry::Vacant(entry) = cache.entry(key) {
            entry.insert(priced_struct_tag);
            true
        } else {
            false
        }
    }
```

**File:** third_party/move/move-vm/runtime/src/storage/ty_tag_converter.rs (L407-450)
```rust
    #[test]
    fn test_struct_key_equivalence_and_hash() {
        let struct_keys = [
            StructKey {
                idx: StructNameIndex::new(0),
                ty_args: vec![],
            },
            StructKey {
                idx: StructNameIndex::new(1),
                ty_args: vec![Type::U8],
            },
            StructKey {
                idx: StructNameIndex::new(2),
                ty_args: vec![Type::Bool, Type::Vector(Arc::new(Type::Bool))],
            },
            StructKey {
                idx: StructNameIndex::new(3),
                ty_args: vec![
                    Type::Struct {
                        idx: StructNameIndex::new(0),
                        ability: AbilityInfo::struct_(AbilitySet::singleton(Ability::Key)),
                    },
                    Type::StructInstantiation {
                        idx: StructNameIndex::new(1),
                        ty_args: Arc::new(vec![Type::Address, Type::Struct {
                            idx: StructNameIndex::new(2),
                            ability: AbilityInfo::struct_(AbilitySet::singleton(Ability::Copy)),
                        }]),
                        ability: AbilityInfo::generic_struct(
                            AbilitySet::singleton(Ability::Drop),
                            SmallBitVec::new(),
                        ),
                    },
                ],
            },
        ];

        for struct_key in struct_keys {
            let struct_key_ref = struct_key.as_ref();
            assert!(struct_key.equivalent(&struct_key_ref));
            assert!(struct_key_ref.equivalent(&struct_key));
            assert_eq!(calculate_hash(&struct_key), calculate_hash(&struct_key_ref));
        }
    }
```
