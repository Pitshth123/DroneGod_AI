// mavprobe — diagnostic: ต่อ SITL ตรงๆ ด้วย gomavlib แล้วพิมพ์ทุก event
//   go run ./cmd/mavprobe -addr 127.0.0.1:5760
package main

import (
	"flag"
	"fmt"
	"log"
	"reflect"
	"time"

	"github.com/bluenviron/gomavlib/v3"
	"github.com/bluenviron/gomavlib/v3/pkg/dialects/ardupilotmega"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:5760", "SITL tcp host:port")
	secs := flag.Int("secs", 10, "run duration")
	flag.Parse()

	node := &gomavlib.Node{
		Endpoints:              []gomavlib.EndpointConf{gomavlib.EndpointTCPClient{Address: *addr}},
		Dialect:                ardupilotmega.Dialect,
		OutVersion:             gomavlib.V2,
		OutSystemID:            255,
		StreamRequestEnable:    true,
		StreamRequestFrequency: 5,
	}
	if err := node.Initialize(); err != nil {
		log.Fatalf("init: %v", err)
	}
	defer node.Close()
	log.Printf("connected to %s — listening %ds", *addr, *secs)

	seen := map[string]int{}
	deadline := time.After(time.Duration(*secs) * time.Second)
	for {
		select {
		case <-deadline:
			fmt.Println("=== message type counts ===")
			for k, v := range seen {
				fmt.Printf("  %-40s %d\n", k, v)
			}
			return
		case evt := <-node.Events():
			switch e := evt.(type) {
			case *gomavlib.EventChannelOpen:
				log.Printf("EVENT ChannelOpen: %v", e.Channel)
			case *gomavlib.EventChannelClose:
				log.Printf("EVENT ChannelClose: %v", e.Channel)
			case *gomavlib.EventParseError:
				log.Printf("EVENT ParseError: %v", e.Error)
			case *gomavlib.EventFrame:
				name := reflect.TypeOf(e.Message()).String()
				if seen[name] == 0 {
					log.Printf("FRAME sysid=%d comp=%d %s", e.SystemID(), e.ComponentID(), name)
				}
				seen[name]++
			default:
				log.Printf("EVENT other: %s", reflect.TypeOf(evt).String())
			}
		}
	}
}
