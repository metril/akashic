package nfsprobe

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// NFSv4 (RFC 7530). Compared to NFSv3, all client→server interaction
// flows through a single COMPOUND procedure that batches multiple
// operations in one RPC. Useful for us because we can do
// PUTROOTFH + LOOKUP(seg1) + LOOKUP(seg2) + ... + GETFH in one round
// trip and observe whether each LOOKUP succeeds.
//
// AUTH_SYS is universally supported; many servers also accept
// AUTH_NONE for non-data ops like LOOKUP, but we play it safe and
// always use AUTH_SYS on the v4 path.

const (
	progNFS = 100003
	versNFS4 = 4
	portNFS = 2049

	procNFSv4Compound uint32 = 1
)

// COMPOUND op codes — RFC 7530 §16. Only the three we use.
const (
	op4PutRootFH uint32 = 24
	op4Lookup    uint32 = 15
	op4GetFH     uint32 = 10
)

// NFSv4 status codes — only the few we actually map.
const (
	nfs4Ok          uint32 = 0
	nfs4ErrPerm     uint32 = 1
	nfs4ErrNoEnt    uint32 = 2
	nfs4ErrIO       uint32 = 5
	nfs4ErrAccess   uint32 = 13
	nfs4ErrNotDir   uint32 = 20
	nfs4ErrInval    uint32 = 22
	nfs4ErrServerFault uint32 = 10006
	nfs4ErrBadHandle uint32 = 10001
)

func nfs4StatusName(s uint32) string {
	switch s {
	case nfs4Ok:
		return "NFS4_OK"
	case nfs4ErrPerm:
		return "NFS4ERR_PERM"
	case nfs4ErrNoEnt:
		return "NFS4ERR_NOENT"
	case nfs4ErrIO:
		return "NFS4ERR_IO"
	case nfs4ErrAccess:
		return "NFS4ERR_ACCESS"
	case nfs4ErrNotDir:
		return "NFS4ERR_NOTDIR"
	case nfs4ErrInval:
		return "NFS4ERR_INVAL"
	case nfs4ErrBadHandle:
		return "NFS4ERR_BADHANDLE"
	default:
		return fmt.Sprintf("nfs4stat=%d", s)
	}
}

// nfs4LookupPath issues a COMPOUND that:
//   PUTROOTFH; LOOKUP(seg1); LOOKUP(seg2); ...; GETFH
//
// Returns the resolved filehandle on success. On a LOOKUP failure
// returns a *nfs4Error with `code` and `failedSegment` so the cascade
// can attribute the failure ("export path not found at /srv/data/foo").
func nfs4LookupPath(
	ctx context.Context,
	host string,
	port uint32,
	auth authBuilder,
	exportPath string,
	timeout time.Duration,
) ([]byte, error) {
	segments := splitPath(exportPath)

	w := newXDRWriter()
	// COMPOUND header: tag<>, minorversion, num_args.
	w.writeString("akashic-probe") // tag — informational
	w.writeUint32(0)               // minorversion = 0 (i.e., NFSv4.0)
	// One PUTROOTFH + one LOOKUP per non-empty segment + one GETFH.
	numOps := uint32(2 + len(segments))
	w.writeUint32(numOps)

	w.writeUint32(op4PutRootFH)
	for _, seg := range segments {
		w.writeUint32(op4Lookup)
		w.writeString(seg)
	}
	w.writeUint32(op4GetFH)

	addr := fmt.Sprintf("%s:%d", host, port)
	body, err := rpcCallTCP(
		ctx, addr,
		progNFS, versNFS4, procNFSv4Compound,
		auth, w.bytes(), timeout,
	)
	if err != nil {
		return nil, err
	}

	// Reply shape: status, tag<>, num_results, then per-op results.
	r := newXDRReader(body)
	overallStatus, err := r.readUint32()
	if err != nil {
		return nil, fmt.Errorf("compound status: %w", err)
	}
	if _, err := r.readString(); err != nil {
		return nil, fmt.Errorf("compound tag: %w", err)
	}
	numRes, err := r.readUint32()
	if err != nil {
		return nil, fmt.Errorf("compound num_results: %w", err)
	}

	// Walk results in order. PUTROOTFH and LOOKUP results are just an op
	// code + status; GETFH on success has the filehandle as its body.
	var fh []byte
	for i := uint32(0); i < numRes; i++ {
		op, err := r.readUint32()
		if err != nil {
			return nil, fmt.Errorf("op[%d] code: %w", i, err)
		}
		status, err := r.readUint32()
		if err != nil {
			return nil, fmt.Errorf("op[%d] status: %w", i, err)
		}
		if status != nfs4Ok {
			return nil, &nfs4Error{
				code:          status,
				op:            op,
				failedSegment: segmentForOp(int(i), segments),
				msg:           nfs4StatusName(status),
			}
		}
		switch op {
		case op4GetFH:
			fhBytes, err := r.readOpaque()
			if err != nil {
				return nil, fmt.Errorf("getfh body: %w", err)
			}
			fh = fhBytes
		case op4PutRootFH, op4Lookup:
			// no body for OK responses
		default:
			// unexpected op — bail rather than guess
			return nil, fmt.Errorf("unexpected op %d in compound reply", op)
		}
	}

	if overallStatus != nfs4Ok {
		// Shouldn't happen if all op statuses were OK, but defend.
		return nil, &nfs4Error{code: overallStatus, msg: nfs4StatusName(overallStatus)}
	}
	if fh == nil {
		return nil, fmt.Errorf("compound completed without GETFH")
	}
	return fh, nil
}

// splitPath returns non-empty path segments. NFSv4 LOOKUP processes
// one segment at a time; trailing/leading/double slashes are trimmed.
func splitPath(p string) []string {
	parts := strings.Split(strings.Trim(p, "/"), "/")
	out := make([]string, 0, len(parts))
	for _, s := range parts {
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}

// segmentForOp maps the result-index back to which path segment
// failed. Op 0 = PUTROOTFH (no segment), ops 1..N = LOOKUPs of
// segments[0..N-1], op N+1 = GETFH.
func segmentForOp(opIndex int, segments []string) string {
	if opIndex == 0 {
		return "(root)"
	}
	if opIndex-1 < len(segments) {
		return segments[opIndex-1]
	}
	return "(getfh)"
}

type nfs4Error struct {
	code          uint32
	op            uint32
	failedSegment string
	msg           string
}

func (e *nfs4Error) Error() string {
	if e.failedSegment != "" {
		return fmt.Sprintf("%s at %q", e.msg, e.failedSegment)
	}
	return e.msg
}
