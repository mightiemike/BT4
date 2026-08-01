[File: 'File Name: api/src/events.rs -> Scope: Critical. Unprivileged input can cause committed state to differ from the VM output that honest validators should derive.'] [Symbol: Context::state_view used by Account::find_resource] Can an unprivileged caller exploit the fact that Account::new captures `latest_ledger_info`/`ledger_version` once while find_resource independently calls self.context.state_view(Some(self.ledger_version)) to read the account resource used for find_event_key, such that a race between these reads and the later events.rs::list call

### Citations

**File:** api/src/events.rs (L161-202)
```rust
    ) -> BasicResultWith404<Vec<VersionedEvent>> {
        let ledger_version = latest_ledger_info.version();
        let events = self
            .context
            .get_events(
                &event_key,
                page.start_option(),
                page.limit(&latest_ledger_info)?,
                ledger_version,
            )
            .context(format!(
