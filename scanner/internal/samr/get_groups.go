package samr

import "github.com/akashic-project/akashic/scanner/internal/dcerpc"

// BuildSamrGetGroupsForUserRequest encodes SamrGetGroupsForUser (opnum 39).
//
// Wire format:
//
//	[20]byte UserHandle
func BuildSamrGetGroupsForUserRequest(callID uint32, user Handle) []byte {
	return dcerpc.WrapRequest(callID, OpnumSamrGetGroupsForUser, user[:])
}

// GroupMembership is one (RID, Attributes) pair from SAMPR_GROUP_MEMBERSHIP.
type GroupMembership struct {
	RID        uint32
	Attributes uint32
}

// ParseSamrGetGroupsForUserResponse parses the response body.
//
// Wire format:
//
//	uint32 GroupsPtr (referent for the SAMPR_GET_GROUPS_BUFFER)
//	if non-NULL:
//	  uint32 MembershipCount
//	  uint32 GroupsArrayPtr (referent)
//	  if GroupsArrayPtr non-NULL:
//	    uint32 conformance = MembershipCount
//	    repeat MembershipCount times:
//	      uint32 RID
//	      uint32 Attributes
//	uint32 NTSTATUS
func ParseSamrGetGroupsForUserResponse(body []byte) ([]GroupMembership, uint32, error) {
	r := dcerpc.NewReader(body)
	bufPtr := r.U32()
	if bufPtr == 0 {
		// No buffer returned — read tail status, return empty list.
		return nil, r.Tail32(), nil
	}
	count := r.U32()
	arrPtr := r.U32()
	if arrPtr == 0 {
		return nil, r.Tail32(), nil
	}
	r.U32() // conformance count (== count)
	out := make([]GroupMembership, count)
	for i := uint32(0); i < count; i++ {
		out[i].RID = r.U32()
		out[i].Attributes = r.U32()
	}
	status := r.Tail32()
	return out, status, nil
}
