// Package mavlink — ห่อ gomavlib เป็น connection ต่อโดรน 1 ลำ
// อ่าน frame แล้วส่งต่อให้ handler (fleet.Drone.HandleMessage)
// แทน drone.py connect + _recv_thread เดิม (แต่เป็น goroutine เดียวสะอาดๆ)
package mavlink

import (
	"context"
	"fmt"

	"github.com/bluenviron/gomavlib/v3"
	"github.com/bluenviron/gomavlib/v3/pkg/dialects/ardupilotmega"
	"github.com/bluenviron/gomavlib/v3/pkg/frame"
	"github.com/bluenviron/gomavlib/v3/pkg/message"
)

// FrameHandler ถูกเรียกทุกครั้งที่ได้ MAVLink message (จาก goroutine ของ conn)
type FrameHandler func(sysID byte, msg message.Message)

// Conn = 1 การเชื่อมต่อ MAVLink (1 โดรน / 1 endpoint)
type Conn struct {
	node *gomavlib.Node
	addr string
}

// Dial สร้าง node แล้ว initialize (ยังไม่เริ่มอ่าน — เรียก Run ต่อ)
//   protocol: "tcp" | "udp"  (SITL ปกติ tcp 127.0.0.1:5760)
//   outSystemID: sysid ของ GCS (255 ตาม convention)
//   signKey: MAVLink signing key 32 bytes (nil = ไม่เซ็น). strict = reject frame ที่ไม่เซ็น (ห้ามใช้กับ SITL ที่ไม่ตั้ง signing)
func Dial(protocol, host string, port int, outSystemID byte, signKey []byte, strict bool) (*Conn, error) {
	addr := fmt.Sprintf("%s:%d", host, port)
	var ep gomavlib.EndpointConf
	switch protocol {
	case "tcp":
		ep = gomavlib.EndpointTCPClient{Address: addr}
	case "udp":
		ep = gomavlib.EndpointUDPClient{Address: addr}
	default:
		return nil, fmt.Errorf("unknown protocol %q (use tcp|udp)", protocol)
	}

	node := &gomavlib.Node{
		Endpoints:              []gomavlib.EndpointConf{ep},
		Dialect:                ardupilotmega.Dialect,
		OutVersion:             gomavlib.V2,
		OutSystemID:            outSystemID,
		StreamRequestEnable:    true, // auto-request ArduPilot data streams
		StreamRequestFrequency: 5,    // Hz
	}
	if len(signKey) == 32 {
		node.OutKey = frame.NewV2Key(signKey) // เซ็น frame ที่ส่งออก (HMAC-SHA256)
		if strict {
			node.InKey = frame.NewV2Key(signKey) // + reject incoming ที่ไม่เซ็น/sig ผิด
		}
	}
	if err := node.Initialize(); err != nil {
		return nil, fmt.Errorf("mavlink init %s: %w", addr, err)
	}
	return &Conn{node: node, addr: addr}, nil
}

// Run อ่าน event loop จนกว่า ctx จะถูกยกเลิก แล้วปิด node
func (c *Conn) Run(ctx context.Context, h FrameHandler) {
	defer c.node.Close()
	events := c.node.Events()
	for {
		select {
		case <-ctx.Done():
			return
		case evt, ok := <-events:
			if !ok {
				return
			}
			if ff, isFrame := evt.(*gomavlib.EventFrame); isFrame {
				h(ff.SystemID(), ff.Message())
			}
		}
	}
}

// Send ส่ง MAVLink message ออกทุก channel (สำหรับคำสั่ง Phase 3)
func (c *Conn) Send(m message.Message) error { return c.node.WriteMessageAll(m) }

func (c *Conn) Addr() string { return c.addr }
