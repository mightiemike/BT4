[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** storage/aptosdb/src/utils/iterators.rs (L193-199)
```rust
impl Iterator for EventsByVersionIter<'_> {
    type Item = Result<Vec<ContractEvent>>;

    fn next(&mut self) -> Option<Self::Item> {
        self.next_impl().transpose()
    }
}
```

**File:** storage/aptosdb/src/ledger_db/event_db.rs (L70-78)
```rust
        let mut iter = self.db.iter::<EventSchema>()?;
        // Grab the first event and then iterate until we get all events for this version.
        iter.seek(&version)?;
        while let Some(((ver, _index), event)) = iter.next().transpose()? {
            if ver != version {
                break;
            }
            events.push(event);
        }
```

**File:** storage/aptosdb/src/ledger_db/event_db.rs (L167-184)
```rust
        events
            .iter()
            .enumerate()
            .try_for_each::<_, Result<_>>(|(idx, event)| {
                if let ContractEvent::V1(v1) = event {
                    if !skip_index {
                        batch.put::<EventByKeySchema>(
                            &(*v1.key(), v1.sequence_number()),
                            &(version, idx as u64),
                        )?;
                        batch.put::<EventByVersionSchema>(
                            &(*v1.key(), version, v1.sequence_number()),
                            &(idx as u64),
                        )?;
                    }
                }
                batch.put::<EventSchema>(&(version, idx as u64), event)
            })?;
```

**File:** storage/aptosdb/src/ledger_db/event_db.rs (L222-239)
```rust
        for events in events_iter {
            let events = events?;
            ret.push(events.len());

            if let Some(ref mut batch) = indices_batch {
                for event in events {
                    if let ContractEvent::V1(v1) = event {
                        batch.delete::<EventByKeySchema>(&(*v1.key(), v1.sequence_number()))?;
                        batch.delete::<EventByVersionSchema>(&(
                            *v1.key(),
                            current_version,
                            v1.sequence_number(),
                        ))?;
                    }
                }
            }
            current_version += 1;
        }
```
