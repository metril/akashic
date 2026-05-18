// Coverage for the SMB operation guard (v0.32.2).
//
// A stalled SMB server otherwise parks the scan goroutine in a
// deadline-less socket read forever — Go context cancellation can't
// interrupt a syscall, so the scanner wedges until restarted. guard()
// bounds each SMB op: on timeout or context cancellation it force-
// closes the connection (which unblocks the wedged go-smb2 call) so the
// walk unwinds. These tests pin that contract without a live server.
package connector

import (
	"context"
	"errors"
	"io"
	"net"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// fakeConn is a net.Conn stub that records whether Close was called.
type fakeConn struct {
	closed atomic.Bool
}

func (f *fakeConn) Read([]byte) (int, error)         { return 0, io.EOF }
func (f *fakeConn) Write(b []byte) (int, error)      { return len(b), nil }
func (f *fakeConn) Close() error                     { f.closed.Store(true); return nil }
func (f *fakeConn) LocalAddr() net.Addr              { return nil }
func (f *fakeConn) RemoteAddr() net.Addr             { return nil }
func (f *fakeConn) SetDeadline(time.Time) error      { return nil }
func (f *fakeConn) SetReadDeadline(time.Time) error  { return nil }
func (f *fakeConn) SetWriteDeadline(time.Time) error { return nil }

// blockingReader is an io.ReadCloser whose Read never returns until the
// block channel is closed — a stand-in for a stalled SMB file handle.
type blockingReader struct{ block chan struct{} }

func (b *blockingReader) Read([]byte) (int, error) { <-b.block; return 0, io.EOF }
func (b *blockingReader) Close() error             { return nil }

func TestGuard_FastOpReturnsImmediately(t *testing.T) {
	fc := &fakeConn{}
	c := &SMBConnector{ctx: context.Background(), conn: fc}

	if err := c.guard("fast", func() error { return nil }); err != nil {
		t.Fatalf("fast op: unexpected error %v", err)
	}
	if c.stalled.Load() {
		t.Error("stalled flag set on a fast op")
	}
	if fc.closed.Load() {
		t.Error("connection closed on a fast op")
	}
}

func TestGuard_PropagatesOpError(t *testing.T) {
	c := &SMBConnector{ctx: context.Background(), conn: &fakeConn{}}
	sentinel := errors.New("permission denied")

	err := c.guard("erroring", func() error { return sentinel })
	if !errors.Is(err, sentinel) {
		t.Fatalf("guard should return fn's error verbatim, got %v", err)
	}
	if c.stalled.Load() {
		t.Error("a plain op error must not latch stalled")
	}
}

func TestGuard_StalledOpTimesOutAndClosesConn(t *testing.T) {
	old := smbOpTimeout
	smbOpTimeout = 50 * time.Millisecond
	defer func() { smbOpTimeout = old }()

	fc := &fakeConn{}
	c := &SMBConnector{ctx: context.Background(), conn: fc}
	block := make(chan struct{})
	defer close(block) // release the leaked op goroutine at test end

	start := time.Now()
	err := c.guard("stalled", func() error {
		<-block
		return nil
	})
	if err == nil {
		t.Fatal("expected a stall error, got nil")
	}
	if !c.stalled.Load() {
		t.Error("stalled flag not set after a timeout")
	}
	if !fc.closed.Load() {
		t.Error("connection not force-closed after a stall")
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Errorf("guard took %s — should return after ~smbOpTimeout", elapsed)
	}
}

func TestGuard_CancelledContextClosesConn(t *testing.T) {
	fc := &fakeConn{}
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // scan cancelled / finalized
	c := &SMBConnector{ctx: ctx, conn: fc}
	block := make(chan struct{})
	defer close(block)

	err := c.guard("cancelled", func() error {
		<-block
		return nil
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
	if !fc.closed.Load() {
		t.Error("connection not closed on context cancellation")
	}
}

func TestGuardedReader_HealthyReadPassesThrough(t *testing.T) {
	c := &SMBConnector{ctx: context.Background(), conn: &fakeConn{}}
	gr := &guardedReader{c: c, rc: io.NopCloser(strings.NewReader("hello"))}

	buf := make([]byte, 5)
	n, err := io.ReadFull(gr, buf)
	if err != nil {
		t.Fatalf("guardedReader read: n=%d err=%v", n, err)
	}
	if string(buf) != "hello" {
		t.Errorf("got %q, want %q", buf, "hello")
	}
	if c.stalled.Load() {
		t.Error("stalled flag set on a healthy read")
	}
}

func TestGuardedReader_StalledReadTripsGuard(t *testing.T) {
	old := smbOpTimeout
	smbOpTimeout = 50 * time.Millisecond
	defer func() { smbOpTimeout = old }()

	fc := &fakeConn{}
	c := &SMBConnector{ctx: context.Background(), conn: fc}
	br := &blockingReader{block: make(chan struct{})}
	defer close(br.block)
	gr := &guardedReader{c: c, rc: br}

	if _, err := gr.Read(make([]byte, 8)); err == nil {
		t.Fatal("expected a stall error from a blocked file read")
	}
	if !fc.closed.Load() {
		t.Error("connection not closed when a file read stalled")
	}
}
