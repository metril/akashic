package lsarpc

import "errors"

var (
	ErrTruncated   = errors.New("lsarpc: pdu truncated")
	ErrUnsupported = errors.New("lsarpc: unsupported pdu type")
	ErrBindFailed  = errors.New("lsarpc: bind failed")
)
