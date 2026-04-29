package samr

import (
	"bytes"
	"encoding/binary"
	"errors"
	"io"
	"testing"
)

// scriptedTransport replays a sequence of canned response PDUs in order.
// Writes are drained into wbuf for inspection.
type scriptedTransport struct {
	wbuf      bytes.Buffer
	responses [][]byte
	pos       int
	closed    bool
}

func (s *scriptedTransport) Write(p []byte) (int, error) { return s.wbuf.Write(p) }

func (s *scriptedTransport) Read(p []byte) (int, error) {
	if s.pos >= len(s.responses) {
		return 0, io.EOF
	}
	n := copy(p, s.responses[s.pos])
	if n < len(s.responses[s.pos]) {
		s.responses[s.pos] = s.responses[s.pos][n:]
		return n, nil
	}
	s.pos++
	return n, nil
}

func (s *scriptedTransport) Close() error {
	s.closed = true
	return nil
}

// Helpers to build canned response PDUs.

func bindAckPDU(callID uint32) []byte {
	hdr := PDUHeader{
		PType:   PtypeBindAck,
		Flags:   PfcFirstFrag | PfcLastFrag,
		FragLen: 16,
		CallID:  callID,
	}.Marshal()
	// Bind ack body is non-trivial in real life but unused by our parser, so
	// just return the bare header.
	return hdr
}

func responsePDU(callID uint32, body []byte) []byte {
	// Request/response PDUs have an 8-byte body header (alloc_hint, p_cont_id,
	// cancel_count, reserved) before the stub data. Per MS-RPCE §2.2.2.4.5,
	// response body header is:
	//   uint32 alloc_hint
	//   uint16 p_cont_id
	//   uint8  cancel_count
	//   uint8  reserved
	bodyHdr := make([]byte, 8)
	binary.LittleEndian.PutUint32(bodyHdr[0:4], uint32(len(body)))
	pdu := PDUHeader{
		PType:   PtypeResponse,
		Flags:   PfcFirstFrag | PfcLastFrag,
		FragLen: uint16(16 + 8 + len(body)),
		CallID:  callID,
	}.Marshal()
	out := append(pdu, bodyHdr...)
	out = append(out, body...)
	return out
}

func cannedConnect5Body(handleByte byte) []byte {
	body := make([]byte, 0, 32)
	body = binary.LittleEndian.AppendUint32(body, 1) // OutVersion
	body = binary.LittleEndian.AppendUint32(body, 3) // Out.Revision
	body = binary.LittleEndian.AppendUint32(body, 0) // Out.SupportedFeatures
	for i := 0; i < 20; i++ {
		body = append(body, handleByte)
	}
	body = binary.LittleEndian.AppendUint32(body, 0) // NTSTATUS
	return body
}

func cannedHandleStatusBody(handleByte byte) []byte {
	body := make([]byte, 24)
	for i := 0; i < 20; i++ {
		body[i] = handleByte
	}
	// status = 0 follows
	return body
}

func cannedGetGroupsBody(rids []uint32) []byte {
	body := make([]byte, 0, 32+len(rids)*8)
	body = binary.LittleEndian.AppendUint32(body, 0x20000) // bufPtr
	body = binary.LittleEndian.AppendUint32(body, uint32(len(rids)))
	body = binary.LittleEndian.AppendUint32(body, 0x20004) // arrPtr
	body = binary.LittleEndian.AppendUint32(body, uint32(len(rids))) // conformance
	for _, r := range rids {
		body = binary.LittleEndian.AppendUint32(body, r)
		body = binary.LittleEndian.AppendUint32(body, 7) // attrs
	}
	body = binary.LittleEndian.AppendUint32(body, 0) // NTSTATUS
	return body
}

func cannedLookupIdsBody(names []string) []byte {
	body := make([]byte, 0, 64)
	body = binary.LittleEndian.AppendUint32(body, 0x20000)        // NamesPtr
	body = binary.LittleEndian.AppendUint32(body, uint32(len(names)))
	body = binary.LittleEndian.AppendUint32(body, 0x20004)        // ElementPtr
	body = binary.LittleEndian.AppendUint32(body, uint32(len(names))) // max_count
	// Per-entry headers
	refID := uint32(0x20008)
	for _, n := range names {
		blen := uint16(len([]rune(n)) * 2) // approx; using rune count for ASCII
		body = binary.LittleEndian.AppendUint16(body, blen)
		body = binary.LittleEndian.AppendUint16(body, blen)
		body = binary.LittleEndian.AppendUint32(body, refID)
		refID += 4
	}
	// Per-entry deferred buffers
	for _, n := range names {
		runes := []rune(n)
		body = binary.LittleEndian.AppendUint32(body, uint32(len(runes))) // max_count
		body = binary.LittleEndian.AppendUint32(body, 0)                  // offset
		body = binary.LittleEndian.AppendUint32(body, uint32(len(runes))) // actual
		for _, c := range runes {
			body = binary.LittleEndian.AppendUint16(body, uint16(c))
		}
		body = AlignBytes(body, 4)
	}
	// Use array pointer = 0, then status.
	body = binary.LittleEndian.AppendUint32(body, 0) // UsePtr
	body = binary.LittleEndian.AppendUint32(body, 0) // NTSTATUS
	return body
}

// cannedLookupIdsBodyWithUseArray is like cannedLookupIdsBody but emits a
// non-null Use[] array between the deferred name buffers and the NTSTATUS
// — matching what real Windows DCs return. Catches over/under-consumption
// bugs in the name parser since Tail32() works regardless but only if the
// parser left the reader at the right position.
func cannedLookupIdsBodyWithUseArray(names []string) []byte {
	// First reuse the no-Use builder, but strip its trailing UsePtr=0 + NTSTATUS.
	base := cannedLookupIdsBody(names)
	base = base[:len(base)-8]

	body := append([]byte{}, base...)
	// UsePtr non-null
	body = binary.LittleEndian.AppendUint32(body, 0x20100)
	// UseCount = len(names)
	body = binary.LittleEndian.AppendUint32(body, uint32(len(names)))
	// UseElementsPtr non-null
	body = binary.LittleEndian.AppendUint32(body, 0x20104)
	// max_count = len(names)
	body = binary.LittleEndian.AppendUint32(body, uint32(len(names)))
	// One uint32 per name (SID_NAME_USE) — value 2 = SidTypeGroup.
	for range names {
		body = binary.LittleEndian.AppendUint32(body, 2)
	}
	// NTSTATUS = 0
	body = binary.LittleEndian.AppendUint32(body, 0)
	return body
}

func TestResolveGroupsForSid_HappyPath(t *testing.T) {
	userSid, _ := ParseSidString("S-1-5-21-1004336348-1177238915-682003330-1013")

	st := &scriptedTransport{
		responses: [][]byte{
			bindAckPDU(1),
			responsePDU(2, cannedConnect5Body(0xa1)),
			responsePDU(3, cannedHandleStatusBody(0xa2)), // OpenDomain
			responsePDU(4, cannedHandleStatusBody(0xa3)), // OpenUser
			responsePDU(5, cannedGetGroupsBody([]uint32{513, 1042})),
			responsePDU(6, cannedLookupIdsBody([]string{"users", "wheel"})),
			// Three CloseHandle responses (user, domain, server) — best-effort.
			responsePDU(7, cannedHandleStatusBody(0)),
			responsePDU(8, cannedHandleStatusBody(0)),
			responsePDU(9, cannedHandleStatusBody(0)),
		},
	}

	names, err := ResolveGroupsForSid(st, "\\\\HOST", userSid)
	if err != nil {
		t.Fatalf("ResolveGroupsForSid: %v", err)
	}
	if len(names) != 2 || names[0] != "users" || names[1] != "wheel" {
		t.Fatalf("groups = %v, want [users wheel]", names)
	}
	if !st.closed {
		t.Errorf("expected transport.Close() to be called")
	}
	// Sanity: writes should include 9 PDUs (bind, connect5, opendomain, openuser,
	// getgroups, lookupids, 3 closes).
	if got := st.wbuf.Len(); got < 100 {
		t.Errorf("transport saw only %d bytes written; expected more", got)
	}
}

func TestResolveGroupsForSid_BindFailure(t *testing.T) {
	userSid, _ := ParseSidString("S-1-5-21-1-2-3-1013")
	// Bind response with wrong PType (Fault).
	hdr := PDUHeader{PType: PtypeFault, Flags: PfcFirstFrag | PfcLastFrag, FragLen: 16, CallID: 1}.Marshal()
	st := &scriptedTransport{responses: [][]byte{hdr}}

	_, err := ResolveGroupsForSid(st, "\\\\HOST", userSid)
	if err == nil {
		t.Fatal("expected bind failure error")
	}
}

func TestResolveGroupsForSid_OpenUserNotFound(t *testing.T) {
	userSid, _ := ParseSidString("S-1-5-21-1-2-3-9999")

	// Build OpenUser response with NTSTATUS=0xC0000073 (STATUS_NONE_MAPPED).
	openUserBody := make([]byte, 24)
	binary.LittleEndian.PutUint32(openUserBody[20:24], 0xC0000073)

	st := &scriptedTransport{
		responses: [][]byte{
			bindAckPDU(1),
			responsePDU(2, cannedConnect5Body(0xa1)),
			responsePDU(3, cannedHandleStatusBody(0xa2)), // OpenDomain ok
			responsePDU(4, openUserBody),                  // OpenUser fails
			// Best-effort closes
			responsePDU(5, cannedHandleStatusBody(0)),
			responsePDU(6, cannedHandleStatusBody(0)),
		},
	}

	_, err := ResolveGroupsForSid(st, "\\\\HOST", userSid)
	if err == nil {
		t.Fatal("expected open_user failure")
	}
	// The wrapped StatusError should classify as not-found.
	var se *StatusError
	if !errors.As(err, &se) {
		t.Fatalf("expected *StatusError in chain, got %T: %v", err, err)
	}
	if !se.IsNotFound() {
		t.Fatalf("StatusError 0x%x should be IsNotFound, isn't", se.Status)
	}
}

func TestResolveGroupsForSid_OpenUserAccessDenied(t *testing.T) {
	userSid, _ := ParseSidString("S-1-5-21-1-2-3-1013")

	// STATUS_ACCESS_DENIED — the user EXISTS but the service account can't
	// open it. Must NOT classify as not-found, since masking permission
	// errors as "user not found" silently hides misconfigurations.
	openUserBody := make([]byte, 24)
	binary.LittleEndian.PutUint32(openUserBody[20:24], 0xC0000022)

	st := &scriptedTransport{
		responses: [][]byte{
			bindAckPDU(1),
			responsePDU(2, cannedConnect5Body(0xa1)),
			responsePDU(3, cannedHandleStatusBody(0xa2)),
			responsePDU(4, openUserBody),
			responsePDU(5, cannedHandleStatusBody(0)),
			responsePDU(6, cannedHandleStatusBody(0)),
		},
	}

	_, err := ResolveGroupsForSid(st, "\\\\HOST", userSid)
	if err == nil {
		t.Fatal("expected open_user failure")
	}
	var se *StatusError
	if !errors.As(err, &se) {
		t.Fatalf("expected *StatusError in chain, got %T", err)
	}
	if se.IsNotFound() {
		t.Fatalf("ACCESS_DENIED should NOT classify as not-found")
	}
}

func TestResolveGroupsForSid_HappyPathWithUseArray(t *testing.T) {
	// Same as HappyPath but the LookupIds response includes a non-null
	// Use[] array — matches what real DCs return. Catches name-buffer
	// over/under-consumption bugs.
	userSid, _ := ParseSidString("S-1-5-21-1-2-3-1013")

	st := &scriptedTransport{
		responses: [][]byte{
			bindAckPDU(1),
			responsePDU(2, cannedConnect5Body(0xa1)),
			responsePDU(3, cannedHandleStatusBody(0xa2)),
			responsePDU(4, cannedHandleStatusBody(0xa3)),
			responsePDU(5, cannedGetGroupsBody([]uint32{513, 1042})),
			responsePDU(6, cannedLookupIdsBodyWithUseArray([]string{"engineers", "users"})),
			responsePDU(7, cannedHandleStatusBody(0)),
			responsePDU(8, cannedHandleStatusBody(0)),
			responsePDU(9, cannedHandleStatusBody(0)),
		},
	}

	names, err := ResolveGroupsForSid(st, "\\\\HOST", userSid)
	if err != nil {
		t.Fatalf("ResolveGroupsForSid: %v", err)
	}
	if len(names) != 2 || names[0] != "engineers" || names[1] != "users" {
		t.Fatalf("names = %v, want [engineers users]", names)
	}
}

func TestResolveGroupsForSid_NoGroups(t *testing.T) {
	// Sometimes a user has 0 group memberships — return empty list, no error.
	userSid, _ := ParseSidString("S-1-5-21-1-2-3-1013")

	emptyGroups := make([]byte, 0, 12)
	emptyGroups = binary.LittleEndian.AppendUint32(emptyGroups, 0) // bufPtr=0
	emptyGroups = binary.LittleEndian.AppendUint32(emptyGroups, 0) // NTSTATUS

	st := &scriptedTransport{
		responses: [][]byte{
			bindAckPDU(1),
			responsePDU(2, cannedConnect5Body(0xa1)),
			responsePDU(3, cannedHandleStatusBody(0xa2)),
			responsePDU(4, cannedHandleStatusBody(0xa3)),
			responsePDU(5, emptyGroups),
			// 3 close handle responses
			responsePDU(6, cannedHandleStatusBody(0)),
			responsePDU(7, cannedHandleStatusBody(0)),
			responsePDU(8, cannedHandleStatusBody(0)),
		},
	}

	names, err := ResolveGroupsForSid(st, "\\\\HOST", userSid)
	if err != nil {
		t.Fatalf("ResolveGroupsForSid: %v", err)
	}
	if len(names) != 0 {
		t.Errorf("groups = %v, want []", names)
	}
}
