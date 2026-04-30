package nfsprobe

// RPCSEC_GSS — RFC 2203. The wire shape is:
//
//   * Auth flavor for the cred = RPCSEC_GSS = 6.
//   * Cred body is a 5-field XDR struct:
//       version: u32   (always 1 / RPCSEC_GSS_VERS_1)
//       gss_proc: u32  (DATA / INIT / CONTINUE_INIT / DESTROY)
//       seq_num: u32   (initiator-managed, monotonic, wraps at 2^32-1)
//       service: u32   (NONE / INTEGRITY / PRIVACY)
//       handle: opaque (server-assigned context handle; empty on INIT)
//
//   * Verifier on the FIRST call (gss_proc=INIT/CONTINUE_INIT) is
//     AUTH_NONE. Verifier on subsequent DATA calls is the GSS-API
//     output of GSS_GetMIC over the full call header bytes from xid
//     through the cred. (RFC 2203 §5.3.1)
//
//   * Reply verifier on INIT carries the AP_REP gss_token (parsed in
//     gss_context.go); on DATA it's a MIC over the call's seq_num
//     bytes. We don't presently verify the DATA reply MIC — the call
//     succeeding is itself proof enough for the probe. krb5i/krb5p
//     phases will need to.

const (
	authRPCSecGSS uint32 = 6

	rpcGSSVersion1 uint32 = 1

	// gss_proc values.
	rpcGSSProcData         uint32 = 0
	rpcGSSProcInit         uint32 = 1
	rpcGSSProcContinueInit uint32 = 2
	rpcGSSProcDestroy      uint32 = 3

	// rpc_gss_service_t values.
	rpcGSSSvcNone      uint32 = 1 // krb5  — auth-only
	rpcGSSSvcIntegrity uint32 = 2 // krb5i — args/reply MIC-protected
	rpcGSSSvcPrivacy   uint32 = 3 // krb5p — args/reply Wrap-protected
)

// gssCred is the in-memory shape of the rpc_gss_cred_t struct.
type gssCred struct {
	version uint32
	gssProc uint32
	seqNum  uint32
	service uint32
	handle  []byte
}

func (c gssCred) marshal() []byte {
	w := newXDRWriter()
	w.writeUint32(c.version)
	w.writeUint32(c.gssProc)
	w.writeUint32(c.seqNum)
	w.writeUint32(c.service)
	w.writeOpaque(c.handle)
	return w.bytes()
}

// gssInitReply is the parsed payload of an RPCSEC_GSS_INIT reply
// (rpc_gss_init_res, RFC 2203 §5.3.3.4):
//
//   handle:     opaque        — server-allocated context handle
//   gss_major:  u32           — 0 = COMPLETE, 1 = CONTINUE_NEEDED
//   gss_minor:  u32           — mech-specific minor status (0 on success)
//   seq_window: u32           — server's sliding-window size for replays
//   gss_token:  opaque        — server's GSS reply token (the AP_REP)
type gssInitReply struct {
	handle    []byte
	gssMajor  uint32
	gssMinor  uint32
	seqWindow uint32
	gssToken  []byte
}

const (
	gssMajorComplete        uint32 = 0
	gssMajorContinueNeeded  uint32 = 1
)

func parseGSSInitReply(body []byte) (*gssInitReply, error) {
	r := newXDRReader(body)
	handle, err := r.readOpaque()
	if err != nil {
		return nil, errorWith("init reply handle", err)
	}
	major, err := r.readUint32()
	if err != nil {
		return nil, errorWith("init reply gss_major", err)
	}
	minor, err := r.readUint32()
	if err != nil {
		return nil, errorWith("init reply gss_minor", err)
	}
	seqWin, err := r.readUint32()
	if err != nil {
		return nil, errorWith("init reply seq_window", err)
	}
	tok, err := r.readOpaque()
	if err != nil {
		return nil, errorWith("init reply gss_token", err)
	}
	return &gssInitReply{
		handle:    handle,
		gssMajor:  major,
		gssMinor:  minor,
		seqWindow: seqWin,
		gssToken:  tok,
	}, nil
}

// errorWith wraps an inner error with a human-readable field name.
// Tiny helper; keeps parseGSSInitReply readable.
func errorWith(field string, err error) error {
	return &gssParseError{field: field, inner: err}
}

type gssParseError struct {
	field string
	inner error
}

func (e *gssParseError) Error() string { return "rpcsec_gss: " + e.field + ": " + e.inner.Error() }
func (e *gssParseError) Unwrap() error { return e.inner }
