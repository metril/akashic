package samr

import "errors"

var (
	ErrTruncated              = errors.New("samr: pdu truncated")
	ErrUnsupported            = errors.New("samr: unsupported pdu type")
	ErrBindFailed             = errors.New("samr: bind failed")
	ErrSamrConnectFailed      = errors.New("samr: SamrConnect5 failed")
	ErrSamrOpenDomainFailed   = errors.New("samr: SamrOpenDomain failed")
	ErrSamrOpenUserFailed     = errors.New("samr: SamrOpenUser failed")
	ErrSamrGetGroupsFailed    = errors.New("samr: SamrGetGroupsForUser failed")
	ErrSamrLookupIdsFailed    = errors.New("samr: SamrLookupIdsInDomain failed")
	ErrInvalidSID             = errors.New("samr: invalid SID")
)
