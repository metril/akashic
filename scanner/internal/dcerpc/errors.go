package dcerpc

import "errors"

var (
	ErrTruncated   = errors.New("dcerpc: pdu truncated")
	ErrUnsupported = errors.New("dcerpc: unsupported pdu type")
	ErrBindFailed  = errors.New("dcerpc: bind failed")
)
