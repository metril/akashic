package nfsprobe

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"time"
)

// ONC-RPC v2 over TCP (RFC 5531). Each call/reply is wrapped in one or
// more "fragments" framed by a 4-byte record marker: the high bit
// signals "last fragment", the remaining 31 bits are the length. We
// always send a single fragment with the high bit set.
//
// For brevity we only implement the call/reply paths the probe uses —
// AUTH_NONE / AUTH_SYS, MSG_ACCEPTED, accept_stat=SUCCESS. Anything
// else surfaces as a typed error so the cascade can act on it.

// RPC message types.
const (
	rpcCall  uint32 = 0
	rpcReply uint32 = 1
)

// Reply status (mtype=REPLY).
const (
	rpcMsgAccepted uint32 = 0
	rpcMsgDenied   uint32 = 1
)

// Accept status — see RFC 5531 §9.
const (
	rpcSuccess      uint32 = 0
	rpcProgUnavail  uint32 = 1
	rpcProgMismatch uint32 = 2
	rpcProcUnavail  uint32 = 3
	rpcGarbageArgs  uint32 = 4
	rpcSystemErr    uint32 = 5
)

// Reject status — when reply_stat=MSG_DENIED.
const (
	rpcRpcMismatch uint32 = 0
	rpcAuthError   uint32 = 1
)

// Auth flavors — only the two we use.
const (
	authNone uint32 = 0
	authSys  uint32 = 1
)

// rpcCallHeader is the on-wire RPC v2 call message header up to (but
// not including) procedure-specific args.
type rpcCallHeader struct {
	xid     uint32
	prog    uint32
	vers    uint32
	proc    uint32
	cred    authBody
	verifier authBody
}

// authBody is `opaque_auth` per RFC 5531 §8: a flavor + variable-
// length opaque body.
type authBody struct {
	flavor uint32
	body   []byte
}

// authBuilder is the credential builder interface — AUTH_NONE and
// AUTH_SYS each implement it. Kept open so RPCSEC_GSS (Phase 3c) can
// slot in later without touching the call sites.
type authBuilder interface {
	cred() authBody
	verifier() authBody
}

// newXID returns a non-zero random XID. The protocol allows zero but
// we avoid it so a forgotten zeroed field is recognizable as a bug.
func newXID() uint32 {
	var b [4]byte
	for {
		_, _ = rand.Read(b[:])
		if v := binary.BigEndian.Uint32(b[:]); v != 0 {
			return v
		}
	}
}

// rpcCallTCP marshals a call, dials TCP, sends it as a single fragment,
// reads the (possibly multi-fragment) reply, and returns the procedure-
// specific reply bytes (the part after the accept_stat=SUCCESS verifier).
//
// The caller decodes the procedure-specific portion against the proc's
// reply struct.
//
// On error this returns a typed `*rpcError` so the cascade can map
// AUTH_ERROR / PROG_UNAVAIL / etc. to user-facing reasons.
func rpcCallTCP(
	ctx context.Context,
	addr string,
	prog, vers, proc uint32,
	auth authBuilder,
	args []byte,
	timeout time.Duration,
) ([]byte, error) {
	d := net.Dialer{Timeout: timeout}
	conn, err := d.DialContext(ctx, "tcp", addr)
	if err != nil {
		return nil, &rpcError{kind: rpcErrConnect, msg: err.Error()}
	}
	defer conn.Close()
	deadline := time.Now().Add(timeout)
	_ = conn.SetDeadline(deadline)
	return doRPCExchange(conn, prog, vers, proc, auth, args)
}

// doRPCExchange is split out so tests can drive it with an
// `io.ReadWriter` (a memory pipe) — no real network needed.
func doRPCExchange(
	rw io.ReadWriter,
	prog, vers, proc uint32,
	auth authBuilder,
	args []byte,
) ([]byte, error) {
	xid := newXID()

	// Build the call body. For RPCSEC_GSS the verifier is computed over
	// the call header bytes from xid through cred — so signingAuthBuilder
	// hands us BOTH the cred and a closure that signs the header. The
	// cred's seq_num and the MIC token's seqnum are captured together,
	// so a builder shared across goroutines never desyncs the two.
	// AUTH_NONE/AUTH_SYS take the simpler cred()/verifier() path.
	w := newXDRWriter()
	w.writeUint32(xid)
	w.writeUint32(rpcCall)
	w.writeUint32(2) // RPC version 2
	w.writeUint32(prog)
	w.writeUint32(vers)
	w.writeUint32(proc)
	var verifier authBody
	if sb, ok := auth.(signingAuthBuilder); ok {
		cred, sign := sb.credAndSign()
		writeAuthBody(w, cred)
		headerBytes := append([]byte(nil), w.bytes()...) // snapshot before verifier
		v, err := sign(headerBytes)
		if err != nil {
			return nil, &rpcError{kind: rpcErrSend, msg: err.Error()}
		}
		verifier = v
	} else {
		writeAuthBody(w, auth.cred())
		verifier = auth.verifier()
	}
	writeAuthBody(w, verifier)
	w.buf = append(w.buf, args...)
	body := w.bytes()

	// Single fragment with the high bit set.
	mark := uint32(len(body)) | (1 << 31)
	var hdr [4]byte
	binary.BigEndian.PutUint32(hdr[:], mark)
	if _, err := rw.Write(hdr[:]); err != nil {
		return nil, &rpcError{kind: rpcErrSend, msg: err.Error()}
	}
	if _, err := rw.Write(body); err != nil {
		return nil, &rpcError{kind: rpcErrSend, msg: err.Error()}
	}

	// Read fragments until the last-fragment bit is set.
	replyBody, err := readReplyFragments(rw)
	if err != nil {
		return nil, err
	}

	// Decode the reply header and validate xid match.
	r := newXDRReader(replyBody)
	respXID, err := r.readUint32()
	if err != nil {
		return nil, &rpcError{kind: rpcErrDecode, msg: "reply xid: " + err.Error()}
	}
	if respXID != xid {
		return nil, &rpcError{
			kind: rpcErrDecode,
			msg:  fmt.Sprintf("xid mismatch: sent %d, got %d", xid, respXID),
		}
	}
	mtype, err := r.readUint32()
	if err != nil || mtype != rpcReply {
		return nil, &rpcError{kind: rpcErrDecode, msg: "expected REPLY"}
	}
	replyStat, err := r.readUint32()
	if err != nil {
		return nil, &rpcError{kind: rpcErrDecode, msg: "reply_stat: " + err.Error()}
	}
	switch replyStat {
	case rpcMsgAccepted:
		// verifier (we ignore it for AUTH_NONE/AUTH_SYS).
		if _, err := readAuthBody(r); err != nil {
			return nil, &rpcError{kind: rpcErrDecode, msg: "verifier: " + err.Error()}
		}
		acceptStat, err := r.readUint32()
		if err != nil {
			return nil, &rpcError{kind: rpcErrDecode, msg: "accept_stat: " + err.Error()}
		}
		if acceptStat != rpcSuccess {
			return nil, &rpcError{
				kind: rpcErrAccept,
				msg:  acceptStatusName(acceptStat),
				code: acceptStat,
			}
		}
		// Procedure-specific reply payload starts at the current cursor.
		return replyBody[r.pos:], nil
	case rpcMsgDenied:
		rejectStat, err := r.readUint32()
		if err != nil {
			return nil, &rpcError{kind: rpcErrDecode, msg: "reject_stat: " + err.Error()}
		}
		if rejectStat == rpcAuthError {
			authStat, _ := r.readUint32()
			return nil, &rpcError{
				kind: rpcErrAuth,
				msg:  fmt.Sprintf("auth_stat=%d", authStat),
				code: authStat,
			}
		}
		return nil, &rpcError{kind: rpcErrReject, msg: fmt.Sprintf("reject_stat=%d", rejectStat)}
	default:
		return nil, &rpcError{kind: rpcErrDecode, msg: fmt.Sprintf("unknown reply_stat=%d", replyStat)}
	}
}

// writeAuthBody appends an `opaque_auth` to w.
func writeAuthBody(w *xdrWriter, a authBody) {
	w.writeUint32(a.flavor)
	w.writeOpaque(a.body)
}

func readAuthBody(r *xdrReader) (authBody, error) {
	flavor, err := r.readUint32()
	if err != nil {
		return authBody{}, err
	}
	body, err := r.readOpaque()
	if err != nil {
		return authBody{}, err
	}
	return authBody{flavor: flavor, body: body}, nil
}

// readReplyFragments collects fragments until one carries the
// last-fragment bit, then returns the concatenated body. We cap the
// total reply size at 16 MB to avoid being a memory footgun under a
// hostile or buggy server.
const maxReplyBytes = 16 * 1024 * 1024

func readReplyFragments(rw io.Reader) ([]byte, error) {
	var body []byte
	for {
		hdr, err := readFull(rw, 4)
		if err != nil {
			return nil, &rpcError{kind: rpcErrRecv, msg: err.Error()}
		}
		mark := binary.BigEndian.Uint32(hdr)
		last := (mark & (1 << 31)) != 0
		fragLen := int(mark &^ (1 << 31))
		if fragLen <= 0 {
			return nil, &rpcError{kind: rpcErrRecv, msg: "zero-length fragment"}
		}
		if len(body)+fragLen > maxReplyBytes {
			return nil, &rpcError{kind: rpcErrRecv, msg: "reply exceeds 16 MB cap"}
		}
		frag, err := readFull(rw, fragLen)
		if err != nil {
			return nil, &rpcError{kind: rpcErrRecv, msg: err.Error()}
		}
		body = append(body, frag...)
		if last {
			return body, nil
		}
	}
}

// rpcErrKind tags rpcError so the cascade can map mechanical failures
// (connect refused, timeout) separately from protocol failures
// (PROG_UNAVAIL, AUTH_ERROR).
type rpcErrKind int

const (
	rpcErrConnect rpcErrKind = iota + 1
	rpcErrSend
	rpcErrRecv
	rpcErrDecode
	rpcErrAccept // accept_stat != SUCCESS
	rpcErrReject // reply_stat = MSG_DENIED, non-auth reject
	rpcErrAuth   // reply_stat = MSG_DENIED, reject_stat = AUTH_ERROR
)

type rpcError struct {
	kind rpcErrKind
	msg  string
	code uint32 // accept_stat or auth_stat depending on kind
}

func (e *rpcError) Error() string {
	switch e.kind {
	case rpcErrConnect:
		return "rpc connect: " + e.msg
	case rpcErrSend:
		return "rpc send: " + e.msg
	case rpcErrRecv:
		return "rpc recv: " + e.msg
	case rpcErrDecode:
		return "rpc decode: " + e.msg
	case rpcErrAccept:
		return "rpc accepted-but-failed: " + e.msg
	case rpcErrReject:
		return "rpc rejected: " + e.msg
	case rpcErrAuth:
		return "rpc auth error: " + e.msg
	}
	return "rpc: " + e.msg
}

func acceptStatusName(s uint32) string {
	switch s {
	case rpcProgUnavail:
		return "PROG_UNAVAIL"
	case rpcProgMismatch:
		return "PROG_MISMATCH"
	case rpcProcUnavail:
		return "PROC_UNAVAIL"
	case rpcGarbageArgs:
		return "GARBAGE_ARGS"
	case rpcSystemErr:
		return "SYSTEM_ERR"
	default:
		return fmt.Sprintf("accept_stat=%d", s)
	}
}
