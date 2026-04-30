// Package nfsprobe implements a minimal NFS export-validity probe used
// only as a pre-flight check from the scanner's `test-connection`
// subcommand. It speaks just enough of ONC-RPC, MOUNT3, and NFSv4 to
// confirm a server is exporting a given path to this client under a
// chosen auth flavor — no read/write/walk operations.
//
// File layout:
//
//   xdr.go       — XDR primitive encode/decode (RFC 4506)
//   oncrpc.go    — RPC v2 record-marker framing + call/reply headers (RFC 5531)
//   auth_none.go — AUTH_NONE credential
//   auth_sys.go  — AUTH_SYS credential (uid/gid/aux gids)
//   portmap.go   — PMAPPROC_GETPORT (program 100000, version 2)
//   mount3.go    — MOUNT3 EXPORT, MNT, UMNT (program 100005, version 3)
//   nfsv4.go     — COMPOUND with PUTROOTFH + LOOKUP + GETFH (program 100003, version 4)
//   probe.go     — public entry: Probe(opts) — cascade across the protocols
//
// The intent is small surface, hand-rolled, no external NFS dependency.
// Scope deliberately excludes anything beyond the handshake: no reads,
// no writes, no symlink traversal, no caching.
package nfsprobe

import (
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

// XDR is a minimal big-endian writer/reader covering the primitives the
// MOUNT3 + NFSv4 + portmap procedures actually use. Per RFC 4506 every
// value is multiple-of-4-bytes aligned with zero padding.

type xdrWriter struct {
	buf []byte
}

func newXDRWriter() *xdrWriter { return &xdrWriter{} }

func (w *xdrWriter) bytes() []byte { return w.buf }

func (w *xdrWriter) writeUint32(v uint32) {
	var b [4]byte
	binary.BigEndian.PutUint32(b[:], v)
	w.buf = append(w.buf, b[:]...)
}

func (w *xdrWriter) writeUint64(v uint64) {
	var b [8]byte
	binary.BigEndian.PutUint64(b[:], v)
	w.buf = append(w.buf, b[:]...)
}

func (w *xdrWriter) writeBool(v bool) {
	if v {
		w.writeUint32(1)
	} else {
		w.writeUint32(0)
	}
}

// writeOpaque emits a length-prefixed variable-length opaque, padded
// to the next 4-byte boundary. Per RFC 4506 §4.10 ("Variable-Length
// Opaque Data"). Same wire shape as `string<>` (§4.11).
func (w *xdrWriter) writeOpaque(b []byte) {
	w.writeUint32(uint32(len(b)))
	w.buf = append(w.buf, b...)
	pad := (4 - (len(b) % 4)) % 4
	if pad > 0 {
		w.buf = append(w.buf, make([]byte, pad)...)
	}
}

func (w *xdrWriter) writeString(s string) { w.writeOpaque([]byte(s)) }

// writeUint32List is a vector<uint32> (§4.13) — the auxiliary GIDs
// list in AUTH_SYS uses this shape.
func (w *xdrWriter) writeUint32List(items []uint32) {
	w.writeUint32(uint32(len(items)))
	for _, v := range items {
		w.writeUint32(v)
	}
}

// xdrReader is the consume side. Each method advances `pos`; bounds
// errors return nontrivial messages so callers can pinpoint which
// reply field failed to decode.
type xdrReader struct {
	buf []byte
	pos int
}

func newXDRReader(b []byte) *xdrReader { return &xdrReader{buf: b} }

func (r *xdrReader) remaining() int { return len(r.buf) - r.pos }

func (r *xdrReader) readUint32() (uint32, error) {
	if r.remaining() < 4 {
		return 0, fmt.Errorf("xdr: short read at %d (need 4, have %d)", r.pos, r.remaining())
	}
	v := binary.BigEndian.Uint32(r.buf[r.pos:])
	r.pos += 4
	return v, nil
}

func (r *xdrReader) readUint64() (uint64, error) {
	if r.remaining() < 8 {
		return 0, fmt.Errorf("xdr: short read at %d (need 8, have %d)", r.pos, r.remaining())
	}
	v := binary.BigEndian.Uint64(r.buf[r.pos:])
	r.pos += 8
	return v, nil
}

func (r *xdrReader) readBool() (bool, error) {
	v, err := r.readUint32()
	if err != nil {
		return false, err
	}
	return v != 0, nil
}

func (r *xdrReader) readOpaque() ([]byte, error) {
	n, err := r.readUint32()
	if err != nil {
		return nil, fmt.Errorf("opaque length: %w", err)
	}
	if n > 1<<24 {
		// Cap to 16 MB. NFS handles and export lists are well under this;
		// a larger value usually means we de-synced and read the wrong
		// field as a length.
		return nil, fmt.Errorf("xdr: opaque length %d exceeds 16 MB", n)
	}
	if r.remaining() < int(n) {
		return nil, fmt.Errorf("xdr: opaque body short (need %d, have %d)", n, r.remaining())
	}
	out := make([]byte, n)
	copy(out, r.buf[r.pos:r.pos+int(n)])
	r.pos += int(n)
	pad := (4 - (int(n) % 4)) % 4
	if r.remaining() < pad {
		return nil, fmt.Errorf("xdr: opaque pad short")
	}
	r.pos += pad
	return out, nil
}

func (r *xdrReader) readString() (string, error) {
	b, err := r.readOpaque()
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// errShortRead is returned by helpers that read against `io.Reader`
// rather than the in-memory buffer. Tests assert against errors.Is.
var errShortRead = errors.New("nfsprobe: short read from network")

// readFull is a convenience around io.ReadFull that wraps EOF into a
// distinguishable sentinel.
func readFull(r io.Reader, n int) ([]byte, error) {
	buf := make([]byte, n)
	if _, err := io.ReadFull(r, buf); err != nil {
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return nil, fmt.Errorf("%w: %v", errShortRead, err)
		}
		return nil, err
	}
	return buf, nil
}
