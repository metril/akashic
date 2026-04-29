package samr

// SAMR opnums (per [MS-SAMR] §3.1.5.* indexed table).
const (
	OpnumSamrCloseHandle       uint16 = 1
	OpnumSamrOpenDomain        uint16 = 7
	OpnumSamrLookupIdsInDomain uint16 = 18
	OpnumSamrOpenUser          uint16 = 34
	OpnumSamrGetGroupsForUser  uint16 = 39
	OpnumSamrConnect5          uint16 = 64
)

// SAMR access masks (per [MS-SAMR] §2.2.1.*).
const (
	// SamrConnect5
	SamServerConnect      uint32 = 0x00000001
	SamServerLookupDomain uint32 = 0x00000020
	SamServerAllAccess    uint32 = 0x000F003F

	// SamrOpenDomain
	DomainLookup       uint32 = 0x00000200
	DomainListAccounts uint32 = 0x00000004
	DomainReadGeneric  uint32 = 0x00020205

	// SamrOpenUser
	UserReadGeneric          uint32 = 0x0002031A
	UserReadGroupInformation uint32 = 0x00000100
)
