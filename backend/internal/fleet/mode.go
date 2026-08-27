package fleet

import pb "github.com/swarmgod/backend/gen/swarmgod/v1"

// ArduCopter custom_mode (จาก HEARTBEAT) → ชื่อ + proto enum
// map ตรงกับ Drone.ARDUPILOT_MODES เดิมใน GCS_1
var ardupilotModes = map[uint32]struct {
	name string
	enum pb.FlightMode
}{
	0:  {"STABILIZE", pb.FlightMode_FLIGHT_MODE_STABILIZE},
	1:  {"ACRO", pb.FlightMode_FLIGHT_MODE_ACRO},
	2:  {"ALT_HOLD", pb.FlightMode_FLIGHT_MODE_ALT_HOLD},
	3:  {"AUTO", pb.FlightMode_FLIGHT_MODE_AUTO},
	4:  {"GUIDED", pb.FlightMode_FLIGHT_MODE_GUIDED},
	5:  {"LOITER", pb.FlightMode_FLIGHT_MODE_LOITER},
	6:  {"RTL", pb.FlightMode_FLIGHT_MODE_RTL},
	7:  {"CIRCLE", pb.FlightMode_FLIGHT_MODE_CIRCLE},
	9:  {"LAND", pb.FlightMode_FLIGHT_MODE_LAND},
	16: {"POSHOLD", pb.FlightMode_FLIGHT_MODE_POSHOLD},
	17: {"BRAKE", pb.FlightMode_FLIGHT_MODE_BRAKE},
}

func decodeMode(customMode uint32) (string, pb.FlightMode) {
	if m, ok := ardupilotModes[customMode]; ok {
		return m.name, m.enum
	}
	return "UNKNOWN", pb.FlightMode_FLIGHT_MODE_UNKNOWN
}

// modeNumber: proto FlightMode enum → ArduCopter custom_mode number
func modeNumber(mode pb.FlightMode) (uint32, bool) {
	for num, info := range ardupilotModes {
		if info.enum == mode {
			return num, true
		}
	}
	return 0, false
}

// ModeNumber exported สำหรับ command package
func ModeNumber(mode pb.FlightMode) (uint32, bool) { return modeNumber(mode) }

// ModeName คืนชื่อโหมดจาก custom_mode — ชื่อเดียวกับที่ SafetyState.Mode ใช้
func ModeName(customMode uint32) string {
	name, _ := decodeMode(customMode)
	return name
}
