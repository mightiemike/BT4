[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/move-stdlib/sources/cmp.move (L107-140)
```text
    #[test]
    fun test_compare_numbers() {
        assert!(compare(&1, &5).is_ne(), 0);
        assert!(!compare(&1, &5).is_eq(), 0);
        assert!(compare(&1, &5).is_lt(), 1);
        assert!(compare(&1, &5).is_le(), 2);
        assert!(compare(&5, &5).is_eq(), 3);
        assert!(!compare(&5, &5).is_ne(), 3);
        assert!(!compare(&5, &5).is_lt(), 4);
        assert!(compare(&5, &5).is_le(), 5);
        assert!(!compare(&7, &5).is_eq(), 6);
        assert!(compare(&7, &5).is_ne(), 6);
        assert!(!compare(&7, &5).is_lt(), 7);
        assert!(!compare(&7, &5).is_le(), 8);

        assert!(!compare(&1, &5).is_eq(), 0);
        assert!(compare(&1, &5).is_ne(), 0);
        assert!(compare(&1, &5).is_lt(), 1);
        assert!(compare(&1, &5).is_le(), 2);
        assert!(!compare(&1, &5).is_gt(), 1);
        assert!(!compare(&1, &5).is_ge(), 1);
        assert!(compare(&5, &5).is_eq(), 3);
        assert!(!compare(&5, &5).is_ne(), 3);
        assert!(!compare(&5, &5).is_lt(), 4);
        assert!(compare(&5, &5).is_le(), 5);
        assert!(!compare(&5, &5).is_gt(), 5);
        assert!(compare(&5, &5).is_ge(), 5);
        assert!(!compare(&7, &5).is_eq(), 6);
        assert!(compare(&7, &5).is_ne(), 6);
        assert!(!compare(&7, &5).is_lt(), 7);
        assert!(!compare(&7, &5).is_le(), 8);
        assert!(compare(&7, &5).is_gt(), 7);
        assert!(compare(&7, &5).is_ge(), 8);
    }
```

**File:** aptos-move/framework/move-stdlib/sources/cmp.move (L154-160)
```text
    #[test]
    fun test_compare_structs() {
        assert!(compare(&SomeStruct { field_1: 1, field_2: 2}, &SomeStruct { field_1: 1, field_2: 2}) is Ordering::Equal, 0);
        assert!(compare(&SomeStruct { field_1: 1, field_2: 2}, &SomeStruct { field_1: 1, field_2: 3}) is Ordering::Less, 1);
        assert!(compare(&SomeStruct { field_1: 1, field_2: 2}, &SomeStruct { field_1: 1, field_2: 1}) is Ordering::Greater, 2);
        assert!(compare(&SomeStruct { field_1: 2, field_2: 1}, &SomeStruct { field_1: 1, field_2: 2}) is Ordering::Greater, 3);
    }
```
