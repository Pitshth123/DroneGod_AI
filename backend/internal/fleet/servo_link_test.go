package fleet

import "testing"

// ── Servo (spec: servo.md — DO_SET_SERVO cmd 183, clamp 900–2100) ──

func TestClampServoPWM(t *testing.T) {
	cases := []struct {
		name string
		in   uint32
		want uint32
	}{
		{"ต่ำกว่าขอบล่างถูกดันขึ้น", 500, ServoPWMMin},
		{"ขอบล่างพอดี", 900, 900},
		{"กลางช่วง", 1500, 1500},
		{"ขอบบนพอดี", 2100, 2100},
		{"เกินขอบบนถูกตัดลง", 5000, ServoPWMMax},
		{"ศูนย์ก็ยังอยู่ในขอบ", 0, ServoPWMMin},
	}
	for _, c := range cases {
		if got := ClampServoPWM(c.in); got != c.want {
			t.Errorf("%s: ClampServoPWM(%d) = %d, want %d", c.name, c.in, got, c.want)
		}
	}
}

func TestServoChannelConstants(t *testing.T) {
	// ปุ่ม A ต้องเป็น ch7 และ B เป็น ch8 ตามสเปกที่ผู้ใช้กำหนด
	if ServoChanA != 7 {
		t.Errorf("ServoChanA = %d, want 7", ServoChanA)
	}
	if ServoChanB != 8 {
		t.Errorf("ServoChanB = %d, want 8", ServoChanB)
	}
}

// ── Link quality (spec ข้อ 1: "Datalink ความแรง ไม่มีข้อมูลส่งมาจริง") ──

func TestLinkQualityZeroWhenNoData(t *testing.T) {
	d := &Drone{connected: true}
	// ยังไม่มีทั้ง RADIO_STATUS และ heartbeat → ต้องบอก "ไม่รู้" (0) ไม่ใช่เดาว่าเต็ม
	if q := d.linkQuality(); q != 0 {
		t.Errorf("linkQuality ตอนไม่มีข้อมูล = %d, want 0", q)
	}
}

func TestLinkQualityZeroWhenDisconnected(t *testing.T) {
	d := &Drone{connected: false, hbIntervalMs: 1000}
	if q := d.linkQuality(); q != 0 {
		t.Errorf("linkQuality ตอนหลุด = %d, want 0", q)
	}
}

func TestLinkQualityFromHeartbeat(t *testing.T) {
	// heartbeat ถี่ (1000ms) ควรได้คุณภาพสูง, ห่างมาก (2800ms) ควรได้ต่ำ
	fast := &Drone{connected: true, hbIntervalMs: 1000}
	slow := &Drone{connected: true, hbIntervalMs: 2800}
	qf, qs := fast.linkQuality(), slow.linkQuality()
	if qf <= qs {
		t.Errorf("heartbeat ถี่ควรคุณภาพดีกว่า: fast=%d slow=%d", qf, qs)
	}
	if qf != 100 {
		t.Errorf("heartbeat 1000ms ควรได้ 100, ได้ %d", qf)
	}
}

func TestLinkQualityDropRatePenalty(t *testing.T) {
	base := &Drone{connected: true, hbIntervalMs: 1000}
	lossy := &Drone{connected: true, hbIntervalMs: 1000, dropRate: 300} // 30%
	if lossy.linkQuality() >= base.linkQuality() {
		t.Errorf("drop rate ต้องหักคุณภาพลง: base=%d lossy=%d",
			base.linkQuality(), lossy.linkQuality())
	}
}

func TestLinkQualityClampedTo100(t *testing.T) {
	// heartbeat เร็วผิดปกติไม่ควรทำให้เกิน 100
	d := &Drone{connected: true, hbIntervalMs: 50}
	if q := d.linkQuality(); q > 100 {
		t.Errorf("linkQuality = %d, ต้องไม่เกิน 100", q)
	}
}

func TestRssiNotValidWithoutRadio(t *testing.T) {
	// ไม่มีวิทยุ SiK (SITL/USB) → rssiFresh ต้องเป็น false เพื่อให้ UI โชว์ N/A
	// ไม่ใช่ตีความ 0 dBm ว่าสัญญาณเต็ม
	d := &Drone{connected: true}
	if d.rssiFresh() {
		t.Error("rssiFresh ต้องเป็น false เมื่อไม่เคยได้ RADIO_STATUS")
	}
}
