package lsarpc

import (
	"bytes"
	"encoding/binary"
	"io"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

// mockTransport records writes and serves canned responses for reads.
type mockTransport struct {
	written  bytes.Buffer
	respond  []byte
	readPos  int
	closeErr error
	closed   bool
}

func (m *mockTransport) Write(p []byte) (int, error) {
	return m.written.Write(p)
}

func (m *mockTransport) Read(p []byte) (int, error) {
	if m.readPos >= len(m.respond) {
		return 0, io.EOF
	}
	n := copy(p, m.respond[m.readPos:])
	m.readPos += n
	return n, nil
}

func (m *mockTransport) Close() error {
	m.closed = true
	return m.closeErr
}

func TestClient_Close_SendsLsarCloseWhenOpened(t *testing.T) {
	c := &Client{t: &mockTransport{}, callID: 5, opened: true}
	for i := range c.handle {
		c.handle[i] = byte(i)
	}
	mt := c.t.(*mockTransport)
	// Pre-load a fake response: 16-byte header + 8-byte response prefix + 4-byte body (status).
	// We don't read it back, but readResponseBody will try; just give it enough bytes.
	hdr := dcerpc.PDUHeader{PType: dcerpc.PtypeResponse, Flags: dcerpc.PfcFirstFrag | dcerpc.PfcLastFrag, FragLen: 28, CallID: 5}.Marshal()
	respHdr := make([]byte, 8)
	binary.LittleEndian.PutUint32(respHdr[0:4], 4)
	binary.LittleEndian.PutUint16(respHdr[4:6], 0)
	binary.LittleEndian.PutUint16(respHdr[6:8], 0)
	body := []byte{0, 0, 0, 0}
	mt.respond = append(append(hdr, respHdr...), body...)

	if err := c.Close(); err != nil {
		t.Fatal(err)
	}
	if !mt.closed {
		t.Error("transport not closed")
	}
	if mt.written.Len() == 0 {
		t.Error("expected LsarClose request to be written")
	}
	// Verify written packet has opnum LsarClose (0).
	written := mt.written.Bytes()
	if len(written) < 24 {
		t.Fatalf("written packet too short: %d", len(written))
	}
	opnum := binary.LittleEndian.Uint16(written[16+6 : 16+8])
	if opnum != OpnumLsarClose {
		t.Errorf("opnum: got %d want %d (LsarClose)", opnum, OpnumLsarClose)
	}
}

func TestClient_Close_NoLsarCloseWhenNeverOpened(t *testing.T) {
	mt := &mockTransport{}
	c := &Client{t: mt, callID: 1, opened: false}
	if err := c.Close(); err != nil {
		t.Fatal(err)
	}
	if !mt.closed {
		t.Error("transport not closed")
	}
	if mt.written.Len() != 0 {
		t.Errorf("expected no writes when never opened, got %d bytes", mt.written.Len())
	}
}
