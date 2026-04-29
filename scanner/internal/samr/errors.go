package samr

import (
	"errors"
	"fmt"
)

var (
	ErrTruncated   = errors.New("samr: pdu truncated")
	ErrUnsupported = errors.New("samr: unsupported pdu type")
	ErrBindFailed  = errors.New("samr: bind failed")
	ErrInvalidSID  = errors.New("samr: invalid SID")
)

// Well-known NTSTATUS codes we care about for SAMR error mapping.
const (
	StatusNoSuchUser uint32 = 0xC0000063
	StatusNoneMapped uint32 = 0xC0000073
)

// StatusError carries the NTSTATUS the server returned for a SAMR opcode
// that completed at the protocol level but reported a non-zero status.
// Callers can errors.As(err, &samr.StatusError{}) and inspect Status to
// disambiguate (e.g., "no such user" vs "access denied").
type StatusError struct {
	Op     string
	Status uint32
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("samr: %s failed: ntstatus=0x%x", e.Op, e.Status)
}

// IsNotFound reports whether the wrapped status indicates the named
// principal does not exist in the domain.
func (e *StatusError) IsNotFound() bool {
	return e.Status == StatusNoSuchUser || e.Status == StatusNoneMapped
}
