No vulnerability found for this question.

The reported issue is about Rust's checked/panicking `Duration + Instant` arithmetic overflowing when a network peer supplies an oversized backoff value. In protobuf-go, the analogous conversions live in `durationpb.Duration.AsDuration()` and `timestamppb.Timestamp.AsTime()`, e.g. [1](#0-0) . This function explicitly implements saturating arithmetic and returns `math.MinInt64`/`math.MaxInt64` on overflow instead of panicking, and a separate `CheckValid`/`check()` path is provided for callers who want strict range validation [2](#0-1) . Likewise `Timestamp.AsTime()` simply calls `time.Unix(...)` which cannot panic on any int64 input [3](#0-2) .

Moreover, Go's integer arithmetic (unlike Rust's debug-mode checked arithmetic) does not panic on overflow — it wraps per two's-complement semantics, so there is no equivalent "unchecked arithmetic causing a runtime panic" primitive being exploited here. Tests confirm large/overflowing seconds and nanos values are handled without panics, only returning validation errors when `CheckValid` is called [4](#0-3) [5](#0-4) .

There is no reachable code path in protobuf-go's default binary or ProtoJSON decode paths where attacker-controlled duration/timestamp field values lead to an unchecked arithmetic panic; overflow is explicitly saturated or surfaced as a normal error, not a crash.

### Citations

**File:** types/known/durationpb/duration.pb.go (L169-188)
```go
// AsDuration converts x to a time.Duration,
// returning the closest duration value in the event of overflow.
func (x *Duration) AsDuration() time.Duration {
	secs := x.GetSeconds()
	nanos := x.GetNanos()
	d := time.Duration(secs) * time.Second
	overflow := d/time.Second != time.Duration(secs)
	d += time.Duration(nanos) * time.Nanosecond
	overflow = overflow || (secs < 0 && nanos < 0 && d > 0)
	overflow = overflow || (secs > 0 && nanos > 0 && d < 0)
	if overflow {
		switch {
		case secs < 0:
			return time.Duration(math.MinInt64)
		case secs > 0:
			return time.Duration(math.MaxInt64)
		}
	}
	return d
}
```

**File:** types/known/durationpb/duration.pb.go (L196-244)
```go
// CheckValid returns an error if the duration is invalid.
// In particular, it checks whether the value is within the range of
// -10000 years to +10000 years inclusive.
// An error is reported for a nil Duration.
func (x *Duration) CheckValid() error {
	switch x.check() {
	case invalidNil:
		return protoimpl.X.NewError("invalid nil Duration")
	case invalidUnderflow:
		return protoimpl.X.NewError("duration (%v) exceeds -10000 years", x)
	case invalidOverflow:
		return protoimpl.X.NewError("duration (%v) exceeds +10000 years", x)
	case invalidNanosRange:
		return protoimpl.X.NewError("duration (%v) has out-of-range nanos", x)
	case invalidNanosSign:
		return protoimpl.X.NewError("duration (%v) has seconds and nanos with different signs", x)
	default:
		return nil
	}
}

const (
	_ = iota
	invalidNil
	invalidUnderflow
	invalidOverflow
	invalidNanosRange
	invalidNanosSign
)

func (x *Duration) check() uint {
	const absDuration = 315576000000 // 10000yr * 365.25day/yr * 24hr/day * 60min/hr * 60sec/min
	secs := x.GetSeconds()
	nanos := x.GetNanos()
	switch {
	case x == nil:
		return invalidNil
	case secs < -absDuration:
		return invalidUnderflow
	case secs > +absDuration:
		return invalidOverflow
	case nanos <= -1e9 || nanos >= +1e9:
		return invalidNanosRange
	case (secs > 0 && nanos < 0) || (secs < 0 && nanos > 0):
		return invalidNanosSign
	default:
		return 0
	}
}
```

**File:** types/known/timestamppb/timestamp.pb.go (L199-202)
```go
// AsTime converts x to a time.Time.
func (x *Timestamp) AsTime() time.Time {
	return time.Unix(int64(x.GetSeconds()), int64(x.GetNanos())).UTC()
}
```

**File:** types/known/durationpb/duration_test.go (L59-82)
```go
		{in: nil, wantDur: time.Duration(0), wantErr: textError("invalid nil Duration")},
		{in: new(durpb.Duration), wantDur: time.Duration(0)},
		{in: &durpb.Duration{Seconds: -1, Nanos: 0}, wantDur: -time.Second},
		{in: &durpb.Duration{Seconds: +1, Nanos: 0}, wantDur: +time.Second},
		{in: &durpb.Duration{Seconds: 0, Nanos: -1}, wantDur: -time.Nanosecond},
		{in: &durpb.Duration{Seconds: 0, Nanos: +1}, wantDur: +time.Nanosecond},
		{in: &durpb.Duration{Seconds: -100, Nanos: 0}, wantDur: -100 * time.Second},
		{in: &durpb.Duration{Seconds: +100, Nanos: 0}, wantDur: +100 * time.Second},
		{in: &durpb.Duration{Seconds: -100, Nanos: -987}, wantDur: -100*time.Second - 987*time.Nanosecond},
		{in: &durpb.Duration{Seconds: +100, Nanos: +987}, wantDur: +100*time.Second + 987*time.Nanosecond},
		{in: &durpb.Duration{Seconds: minGoSeconds, Nanos: int32(math.MinInt64 - 1e9*minGoSeconds)}, wantDur: time.Duration(math.MinInt64)},
		{in: &durpb.Duration{Seconds: maxGoSeconds, Nanos: int32(math.MaxInt64 - 1e9*maxGoSeconds)}, wantDur: time.Duration(math.MaxInt64)},
		{in: &durpb.Duration{Seconds: minGoSeconds - 1, Nanos: int32(math.MinInt64 - 1e9*minGoSeconds)}, wantDur: time.Duration(math.MinInt64)},
		{in: &durpb.Duration{Seconds: maxGoSeconds + 1, Nanos: int32(math.MaxInt64 - 1e9*maxGoSeconds)}, wantDur: time.Duration(math.MaxInt64)},
		{in: &durpb.Duration{Seconds: minGoSeconds, Nanos: int32(math.MinInt64-1e9*minGoSeconds) - 1}, wantDur: time.Duration(math.MinInt64)},
		{in: &durpb.Duration{Seconds: maxGoSeconds, Nanos: int32(math.MaxInt64-1e9*maxGoSeconds) + 1}, wantDur: time.Duration(math.MaxInt64)},
		{in: &durpb.Duration{Seconds: -123, Nanos: +456}, wantDur: -123*time.Second + 456*time.Nanosecond, wantErr: textError("duration (seconds:-123 nanos:456) has seconds and nanos with different signs")},
		{in: &durpb.Duration{Seconds: +123, Nanos: -456}, wantDur: +123*time.Second - 456*time.Nanosecond, wantErr: textError("duration (seconds:123 nanos:-456) has seconds and nanos with different signs")},
		{in: &durpb.Duration{Seconds: math.MinInt64}, wantDur: time.Duration(math.MinInt64), wantErr: textError("duration (seconds:-9223372036854775808) exceeds -10000 years")},
		{in: &durpb.Duration{Seconds: math.MaxInt64}, wantDur: time.Duration(math.MaxInt64), wantErr: textError("duration (seconds:9223372036854775807) exceeds +10000 years")},
		{in: &durpb.Duration{Seconds: -absSeconds, Nanos: -(1e9 - 1)}, wantDur: time.Duration(math.MinInt64)},
		{in: &durpb.Duration{Seconds: +absSeconds, Nanos: +(1e9 - 1)}, wantDur: time.Duration(math.MaxInt64)},
		{in: &durpb.Duration{Seconds: -absSeconds - 1, Nanos: 0}, wantDur: time.Duration(math.MinInt64), wantErr: textError("duration (seconds:-315576000001) exceeds -10000 years")},
		{in: &durpb.Duration{Seconds: +absSeconds + 1, Nanos: 0}, wantDur: time.Duration(math.MaxInt64), wantErr: textError("duration (seconds:315576000001) exceeds +10000 years")},
```

**File:** types/known/timestamppb/timestamp_test.go (L62-86)
```go
		{in: nil, wantTime: time.Unix(0, 0), wantErr: textError("invalid nil Timestamp")},
		{in: new(tspb.Timestamp), wantTime: time.Unix(0, 0)},
		{in: &tspb.Timestamp{Seconds: -62135596800, Nanos: 0}, wantTime: time.Time{}},
		{in: &tspb.Timestamp{Seconds: -1, Nanos: -1}, wantTime: time.Unix(-1, -1), wantErr: textError("timestamp (seconds:-1 nanos:-1) has out-of-range nanos")},
		{in: &tspb.Timestamp{Seconds: -1, Nanos: 0}, wantTime: time.Unix(-1, 0)},
		{in: &tspb.Timestamp{Seconds: -1, Nanos: +1}, wantTime: time.Unix(-1, +1)},
		{in: &tspb.Timestamp{Seconds: 0, Nanos: -1}, wantTime: time.Unix(0, -1), wantErr: textError("timestamp (nanos:-1) has out-of-range nanos")},
		{in: &tspb.Timestamp{Seconds: 0, Nanos: 0}, wantTime: time.Unix(0, 0)},
		{in: &tspb.Timestamp{Seconds: 0, Nanos: +1}, wantTime: time.Unix(0, +1)},
		{in: &tspb.Timestamp{Seconds: +1, Nanos: -1}, wantTime: time.Unix(+1, -1), wantErr: textError("timestamp (seconds:1 nanos:-1) has out-of-range nanos")},
		{in: &tspb.Timestamp{Seconds: +1, Nanos: 0}, wantTime: time.Unix(+1, 0)},
		{in: &tspb.Timestamp{Seconds: +1, Nanos: +1}, wantTime: time.Unix(+1, +1)},
		{in: &tspb.Timestamp{Seconds: -9876543210, Nanos: -1098765432}, wantTime: time.Unix(-9876543210, -1098765432), wantErr: textError("timestamp (seconds:-9876543210 nanos:-1098765432) has out-of-range nanos")},
		{in: &tspb.Timestamp{Seconds: +9876543210, Nanos: -1098765432}, wantTime: time.Unix(+9876543210, -1098765432), wantErr: textError("timestamp (seconds:9876543210 nanos:-1098765432) has out-of-range nanos")},
		{in: &tspb.Timestamp{Seconds: -9876543210, Nanos: +1098765432}, wantTime: time.Unix(-9876543210, +1098765432), wantErr: textError("timestamp (seconds:-9876543210 nanos:1098765432) has out-of-range nanos")},
		{in: &tspb.Timestamp{Seconds: +9876543210, Nanos: +1098765432}, wantTime: time.Unix(+9876543210, +1098765432), wantErr: textError("timestamp (seconds:9876543210 nanos:1098765432) has out-of-range nanos")},
		{in: &tspb.Timestamp{Seconds: math.MinInt64, Nanos: 0}, wantTime: time.Unix(math.MinInt64, 0), wantErr: textError("timestamp (seconds:-9223372036854775808) before 0001-01-01")},
		{in: &tspb.Timestamp{Seconds: math.MaxInt64, Nanos: 1e9 - 1}, wantTime: time.Unix(math.MaxInt64, 1e9-1), wantErr: textError("timestamp (seconds:9223372036854775807 nanos:999999999) after 9999-12-31")},
		{in: &tspb.Timestamp{Seconds: minTimestamp, Nanos: 0}, wantTime: time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC)},
		{in: &tspb.Timestamp{Seconds: minTimestamp - 1, Nanos: 1e9 - 1}, wantTime: time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC).Add(-time.Nanosecond), wantErr: textError("timestamp (seconds:-62135596801 nanos:999999999) before 0001-01-01")},
		{in: &tspb.Timestamp{Seconds: maxTimestamp, Nanos: 1e9 - 1}, wantTime: time.Date(9999, 12, 31, 23, 59, 59, 1e9-1, time.UTC)},
		{in: &tspb.Timestamp{Seconds: maxTimestamp + 1}, wantTime: time.Date(9999, 12, 31, 23, 59, 59, 1e9-1, time.UTC).Add(+time.Nanosecond), wantErr: textError("timestamp (seconds:253402300800) after 9999-12-31")},
		{in: &tspb.Timestamp{Seconds: -281836800, Nanos: 0}, wantTime: time.Date(1961, 1, 26, 0, 0, 0, 0, time.UTC)},
		{in: &tspb.Timestamp{Seconds: 1296000000, Nanos: 0}, wantTime: time.Date(2011, 1, 26, 0, 0, 0, 0, time.UTC)},
		{in: &tspb.Timestamp{Seconds: 1296012345, Nanos: 940483}, wantTime: time.Date(2011, 1, 26, 3, 25, 45, 940483, time.UTC)},
```
