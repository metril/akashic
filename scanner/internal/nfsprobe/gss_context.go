package nfsprobe

import (
	"context"
	"errors"
	"fmt"
	"sync/atomic"
	"time"

	"github.com/jcmturner/gokrb5/v8/gssapi"
	"github.com/jcmturner/gokrb5/v8/iana/keyusage"
	"github.com/jcmturner/gokrb5/v8/types"
)

// GSS context establishment for RPCSEC_GSS over RFC 4121 (Kerberos V5).
//
// The flow per RFC 2203 §5.2.2:
//
//   1. Client → Server:  RPC call with cred.gss_proc=INIT, cred.handle=<empty>,
//      verifier=AUTH_NONE, args = { gss_token = <APREQ token> }.
//      The procedure being called is irrelevant — servers accept the
//      INIT against any program/proc; convention is "the proc you'll
//      ultimately use". We send it against NFSv4 proc=NULL (proc 0) so
//      no real work is done if the server short-circuits.
//
//   2. Server → Client:  RPC reply with verifier carrying the GSS reply
//      token, body = rpc_gss_init_res { handle, major, minor, seq_window,
//      gss_token }. major=0 means context established in one round trip;
//      major=1 (CONTINUE_NEEDED) means another INIT/CONTINUE_INIT is
//      needed. For raw Kerberos this is always one round trip.
//
//   3. Subsequent DATA calls reuse the established handle. The verifier
//      on each call is a MIC over the call header bytes (xid through
//      cred); the server validates with the session key from the AP_REQ.
//
// Only `service = rpc_gss_svc_none` (krb5 auth-only) is implemented in
// this phase. krb5i/krb5p require args-side MIC/Wrap framing in
// addition to the verifier and are deferred.

// gssAuthBuilder is the credential builder for established GSS contexts
// on DATA calls. Implements both authBuilder and signingAuthBuilder so
// the existing oncrpc dispatch path can use it without special casing.
type gssAuthBuilder struct {
	handle     []byte
	sessionKey types.EncryptionKey
	service    uint32 // rpcGSSSvcNone for krb5 auth-only

	// seqWindow is the server's sliding-window size from the INIT reply.
	// Per RFC 2203 §5.3.3.1, the client's seq_num must be ≤ seq_window
	// or the server returns AUTH_ERROR. For a probe doing a handful of
	// calls, this is comfortably below typical 32–256-element windows.
	seqWindow uint32

	seqCounter atomic.Uint32
}

// nextSeq atomically returns the next seq_num for a DATA call. Per RFC
// 2203 §5.3.3.1, seq_num is monotonically increasing within a context;
// wraps at 2^32-1 (we don't wrap here — for a probe, 4 billion calls is
// not in scope).
func (g *gssAuthBuilder) nextSeq() uint32 { return g.seqCounter.Add(1) }

func (g *gssAuthBuilder) cred() authBody {
	// cred() is no longer the right entry-point for GSS auth — the seq_num
	// must flow into the MIC verifier of the same call. doRPCExchange
	// branches on signingAuthBuilder and uses credAndSign() instead. This
	// path remains for safety (interface satisfaction) but generates a
	// DATA cred with a fresh seq_num that no verifier will match,
	// effectively guaranteeing server-side rejection. A panic would be
	// louder, but this is library code; the calling convention is
	// enforced by the type system at the dispatch site.
	c := gssCred{
		version: rpcGSSVersion1,
		gssProc: rpcGSSProcData,
		seqNum:  g.nextSeq(),
		service: g.service,
		handle:  g.handle,
	}
	return authBody{flavor: authRPCSecGSS, body: c.marshal()}
}

func (g *gssAuthBuilder) verifier() authBody {
	// Same situation as cred(): the signingAuthBuilder path bypasses
	// this. AUTH_NONE is the safe default — a server validating against
	// the GSS verifier will reject it cleanly rather than us panicking.
	return authBody{flavor: authNone, body: nil}
}

// credAndSign returns both the cred for this call and a closure that
// signs the call header bytes with the seq_num used in this very cred.
// Callers do:
//
//   cred, sign := builder.credAndSign()
//   writeAuthBody(w, cred)
//   headerBytes := snapshot(w)
//   verifier, _ := sign(headerBytes)
//   writeAuthBody(w, verifier)
//
// Atomically incrementing the counter inside this method and capturing
// the value in the closure means the cred and the MIC always agree on
// seq_num even if the builder is shared across goroutines.
func (g *gssAuthBuilder) credAndSign() (authBody, func([]byte) (authBody, error)) {
	seq := g.nextSeq()
	if g.seqWindow > 0 && seq > g.seqWindow {
		// In practice we never approach the window for a probe, but if
		// somebody chains many calls the server will reject — give the
		// caller a clear error rather than silently wrapping.
		return authBody{flavor: authRPCSecGSS}, func([]byte) (authBody, error) {
			return authBody{}, fmt.Errorf("rpcsec_gss: seq_num %d exceeds seq_window %d", seq, g.seqWindow)
		}
	}
	c := gssCred{
		version: rpcGSSVersion1,
		gssProc: rpcGSSProcData,
		seqNum:  seq,
		service: g.service,
		handle:  g.handle,
	}
	body := c.marshal()
	sessionKey := g.sessionKey
	sign := func(headerBytes []byte) (authBody, error) {
		// MIC token's SndSeqNum: per RFC 4121 §4.2.6.1, it's the GSS
		// per-token seqnum. Aligning it with the RPCSEC_GSS seq_num
		// matches what the Linux kernel RPC client does for sec=krb5
		// (net/sunrpc/auth_gss/) and what tnfs and other userspace
		// implementations do in practice. Independent counter tracking
		// would be more correct but isn't required for interop.
		mt := &gssapi.MICToken{
			Flags:     0x00, // initiator, not sealed, no acceptor subkey
			SndSeqNum: uint64(seq),
			Payload:   headerBytes,
		}
		if err := mt.SetChecksum(sessionKey, keyusage.GSSAPI_INITIATOR_SIGN); err != nil {
			return authBody{}, fmt.Errorf("rpcsec_gss: verifier MIC: %w", err)
		}
		tok, err := mt.Marshal()
		if err != nil {
			return authBody{}, fmt.Errorf("rpcsec_gss: marshal MIC: %w", err)
		}
		return authBody{flavor: authRPCSecGSS, body: tok}, nil
	}
	return authBody{flavor: authRPCSecGSS, body: body}, sign
}

// signingAuthBuilder is implemented by credential builders whose
// verifier depends on the call's serialized header (xid+mtype+...+cred).
// oncrpc.doRPCExchange branches on this: AUTH_NONE/AUTH_SYS take the
// simple path; signingAuthBuilder uses credAndSign() so the cred and
// the MIC verifier always agree on seq_num — even if multiple
// goroutines share a builder.
type signingAuthBuilder interface {
	authBuilder
	credAndSign() (authBody, func(headerBytes []byte) (authBody, error))
}

// initAuthBuilder is used ONLY for the RPCSEC_GSS_INIT call. cred has
// gss_proc=INIT, empty handle, seq_num=0; verifier is AUTH_NONE.
type initAuthBuilder struct{}

func (initAuthBuilder) cred() authBody {
	c := gssCred{
		version: rpcGSSVersion1,
		gssProc: rpcGSSProcInit,
		seqNum:  0,
		service: rpcGSSSvcNone,
		handle:  nil,
	}
	return authBody{flavor: authRPCSecGSS, body: c.marshal()}
}

func (initAuthBuilder) verifier() authBody {
	return authBody{flavor: authNone, body: nil}
}

// establishGSSContext runs the RPCSEC_GSS_INIT exchange against `addr`
// using the krb5 client's AP_REQ token. On success returns a populated
// gssAuthBuilder ready for DATA calls.
//
// The "exchange procedure" — i.e., the program/version/proc the INIT
// rides on — is the NULLPROC of the program the caller intends to use.
// We hardcode NFSv4 NULL (prog=100003, vers=4, proc=0) because the
// probe only uses krb5 against the NFS daemon (not mountd).
func establishGSSContext(
	ctx context.Context,
	addr string,
	kc *krb5Client,
	timeout time.Duration,
) (*gssAuthBuilder, error) {
	// Acquire the service ticket up front; surfaces KDC errors with
	// a clear "before we even talk to NFS" attribution.
	if err := kc.acquireServiceTicket(); err != nil {
		return nil, err
	}
	// Build the AP_REQ token. We don't request mutual auth (no
	// ContextFlagMutual flag) — RPCSEC_GSS doesn't require it and
	// gokrb5 has no AP_REP verification anyway. The integrity flag is
	// implicit for any GSS context that can MIC.
	apReq, err := kc.buildAPReqToken(nil)
	if err != nil {
		return nil, err
	}

	// Encode the gss_token as the INIT call's args (a length-prefixed
	// opaque per the rpc_gss_init_arg shape).
	w := newXDRWriter()
	w.writeOpaque(apReq)

	body, err := rpcCallTCP(
		ctx, addr,
		progNFS, versNFS4, 0, // NULLPROC = 0
		initAuthBuilder{}, w.bytes(), timeout,
	)
	if err != nil {
		return nil, fmt.Errorf("rpcsec_gss: INIT call: %w", err)
	}

	reply, err := parseGSSInitReply(body)
	if err != nil {
		return nil, err
	}
	switch reply.gssMajor {
	case gssMajorComplete:
		// happy path
	case gssMajorContinueNeeded:
		// Raw Kerberos (no SPNEGO) completes in one round trip; if a
		// server claims CONTINUE_NEEDED here, something on the server
		// side is unusual — we don't have a CONTINUE_INIT loop.
		return nil, fmt.Errorf("rpcsec_gss: server requested CONTINUE_INIT (gss_minor=%d) — not supported in this build", reply.gssMinor)
	default:
		return nil, fmt.Errorf("rpcsec_gss: INIT failed (gss_major=%d, gss_minor=%d)", reply.gssMajor, reply.gssMinor)
	}
	if len(reply.handle) == 0 {
		return nil, errors.New("rpcsec_gss: INIT reply missing context handle")
	}

	g := &gssAuthBuilder{
		handle:     reply.handle,
		sessionKey: kc.sessionKey,
		service:    rpcGSSSvcNone, // krb5 auth-only
		seqWindow:  reply.seqWindow,
	}
	// We intentionally DON'T verify reply.gssToken (the AP_REP) — gokrb5
	// v8 has no AP_REP verification path, and for auth-only mutual auth
	// is not required. If a future caller needs it (krb5p sometimes
	// derives subkeys from AP_REP), this is the place to plumb it in.
	_ = reply.gssToken
	return g, nil
}
