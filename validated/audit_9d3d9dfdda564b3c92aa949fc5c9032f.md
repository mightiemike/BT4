No vulnerability found for this question.

Investigation summary: The reported prost-types issue was that `From<Timestamp> for SystemTime` in the Rust crate could panic on overflow when converting untrusted `Timestamp` values to `SystemTime`, and the fix required switching callers to a fallible `TryFrom`.

In protobuf-go, the analogous conversion is `Timestamp.AsTime()` in [1](#0-0) , which calls `time.Unix(int64(x.GetSeconds()), int64(x.GetNanos())).UTC()`. Unlike Rust's `SystemTime` conversion, Go's `time.Unix` performs pure integer arithmetic and does not panic for any combination of `int64` seconds/nanoseconds inputs — it is explicitly documented as a "best-effort" conversion that normalizes denormal values rather than erroring. A separate `CheckValid`/`check()` method is provided for callers that want strict RFC-range validation [2](#0-1) , but `AsTime()` itself was designed from the start to never panic on out-of-range input, which is confirmed by the test suite exercising `math.MinInt64`/`math.MaxInt64` seconds without panicking [3](#0-2) .

Since the underlying broken invariant in the report (a fallible/panicking conversion being used as an infallible one) does not exist in protobuf-go's Go implementation — the sink (`time.Unix`) is not capable of panicking on overflow regardless of input — there is no reachable path where untrusted `Timestamp` fields cause a panic or crash via `AsTime()`. This is a fundamental language/runtime difference (Rust's `SystemTime` arithmetic overflow-panics; Go's `time.Unix` does not), not a preserved bug class.

### Citations

**File:** types/known/timestamppb/timestamp.pb.go (L199-202)
```go
// AsTime converts x to a time.Time.
func (x *Timestamp) AsTime() time.Time {
	return time.Unix(int64(x.GetSeconds()), int64(x.GetNanos())).UTC()
}
```

**File:** types/known/timestamppb/timestamp.pb.go (L210-254)
```go
// CheckValid returns an error if the timestamp is invalid.
// In particular, it checks whether the value represents a date that is
// in the range of 0001-01-01T00:00:00Z to 9999-12-31T23:59:59Z inclusive.
// An error is reported for a nil Timestamp.
func (x *Timestamp) CheckValid() error {
	switch x.check() {
	case invalidNil:
		return protoimpl.X.NewError("invalid nil Timestamp")
	case invalidUnderflow:
		return protoimpl.X.NewError("timestamp (%v) before 0001-01-01", x)
	case invalidOverflow:
		return protoimpl.X.NewError("timestamp (%v) after 9999-12-31", x)
	case invalidNanos:
		return protoimpl.X.NewError("timestamp (%v) has out-of-range nanos", x)
	default:
		return nil
	}
}

const (
	_ = iota
	invalidNil
	invalidUnderflow
	invalidOverflow
	invalidNanos
)

func (x *Timestamp) check() uint {
	const minTimestamp = -62135596800  // Seconds between 1970-01-01T00:00:00Z and 0001-01-01T00:00:00Z, inclusive
	const maxTimestamp = +253402300799 // Seconds between 1970-01-01T00:00:00Z and 9999-12-31T23:59:59Z, inclusive
	secs := x.GetSeconds()
	nanos := x.GetNanos()
	switch {
	case x == nil:
		return invalidNil
	case secs < minTimestamp:
		return invalidUnderflow
	case secs > maxTimestamp:
		return invalidOverflow
	case nanos < 0 || nanos >= 1e9:
		return invalidNanos
	default:
		return 0
	}
}
```

**File:** types/known/timestamppb/timestamp_test.go (L78-79)
```go
		{in: &tspb.Timestamp{Seconds: math.MinInt64, Nanos: 0}, wantTime: time.Unix(math.MinInt64, 0), wantErr: textError("timestamp (seconds:-9223372036854775808) before 0001-01-01")},
		{in: &tspb.Timestamp{Seconds: math.MaxInt64, Nanos: 1e9 - 1}, wantTime: time.Unix(math.MaxInt64, 1e9-1), wantErr: textError("timestamp (seconds:9223372036854775807 nanos:999999999) after 9999-12-31")},
```
