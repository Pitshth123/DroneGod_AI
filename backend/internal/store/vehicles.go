package store

import "time"

// Vehicle = แถวใน registry (spec §6.1) — vehicle_uuid คือตัวตนถาวร
type Vehicle struct {
	UUID        string
	SystemID    int
	ComponentID int
	DisplayName string
	Serial      string
	FirstSeen   time.Time
	LastSeen    time.Time
}

// UpsertVehicle บันทึก/อัปเดต vehicle
//   - เจอครั้งแรก: insert พร้อม first_seen
//   - เจอซ้ำ (uuid เดิม): อัปเดต display_name/system_id/last_seen, คง first_seen
func (s *Store) UpsertVehicle(uuid string, systemID, componentID int, displayName, serial string) error {
	now := nowUTC().Format(time.RFC3339Nano)
	_, err := s.exec(`
INSERT INTO vehicles(vehicle_uuid, mav_system_id, mav_component_id, display_name, hardware_serial, first_seen, last_seen)
VALUES(?,?,?,?,?,?,?)
ON CONFLICT(vehicle_uuid) DO UPDATE SET
    mav_system_id    = excluded.mav_system_id,
    mav_component_id = excluded.mav_component_id,
    display_name     = excluded.display_name,
    hardware_serial  = excluded.hardware_serial,
    last_seen        = excluded.last_seen`,
		uuid, systemID, componentID, displayName, nullIfEmpty(serial), now, now,
	)
	return err
}

// ListVehicles คืน registry ทั้งหมด เรียงตาม first_seen
func (s *Store) ListVehicles() ([]Vehicle, error) {
	rows, err := s.db.Query(`
SELECT vehicle_uuid, mav_system_id, mav_component_id, display_name,
       COALESCE(hardware_serial, ''), first_seen, last_seen
FROM vehicles ORDER BY first_seen`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []Vehicle
	for rows.Next() {
		var (
			v           Vehicle
			first, last string
		)
		if err := rows.Scan(&v.UUID, &v.SystemID, &v.ComponentID, &v.DisplayName,
			&v.Serial, &first, &last); err != nil {
			return nil, err
		}
		v.FirstSeen, _ = time.Parse(time.RFC3339Nano, first)
		v.LastSeen, _ = time.Parse(time.RFC3339Nano, last)
		out = append(out, v)
	}
	return out, rows.Err()
}

func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}
