package samr

import "github.com/akashic-project/akashic/scanner/internal/dcerpc"

// BuildSamrCloseHandleRequest encodes SamrCloseHandle (opnum 1).
// Body is just the 20-byte handle.
func BuildSamrCloseHandleRequest(callID uint32, h Handle) []byte {
	return dcerpc.WrapRequest(callID, OpnumSamrCloseHandle, h[:])
}
