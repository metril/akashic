package samr

import (
	"encoding/binary"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

func TestBuildSamrCloseHandleRequest_OpnumAndShape(t *testing.T) {
	var h Handle
	h[0] = 0xab
	h[19] = 0xcd
	pkt := BuildSamrCloseHandleRequest(7, h)
	// PDU = 16 (header) + 8 (request hdr) + 20 (handle) = 44 bytes.
	if len(pkt) != 44 {
		t.Fatalf("len = %d, want 44", len(pkt))
	}
	// Opnum at bytes 22:24 (PDU header 16 + alloc_hint 4 + p_cont_id 2).
	opnum := binary.LittleEndian.Uint16(pkt[22:24])
	if opnum != OpnumSamrCloseHandle {
		t.Errorf("opnum = %d, want %d", opnum, OpnumSamrCloseHandle)
	}
	// Handle bytes follow at offset 24.
	if pkt[24] != 0xab || pkt[24+19] != 0xcd {
		t.Errorf("handle bytes wrong: % x...% x", pkt[24:25], pkt[24+19:24+20])
	}
}

func TestBuildSamrConnect5Request_Opnum(t *testing.T) {
	pkt := BuildSamrConnect5Request(1, "\\\\HOST", 0x21)
	opnum := binary.LittleEndian.Uint16(pkt[22:24])
	if opnum != OpnumSamrConnect5 {
		t.Errorf("opnum = %d, want %d", opnum, OpnumSamrConnect5)
	}
	// Body should include the desired access (0x21) and InVersion (1).
	body := pkt[24:]
	if len(body) < 24 {
		t.Fatalf("body too short: %d", len(body))
	}
}

func TestParseSamrConnect5Response_Status(t *testing.T) {
	// Hand-crafted response: OutVersion=1, Out.Revision=3, Out.SupportedFeatures=0,
	// 20-byte handle (all 0x55), NTSTATUS=0.
	body := make([]byte, 0, 32)
	body = binary.LittleEndian.AppendUint32(body, 1)  // OutVersion
	body = binary.LittleEndian.AppendUint32(body, 3)  // Revision
	body = binary.LittleEndian.AppendUint32(body, 0)  // Features
	for i := 0; i < 20; i++ {
		body = append(body, 0x55)
	}
	body = binary.LittleEndian.AppendUint32(body, 0) // NTSTATUS

	h, status, err := ParseSamrConnect5Response(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if status != 0 {
		t.Errorf("status = %#x, want 0", status)
	}
	if h[0] != 0x55 {
		t.Errorf("handle[0] = %#x, want 0x55", h[0])
	}
}

func TestBuildSamrOpenDomainRequest_EmbedsSID(t *testing.T) {
	dom, _ := ParseSidString("S-1-5-21-1004336348-1177238915-682003330")
	var srv Handle
	pkt := BuildSamrOpenDomainRequest(2, srv, 0x205, dom)
	opnum := binary.LittleEndian.Uint16(pkt[22:24])
	if opnum != OpnumSamrOpenDomain {
		t.Errorf("opnum = %d, want %d", opnum, OpnumSamrOpenDomain)
	}
	// First 20 bytes after request header is the server handle (zeroed).
	for i := 24; i < 44; i++ {
		if pkt[i] != 0 {
			t.Errorf("handle byte %d = %#x, want 0", i, pkt[i])
		}
	}
	// Then DesiredAccess = 0x205.
	access := binary.LittleEndian.Uint32(pkt[44:48])
	if access != 0x205 {
		t.Errorf("access = %#x, want 0x205", access)
	}
	// Then RPC_SID conformance count = 4, revision = 1, sub-auth count = 4.
	count := binary.LittleEndian.Uint32(pkt[48:52])
	if count != 4 {
		t.Errorf("conformance count = %d, want 4", count)
	}
	if pkt[52] != 1 || pkt[53] != 4 {
		t.Errorf("rev/subauth count = %d/%d, want 1/4", pkt[52], pkt[53])
	}
}

func TestBuildSamrOpenUserRequest_Layout(t *testing.T) {
	var dom Handle
	pkt := BuildSamrOpenUserRequest(3, dom, 0x100, 1013)
	if got := binary.LittleEndian.Uint16(pkt[22:24]); got != OpnumSamrOpenUser {
		t.Errorf("opnum = %d, want %d", got, OpnumSamrOpenUser)
	}
	// access=0x100 at body offset 20, rid=1013 at 24.
	access := binary.LittleEndian.Uint32(pkt[44:48])
	rid := binary.LittleEndian.Uint32(pkt[48:52])
	if access != 0x100 || rid != 1013 {
		t.Errorf("access/rid = %#x/%d, want 0x100/1013", access, rid)
	}
}

func TestParseSamrGetGroupsForUserResponse_TwoGroups(t *testing.T) {
	// referent + count + array-ptr + conformance + 2*(rid,attrs) + status
	body := make([]byte, 0, 36)
	body = binary.LittleEndian.AppendUint32(body, 0x20000) // bufPtr
	body = binary.LittleEndian.AppendUint32(body, 2)       // count
	body = binary.LittleEndian.AppendUint32(body, 0x20004) // arrPtr
	body = binary.LittleEndian.AppendUint32(body, 2)       // conformance
	body = binary.LittleEndian.AppendUint32(body, 513)     // rid
	body = binary.LittleEndian.AppendUint32(body, 7)       // attrs
	body = binary.LittleEndian.AppendUint32(body, 1042)    // rid
	body = binary.LittleEndian.AppendUint32(body, 7)       // attrs
	body = binary.LittleEndian.AppendUint32(body, 0)       // NTSTATUS

	groups, status, err := ParseSamrGetGroupsForUserResponse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if status != 0 {
		t.Errorf("status = %#x, want 0", status)
	}
	if len(groups) != 2 {
		t.Fatalf("count = %d, want 2", len(groups))
	}
	if groups[0].RID != 513 || groups[1].RID != 1042 {
		t.Errorf("rids = %d,%d, want 513,1042", groups[0].RID, groups[1].RID)
	}
}

func TestParseSamrGetGroupsForUserResponse_NullBuf(t *testing.T) {
	body := []byte{
		0, 0, 0, 0, // bufPtr = 0
		0, 0, 0, 0, // NTSTATUS = 0
	}
	groups, _, err := ParseSamrGetGroupsForUserResponse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if groups != nil {
		t.Errorf("expected nil groups, got %v", groups)
	}
}

func TestBuildSamrLookupIdsInDomainRequest_ConformanceMarkers(t *testing.T) {
	var dom Handle
	pkt := BuildSamrLookupIdsInDomainRequest(4, dom, []uint32{513, 1042})
	if got := binary.LittleEndian.Uint16(pkt[22:24]); got != OpnumSamrLookupIdsInDomain {
		t.Errorf("opnum = %d, want %d", got, OpnumSamrLookupIdsInDomain)
	}
	// After 20-byte handle: count=2, max=1000, offset=0, actual=2, then 2 RIDs.
	count := binary.LittleEndian.Uint32(pkt[44:48])
	max := binary.LittleEndian.Uint32(pkt[48:52])
	offset := binary.LittleEndian.Uint32(pkt[52:56])
	actual := binary.LittleEndian.Uint32(pkt[56:60])
	if count != 2 || max != 1000 || offset != 0 || actual != 2 {
		t.Errorf("hdr = count=%d max=%d off=%d actual=%d", count, max, offset, actual)
	}
	rid0 := binary.LittleEndian.Uint32(pkt[60:64])
	rid1 := binary.LittleEndian.Uint32(pkt[64:68])
	if rid0 != 513 || rid1 != 1042 {
		t.Errorf("rids = %d,%d", rid0, rid1)
	}
}

func TestParseSamrLookupIdsInDomainResponse_TwoNames(t *testing.T) {
	// Two names: "users" (5 chars) and "wheel" (5 chars).
	body := make([]byte, 0, 128)
	// NamesPtr non-null
	body = binary.LittleEndian.AppendUint32(body, 0x20000)
	// count = 2
	body = binary.LittleEndian.AppendUint32(body, 2)
	// ElementPtr non-null
	body = binary.LittleEndian.AppendUint32(body, 0x20004)
	// max_count = 2
	body = binary.LittleEndian.AppendUint32(body, 2)
	// 2 × { length(2), maxLen(2), bufPtr(4) }
	body = binary.LittleEndian.AppendUint16(body, 10) // 5 chars × 2 bytes
	body = binary.LittleEndian.AppendUint16(body, 10)
	body = binary.LittleEndian.AppendUint32(body, 0x20008)
	body = binary.LittleEndian.AppendUint16(body, 10)
	body = binary.LittleEndian.AppendUint16(body, 10)
	body = binary.LittleEndian.AppendUint32(body, 0x2000c)
	// Deferred buffer for "users"
	body = binary.LittleEndian.AppendUint32(body, 5) // max
	body = binary.LittleEndian.AppendUint32(body, 0) // offset
	body = binary.LittleEndian.AppendUint32(body, 5) // actual
	body = append(body, 'u', 0, 's', 0, 'e', 0, 'r', 0, 's', 0)
	body = dcerpc.AlignBytes(body, 4)
	// Deferred buffer for "wheel"
	body = binary.LittleEndian.AppendUint32(body, 5)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 5)
	body = append(body, 'w', 0, 'h', 0, 'e', 0, 'e', 0, 'l', 0)
	body = dcerpc.AlignBytes(body, 4)
	// Then UsePtr = 0 (we skip it via Tail32 — but we still need to give Tail32
	// something to land on for the NTSTATUS.) Layout the Use pointer + status.
	body = binary.LittleEndian.AppendUint32(body, 0) // UsePtr
	body = binary.LittleEndian.AppendUint32(body, 0) // NTSTATUS

	names, status, err := ParseSamrLookupIdsInDomainResponse(body)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if status != 0 {
		t.Errorf("status = %#x, want 0", status)
	}
	if len(names) != 2 || names[0] != "users" || names[1] != "wheel" {
		t.Errorf("names = %v, want [users wheel]", names)
	}
}
