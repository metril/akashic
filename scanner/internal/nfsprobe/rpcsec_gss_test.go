package nfsprobe

import (
	"bytes"
	"encoding/binary"
	"testing"
)

// gssCred wire shape per RFC 2203 §5.3.1: { version, gss_proc, seq_num,
// service, handle<> }. Round-trip through marshal then decode the
// fields manually to keep this test independent of the parser.
func TestGSSCredMarshal(t *testing.T) {
	c := gssCred{
		version: rpcGSSVersion1,
		gssProc: rpcGSSProcData,
		seqNum:  42,
		service: rpcGSSSvcNone,
		handle:  []byte{0xde, 0xad, 0xbe, 0xef},
	}
	got := c.marshal()

	want := []byte{
		0x00, 0x00, 0x00, 0x01, // version
		0x00, 0x00, 0x00, 0x00, // gss_proc = DATA
		0x00, 0x00, 0x00, 0x2a, // seq_num = 42
		0x00, 0x00, 0x00, 0x01, // service = NONE
		0x00, 0x00, 0x00, 0x04, // handle length
		0xde, 0xad, 0xbe, 0xef, // handle bytes (already 4-byte aligned)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("gssCred.marshal:\nwant %x\ngot  %x", want, got)
	}
}

// gssCred with empty handle (the INIT call shape) — confirms the
// length prefix is written even when handle is nil.
func TestGSSCredMarshalEmptyHandle(t *testing.T) {
	c := gssCred{
		version: rpcGSSVersion1,
		gssProc: rpcGSSProcInit,
		seqNum:  0,
		service: rpcGSSSvcNone,
		handle:  nil,
	}
	got := c.marshal()
	// 4 uint32 fields + 4 bytes for the (zero) handle length = 20 bytes.
	if len(got) != 20 {
		t.Fatalf("INIT cred length: want 20, got %d (%x)", len(got), got)
	}
	if binary.BigEndian.Uint32(got[16:20]) != 0 {
		t.Fatalf("expected zero handle length, got %x", got[16:20])
	}
}

// parseGSSInitReply happy-path. Hand-built reply matches the wire shape
// the server emits: handle, gss_major=0, gss_minor=0, seq_window, gss_token.
func TestParseGSSInitReply(t *testing.T) {
	w := newXDRWriter()
	w.writeOpaque([]byte{0x01, 0x02, 0x03, 0x04, 0x05}) // handle (5 bytes — odd len exercises padding)
	w.writeUint32(gssMajorComplete)
	w.writeUint32(0)   // minor
	w.writeUint32(128) // seq_window
	w.writeOpaque([]byte("token-bytes"))

	r, err := parseGSSInitReply(w.bytes())
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	if !bytes.Equal(r.handle, []byte{1, 2, 3, 4, 5}) {
		t.Errorf("handle: got %x", r.handle)
	}
	if r.gssMajor != gssMajorComplete {
		t.Errorf("gss_major: got %d", r.gssMajor)
	}
	if r.seqWindow != 128 {
		t.Errorf("seq_window: got %d", r.seqWindow)
	}
	if string(r.gssToken) != "token-bytes" {
		t.Errorf("gss_token: got %q", string(r.gssToken))
	}
}

// parseGSSInitReply truncated payload — every field's bounds error is
// surfaced as a *gssParseError so the gss_context.go caller can
// distinguish "truncated handle" from "truncated seq_window".
func TestParseGSSInitReplyTruncated(t *testing.T) {
	cases := []struct {
		name string
		body []byte
		want string
	}{
		{"empty", []byte{}, "init reply handle"},
		{
			name: "no major",
			body: func() []byte {
				w := newXDRWriter()
				w.writeOpaque([]byte{1})
				return w.bytes()
			}(),
			want: "init reply gss_major",
		},
		{
			name: "no token",
			body: func() []byte {
				w := newXDRWriter()
				w.writeOpaque([]byte{1})
				w.writeUint32(0)   // major
				w.writeUint32(0)   // minor
				w.writeUint32(128) // seq_window
				return w.bytes()
			}(),
			want: "init reply gss_token",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := parseGSSInitReply(tc.body)
			if err == nil {
				t.Fatal("expected error, got nil")
			}
			pe, ok := err.(*gssParseError)
			if !ok {
				t.Fatalf("want *gssParseError, got %T (%v)", err, err)
			}
			if pe.field != tc.want {
				t.Errorf("field: want %q, got %q", tc.want, pe.field)
			}
		})
	}
}

// gssAuthBuilder.credAndSign() bumps seq_num atomically across calls.
// The closure captures the same seq_num the cred carries so cred and
// MIC verifier can never disagree.
func TestGSSAuthBuilderSeqNumProgression(t *testing.T) {
	g := &gssAuthBuilder{
		handle:  []byte{0xab, 0xcd},
		service: rpcGSSSvcNone,
	}
	c1, _ := g.credAndSign()
	c2, _ := g.credAndSign()
	c3, _ := g.credAndSign()

	// Decode each cred body to confirm seq_num is monotonic 1, 2, 3.
	for i, body := range [][]byte{c1.body, c2.body, c3.body} {
		r := newXDRReader(body)
		if v, _ := r.readUint32(); v != rpcGSSVersion1 {
			t.Errorf("call %d version: got %d", i+1, v)
		}
		if v, _ := r.readUint32(); v != rpcGSSProcData {
			t.Errorf("call %d gss_proc: got %d", i+1, v)
		}
		seq, _ := r.readUint32()
		if seq != uint32(i+1) {
			t.Errorf("call %d seq_num: want %d, got %d", i+1, i+1, seq)
		}
	}

	// All three should carry flavor=RPCSEC_GSS.
	for i, c := range []authBody{c1, c2, c3} {
		if c.flavor != authRPCSecGSS {
			t.Errorf("cred %d flavor: want %d, got %d", i, authRPCSecGSS, c.flavor)
		}
	}
}

// seqWindow enforcement — once seq_num exceeds the server-advertised
// window, the sign closure surfaces a clear error rather than silently
// producing a verifier the server will reject.
func TestGSSAuthBuilderSeqWindowExceeded(t *testing.T) {
	g := &gssAuthBuilder{
		handle:    []byte{0x01},
		service:   rpcGSSSvcNone,
		seqWindow: 2,
	}
	// Exhaust the window: seq 1, seq 2 (both OK), seq 3 (over).
	_, sign1 := g.credAndSign()
	_, _ = sign1([]byte{1})
	_, sign2 := g.credAndSign()
	_, _ = sign2([]byte{1})
	_, sign3 := g.credAndSign()
	if _, err := sign3([]byte{1}); err == nil {
		t.Fatal("expected error when seq_num exceeds seq_window")
	}
}

// initAuthBuilder produces the canonical INIT cred shape (proc=INIT,
// handle empty, seq_num=0) and AUTH_NONE verifier.
func TestInitAuthBuilder(t *testing.T) {
	b := initAuthBuilder{}
	cred := b.cred()
	if cred.flavor != authRPCSecGSS {
		t.Errorf("flavor: got %d, want %d", cred.flavor, authRPCSecGSS)
	}
	r := newXDRReader(cred.body)
	if v, _ := r.readUint32(); v != rpcGSSVersion1 {
		t.Errorf("version: got %d", v)
	}
	if v, _ := r.readUint32(); v != rpcGSSProcInit {
		t.Errorf("gss_proc: got %d, want INIT", v)
	}
	if v, _ := r.readUint32(); v != 0 {
		t.Errorf("seq_num: got %d, want 0", v)
	}
	if v, _ := r.readUint32(); v != rpcGSSSvcNone {
		t.Errorf("service: got %d", v)
	}
	if v, _ := r.readUint32(); v != 0 {
		t.Errorf("INIT handle length: got %d, want 0", v)
	}

	ver := b.verifier()
	if ver.flavor != authNone {
		t.Errorf("INIT verifier flavor: got %d, want AUTH_NONE", ver.flavor)
	}
	if len(ver.body) != 0 {
		t.Errorf("INIT verifier body: should be empty, got %x", ver.body)
	}
}
