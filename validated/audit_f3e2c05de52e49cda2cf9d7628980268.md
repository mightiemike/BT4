[1](#0-0) [2](#0-1)

### Citations

**File:** dkg/src/types.rs (L17-28)
```rust
#[derive(Clone, Serialize, Deserialize, CryptoHasher, Debug, PartialEq)]
pub struct DKGTranscriptRequest {
    dealer_epoch: u64,
}

impl DKGTranscriptRequest {
    pub fn new(epoch: u64) -> Self {
        Self {
            dealer_epoch: epoch,
        }
    }
}
```

**File:** dkg/src/types.rs (L44-55)
```rust
    pub fn epoch(&self) -> u64 {
        match self {
            DKGMessage::TranscriptRequest(request) => request.dealer_epoch,
            DKGMessage::TranscriptResponse(response) => response.metadata.epoch,
            DKGMessage::ChunkyTranscriptRequest(request) => request.dealer_epoch,
            DKGMessage::ChunkyTranscriptResponse(response) => response.metadata.epoch,
            DKGMessage::SubtranscriptSignatureRequest(request) => request.dealer_epoch,
            DKGMessage::SubtranscriptSignatureResponse(response) => response.dealer_epoch,
            DKGMessage::MissingTranscriptRequest(request) => request.dealer_epoch,
            DKGMessage::MissingTranscriptResponse(response) => response.transcript.metadata.epoch,
        }
    }
```
