[1](#0-0)

### Citations

**File:** stackslib/src/net/unsolicited.rs (L39-47)
```rust
/// Normally, the PeerNetwork will attempt to validate each message and pass it to the Relayer via
/// a NetworkResult.  However, some kinds of messages (such as these) cannot be always be
/// validated, because validation depends on chainstate data that is not yet available.  For
/// example, if this node is behind the burnchain chain tip, it will be unable to verify blocks
/// pushed to it for sortitions that have yet to be processed locally.
///
/// In the event that a message cannot be validated, the PeerNetwork will instead store these
/// messages internally (in `self.pending_messages`), and try to validate them again once the
/// burnchain view changes.
```
