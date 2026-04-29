package samr

// BuildSamrCloseHandleRequest encodes SamrCloseHandle (opnum 1).
// Body is just the 20-byte handle.
func BuildSamrCloseHandleRequest(callID uint32, h Handle) []byte {
	return wrapRequest(callID, OpnumSamrCloseHandle, h[:])
}
