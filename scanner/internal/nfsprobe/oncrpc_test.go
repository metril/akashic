package nfsprobe

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"testing"
	"time"
)

// rpcExchangeFixture wraps net.Pipe — full-duplex synchronous in-memory
// pipe. The probe code under test runs on `client` and the test
// imitates a server on `server`, reading the call out and writing a
// canned reply back. No real socket / no flaky timing.
type rpcExchangeFixture struct {
	client net.Conn
	server net.Conn
}

func newRPCFixture(t *testing.T) *rpcExchangeFixture {
	t.Helper()
	c, s := net.Pipe()
	t.Cleanup(func() {
		c.Close()
		s.Close()
	})
	return &rpcExchangeFixture{client: c, server: s}
}

// readCallFromServerSide reads one record-marked RPC call from the
// server side of the pipe and returns the inner body bytes.
func (f *rpcExchangeFixture) readCallFromServerSide(t *testing.T) []byte {
	t.Helper()
	_ = f.server.SetReadDeadline(time.Now().Add(2 * time.Second))
	hdr := make([]byte, 4)
	if _, err := io.ReadFull(f.server, hdr); err != nil {
		t.Fatalf("read marker: %v", err)
	}
	mark := binary.BigEndian.Uint32(hdr)
	fragLen := int(mark &^ (1 << 31))
	body := make([]byte, fragLen)
	if _, err := io.ReadFull(f.server, body); err != nil {
		t.Fatalf("read body: %v", err)
	}
	return body
}

// writeReplyFromServerSide constructs a single-fragment reply with the
// given xid + status fields and writes it back to the client.
func (f *rpcExchangeFixture) writeReplyFromServerSide(t *testing.T, body []byte) {
	t.Helper()
	_ = f.server.SetWriteDeadline(time.Now().Add(2 * time.Second))
	mark := uint32(len(body)) | (1 << 31)
	var hdr [4]byte
	binary.BigEndian.PutUint32(hdr[:], mark)
	if _, err := f.server.Write(hdr[:]); err != nil {
		t.Fatalf("write marker: %v", err)
	}
	if _, err := f.server.Write(body); err != nil {
		t.Fatalf("write body: %v", err)
	}
}

// buildReplyBody constructs the inner body of an RPC reply (no record
// marker). The fixture's `writeReplyFromServerSide` adds the marker.
func buildReplyBody(xid uint32, replyStat uint32, acceptStat uint32, payload []byte) []byte {
	w := newXDRWriter()
	w.writeUint32(xid)
	w.writeUint32(rpcReply)
	w.writeUint32(replyStat)
	if replyStat == rpcMsgAccepted {
		writeAuthBody(w, authBody{flavor: authNone, body: nil}) // verifier
		w.writeUint32(acceptStat)
		w.buf = append(w.buf, payload...)
	}
	return w.bytes()
}

func inspectCallXID(t *testing.T, callBody []byte) uint32 {
	t.Helper()
	if len(callBody) < 4 {
		t.Fatal("call too short")
	}
	// callBody starts at xid (no record marker — fixture stripped it).
	return binary.BigEndian.Uint32(callBody[:4])
}

func TestRPCExchangeSuccessReturnsProcReply(t *testing.T) {
	f := newRPCFixture(t)
	procReply := []byte{0x00, 0x00, 0x00, 0x42}

	type resT struct {
		body []byte
		err  error
	}
	done := make(chan resT, 1)
	go func() {
		body, err := doRPCExchange(f.client, 100000, 2, 3, noAuth, []byte{0x00, 0x00, 0x00, 0x01})
		done <- resT{body, err}
	}()

	callBody := f.readCallFromServerSide(t)
	xid := inspectCallXID(t, callBody)
	f.writeReplyFromServerSide(t, buildReplyBody(xid, rpcMsgAccepted, rpcSuccess, procReply))

	res := <-done
	if res.err != nil {
		t.Fatalf("err: %v", res.err)
	}
	if !bytes.Equal(res.body, procReply) {
		t.Errorf("body: got %x, want %x", res.body, procReply)
	}
}

func TestRPCExchangeProcedureUnavailable(t *testing.T) {
	f := newRPCFixture(t)
	done := make(chan error, 1)
	go func() {
		_, err := doRPCExchange(f.client, 100000, 2, 99, noAuth, nil)
		done <- err
	}()
	callBody := f.readCallFromServerSide(t)
	xid := inspectCallXID(t, callBody)
	f.writeReplyFromServerSide(t, buildReplyBody(xid, rpcMsgAccepted, rpcProcUnavail, nil))

	err := <-done
	if err == nil {
		t.Fatal("expected error")
	}
	var rerr *rpcError
	if !errors.As(err, &rerr) {
		t.Fatalf("expected *rpcError, got %T: %v", err, err)
	}
	if rerr.kind != rpcErrAccept || rerr.code != rpcProcUnavail {
		t.Errorf("got kind=%d code=%d, want Accept/PROC_UNAVAIL", rerr.kind, rerr.code)
	}
}

func TestRPCExchangeXIDMismatchIsRejected(t *testing.T) {
	f := newRPCFixture(t)
	done := make(chan error, 1)
	go func() {
		_, err := doRPCExchange(f.client, 1, 1, 1, noAuth, nil)
		done <- err
	}()
	_ = f.readCallFromServerSide(t)
	// Reply with a deliberately-wrong xid.
	f.writeReplyFromServerSide(t, buildReplyBody(0xBADBAD, rpcMsgAccepted, rpcSuccess, nil))

	err := <-done
	if err == nil {
		t.Fatal("expected mismatch error")
	}
	var rerr *rpcError
	if !errors.As(err, &rerr) || rerr.kind != rpcErrDecode {
		t.Errorf("expected decode error for xid mismatch, got %v", err)
	}
}

func TestRPCExchangeAuthDeniedSurfacesTypedError(t *testing.T) {
	f := newRPCFixture(t)
	done := make(chan error, 1)
	go func() {
		_, err := doRPCExchange(f.client, 1, 1, 1, noAuth, nil)
		done <- err
	}()
	callBody := f.readCallFromServerSide(t)
	xid := inspectCallXID(t, callBody)
	// MSG_DENIED + AUTH_ERROR + auth_stat=2 (AUTH_BADCRED).
	w := newXDRWriter()
	w.writeUint32(xid)
	w.writeUint32(rpcReply)
	w.writeUint32(rpcMsgDenied)
	w.writeUint32(rpcAuthError)
	w.writeUint32(2)
	f.writeReplyFromServerSide(t, w.bytes())

	err := <-done
	if err == nil {
		t.Fatal("expected error")
	}
	var rerr *rpcError
	if !errors.As(err, &rerr) {
		t.Fatalf("expected *rpcError, got %T", err)
	}
	if rerr.kind != rpcErrAuth || rerr.code != 2 {
		t.Errorf("got kind=%d code=%d", rerr.kind, rerr.code)
	}
}
