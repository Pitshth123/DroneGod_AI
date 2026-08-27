package store

import (
	"log"
	"sync"
	"sync/atomic"
	"time"
)

// AsyncIndexer เขียน audit index ลง SQLite แบบ "ไม่บล็อกเด็ดขาด"
//
// เหตุผลด้านความปลอดภัย (spec §5.9, §3.4): DB ห้ามอยู่ใน flight-command critical path
// ดังนั้น Index() แค่หยอด entry ลง buffered channel แบบ non-blocking:
//   - buffer เต็ม → ทิ้ง entry (นับไว้ที่ dropped) ไม่รอ, ไม่ error
//   - DB ช้า/ล่ม → กระทบแค่ index ที่ query ได้ ไม่กระทบ audit ตัวจริง (JSONL) และไม่ค้าง command
type AsyncIndexer struct {
	store    *Store
	ch       chan map[string]any
	done     chan struct{}
	wg       sync.WaitGroup
	dropped  atomic.Int64
	written  atomic.Int64
	closeOne sync.Once

	mu       sync.RWMutex
	flightID string
}

// NewAsyncIndexer สร้าง indexer + start goroutine (bufSize = จำนวน entry ที่ buffer ได้)
func (s *Store) NewAsyncIndexer(bufSize int) *AsyncIndexer {
	if bufSize <= 0 {
		bufSize = 1024
	}
	ai := &AsyncIndexer{
		store: s,
		ch:    make(chan map[string]any, bufSize),
		done:  make(chan struct{}),
	}
	ai.wg.Add(1)
	go ai.run()
	s.idx = ai // ให้ Store.Close() ปิด indexer ให้ด้วย
	return ai
}

// SetFlightSession ตั้ง flight_session_id ที่จะแนบกับทุก entry ต่อจากนี้
func (ai *AsyncIndexer) SetFlightSession(id string) {
	ai.mu.Lock()
	ai.flightID = id
	ai.mu.Unlock()
}

// Index — implements audit.Indexer. ต้องไม่บล็อก (เรียกจาก command path)
func (ai *AsyncIndexer) Index(entry map[string]any) {
	if ai == nil {
		return
	}
	select {
	case ai.ch <- entry:
	default:
		ai.dropped.Add(1) // buffer เต็ม → ทิ้ง ไม่รอ
	}
}

// Dropped / Written สำหรับ diagnostics และเทสต์
func (ai *AsyncIndexer) Dropped() int64 { return ai.dropped.Load() }
func (ai *AsyncIndexer) Written() int64 { return ai.written.Load() }

func (ai *AsyncIndexer) run() {
	defer ai.wg.Done()
	for {
		select {
		case <-ai.done:
			ai.drain() // flush ที่ค้างก่อนปิด (best-effort)
			return
		case entry := <-ai.ch:
			ai.persist(entry)
		}
	}
}

// drain เขียน entry ที่ยังค้างใน channel ตอนปิด (ไม่บล็อกรอ entry ใหม่)
func (ai *AsyncIndexer) drain() {
	for {
		select {
		case entry := <-ai.ch:
			ai.persist(entry)
		default:
			return
		}
	}
}

func (ai *AsyncIndexer) persist(entry map[string]any) {
	ai.mu.RLock()
	fid := ai.flightID
	ai.mu.RUnlock()

	ts, _ := entry["ts"].(string)
	if ts == "" {
		ts = nowUTC().Format(time.RFC3339Nano)
	}
	entryType, _ := entry["type"].(string)

	_, err := ai.store.exec(`
INSERT INTO audit_index(ts, flight_session_id, entry_type, drone_id, command, category, allowed, reason, result_code, request_id)
VALUES(?,?,?,?,?,?,?,?,?,?)`,
		ts,
		nullIfEmpty(fid),
		entryType,
		anyIntPtr(entry["drone_id"]),
		anyStrPtr(entry["command"]),
		anyStrPtr(entry["category"]),
		anyBoolPtr(entry["allowed"]),
		anyStrPtr(firstNonEmpty(entry["reason"], entry["message"])), // command→reason, event→message
		anyIntPtr(entry["result_code"]),
		anyStrPtr(entry["request_id"]), // §16.1
	)
	if err != nil {
		// index ล้มเหลวไม่ใช่เรื่องคอขาดบาดตาย — log แล้วไปต่อ (JSONL คือ source of truth)
		log.Printf("[store] audit index write failed: %v", err)
		ai.dropped.Add(1)
		return
	}
	ai.written.Add(1)
}

// Close หยุด goroutine และรอ flush เสร็จ (idempotent)
func (ai *AsyncIndexer) Close() {
	if ai == nil {
		return
	}
	ai.closeOne.Do(func() {
		close(ai.done)
		ai.wg.Wait()
	})
}

// ── AuditFilter/AuditRow: query index ──

type AuditFilter struct {
	DroneID         *uint32
	FlightSessionID string
	Limit           int
}

type AuditRow struct {
	Ts              time.Time
	FlightSessionID string
	Type            string
	DroneID         *uint32
	Command         string
	Category        string
	Allowed         *bool
	Reason          string
	ResultCode      *int32
	RequestID       string
}

// QueryAudit ดึง audit index ตาม filter (ล่าสุดก่อน)
func (s *Store) QueryAudit(f AuditFilter) ([]AuditRow, error) {
	q := `SELECT ts, COALESCE(flight_session_id,''), entry_type, drone_id, COALESCE(command,''),
	             COALESCE(category,''), allowed, COALESCE(reason,''), result_code, COALESCE(request_id,'')
	      FROM audit_index WHERE 1=1`
	var args []any
	if f.DroneID != nil {
		q += " AND drone_id = ?"
		args = append(args, *f.DroneID)
	}
	if f.FlightSessionID != "" {
		q += " AND flight_session_id = ?"
		args = append(args, f.FlightSessionID)
	}
	q += " ORDER BY id DESC"
	if f.Limit > 0 {
		q += " LIMIT ?"
		args = append(args, f.Limit)
	}
	rows, err := s.db.Query(q, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []AuditRow
	for rows.Next() {
		var (
			r       AuditRow
			ts      string
			droneID *int64
			allowed *int64
			code    *int64
		)
		if err := rows.Scan(&ts, &r.FlightSessionID, &r.Type, &droneID, &r.Command,
			&r.Category, &allowed, &r.Reason, &code, &r.RequestID); err != nil {
			return nil, err
		}
		r.Ts, _ = time.Parse(time.RFC3339Nano, ts)
		if droneID != nil {
			d := uint32(*droneID)
			r.DroneID = &d
		}
		if allowed != nil {
			b := *allowed != 0
			r.Allowed = &b
		}
		if code != nil {
			c := int32(*code)
			r.ResultCode = &c
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// ── helpers: แปลง any → pointer สำหรับ nullable column ──

// firstNonEmpty คืน string แรกที่ไม่ว่างจาก candidates (ไม่งั้น "")
func firstNonEmpty(cands ...any) any {
	for _, c := range cands {
		if s, ok := c.(string); ok && s != "" {
			return s
		}
	}
	return ""
}

func anyStrPtr(v any) any {
	if s, ok := v.(string); ok && s != "" {
		return s
	}
	return nil
}

func anyBoolPtr(v any) any {
	if b, ok := v.(bool); ok {
		if b {
			return 1
		}
		return 0
	}
	return nil
}

func anyIntPtr(v any) any {
	switch n := v.(type) {
	case uint32:
		return int64(n)
	case int32:
		return int64(n)
	case int:
		return int64(n)
	case int64:
		return n
	default:
		return nil
	}
}
