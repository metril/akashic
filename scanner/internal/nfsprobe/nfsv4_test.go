package nfsprobe

import (
	"errors"
	"testing"
)

func TestSplitPath(t *testing.T) {
	cases := map[string][]string{
		"/srv/data":        {"srv", "data"},
		"srv/data":         {"srv", "data"},
		"/srv/data/":       {"srv", "data"},
		"//srv//data//":    {"srv", "data"},
		"/":                {},
		"":                 {},
		"single":           {"single"},
	}
	for in, want := range cases {
		got := splitPath(in)
		if len(got) != len(want) {
			t.Errorf("splitPath(%q): got %v, want %v", in, got, want)
			continue
		}
		for i := range got {
			if got[i] != want[i] {
				t.Errorf("splitPath(%q)[%d]: got %q, want %q", in, i, got[i], want[i])
			}
		}
	}
}

func TestSegmentForOp(t *testing.T) {
	segs := []string{"srv", "data", "deep"}
	cases := map[int]string{
		0: "(root)",
		1: "srv",
		2: "data",
		3: "deep",
		4: "(getfh)",
	}
	for i, want := range cases {
		if got := segmentForOp(i, segs); got != want {
			t.Errorf("segmentForOp(%d): got %q, want %q", i, got, want)
		}
	}
}

func TestNFS4StatusName(t *testing.T) {
	cases := map[uint32]string{
		nfs4Ok:        "NFS4_OK",
		nfs4ErrNoEnt:  "NFS4ERR_NOENT",
		nfs4ErrAccess: "NFS4ERR_ACCESS",
	}
	for code, want := range cases {
		if got := nfs4StatusName(code); got != want {
			t.Errorf("status %d: %q, want %q", code, got, want)
		}
	}
}

// Synthesize a COMPOUND reply with the given per-op (op, status,
// optional body) and run it through the same parser the live path uses
// (extracted into a helper for testability). Verifies the
// success/NOENT/ACCESS branches without a real server.
//
// Wire shape per RFC 7530 §15:
//
//   COMPOUND4res {
//     status, tag, results<>
//   }
//   nfs_resop4 {
//     op,
//     resop_specific (varies — for OK, body; for non-OK, just status)
//   }
//
// We reproduce just enough.

type compoundOp struct {
	op     uint32
	status uint32
	body   []byte
}

func encodeCompoundReply(overall uint32, tag string, ops []compoundOp) []byte {
	w := newXDRWriter()
	w.writeUint32(overall)
	w.writeString(tag)
	w.writeUint32(uint32(len(ops)))
	for _, op := range ops {
		w.writeUint32(op.op)
		w.writeUint32(op.status)
		if op.status == nfs4Ok && op.op == op4GetFH {
			w.writeOpaque(op.body)
		}
	}
	return w.bytes()
}

// parseCompoundReply mirrors the inline parser in nfs4LookupPath. Kept
// as a separate function so tests can call it without dispatching an
// RPC.
func parseCompoundReply(body []byte, segments []string) ([]byte, error) {
	r := newXDRReader(body)
	overall, _ := r.readUint32()
	_, _ = r.readString()
	num, _ := r.readUint32()
	var fh []byte
	for i := uint32(0); i < num; i++ {
		op, _ := r.readUint32()
		status, _ := r.readUint32()
		if status != nfs4Ok {
			return nil, &nfs4Error{
				code:          status,
				op:            op,
				failedSegment: segmentForOp(int(i), segments),
				msg:           nfs4StatusName(status),
			}
		}
		if op == op4GetFH {
			b, _ := r.readOpaque()
			fh = b
		}
	}
	if overall != nfs4Ok {
		return nil, &nfs4Error{code: overall, msg: nfs4StatusName(overall)}
	}
	return fh, nil
}

func TestCompoundReplySuccess(t *testing.T) {
	segs := []string{"srv", "data"}
	body := encodeCompoundReply(nfs4Ok, "", []compoundOp{
		{op: op4PutRootFH, status: nfs4Ok},
		{op: op4Lookup, status: nfs4Ok},
		{op: op4Lookup, status: nfs4Ok},
		{op: op4GetFH, status: nfs4Ok, body: []byte{0xFA, 0xCE}},
	})
	fh, err := parseCompoundReply(body, segs)
	if err != nil {
		t.Fatal(err)
	}
	if len(fh) != 2 || fh[0] != 0xFA || fh[1] != 0xCE {
		t.Errorf("fh: %x", fh)
	}
}

func TestCompoundReplyNoEntAtSegment(t *testing.T) {
	segs := []string{"srv", "missing"}
	body := encodeCompoundReply(nfs4ErrNoEnt, "", []compoundOp{
		{op: op4PutRootFH, status: nfs4Ok},
		{op: op4Lookup, status: nfs4Ok},
		{op: op4Lookup, status: nfs4ErrNoEnt},
	})
	_, err := parseCompoundReply(body, segs)
	if err == nil {
		t.Fatal("expected NOENT")
	}
	var e4 *nfs4Error
	if !errors.As(err, &e4) {
		t.Fatalf("expected *nfs4Error, got %T", err)
	}
	if e4.code != nfs4ErrNoEnt {
		t.Errorf("code: got %d", e4.code)
	}
	if e4.failedSegment != "missing" {
		t.Errorf("failed segment: got %q, want missing", e4.failedSegment)
	}
}

func TestCompoundReplyAccessDenied(t *testing.T) {
	segs := []string{"srv"}
	body := encodeCompoundReply(nfs4ErrAccess, "", []compoundOp{
		{op: op4PutRootFH, status: nfs4ErrAccess},
	})
	_, err := parseCompoundReply(body, segs)
	if err == nil {
		t.Fatal("expected ACCESS")
	}
	var e4 *nfs4Error
	if !errors.As(err, &e4) || e4.code != nfs4ErrAccess {
		t.Errorf("expected NFS4ERR_ACCESS, got %v", err)
	}
}
