"""
grpc_client.py — เชื่อม Python cockpit เข้ากับ Go core
- CoreClient: unary calls (Connect, Disconnect, commands ภายหลัง)
- TelemetryThread: subscribe telemetry stream → emit Qt signal ใน main thread
"""
import collections
import os
import threading
import uuid

import grpc
from PyQt5.QtCore import QThread, pyqtSignal

from . import rpc
from .command_correlation import current_command_correlation
from .rpc import telemetry_pb2, command_pb2, swarm_pb2, service_pb2, service_pb2_grpc


class _ClientCallDetails(
        collections.namedtuple(
            "_ClientCallDetails",
            ("method", "timeout", "metadata", "credentials",
             "wait_for_ready", "compression")),
        grpc.ClientCallDetails):
    pass


class _BearerAuthInterceptor(grpc.UnaryUnaryClientInterceptor,
                             grpc.UnaryStreamClientInterceptor):
    """แนบ authorization: Bearer <token> ให้ทุก call (spec §8/§15)

    core บังคับ token เมื่อ profile=production; ใน dev ถ้าไม่ตั้ง token ก็ไม่ต้องใช้ interceptor นี้
    """

    def __init__(self, token: str):
        self._meta = ("authorization", "Bearer " + token)

    def _with_auth(self, details):
        md = list(details.metadata or [])
        md.append(self._meta)
        return _ClientCallDetails(
            details.method, details.timeout, md, details.credentials,
            getattr(details, "wait_for_ready", None),
            getattr(details, "compression", None))

    def intercept_unary_unary(self, continuation, details, request):
        return continuation(self._with_auth(details), request)

    def intercept_unary_stream(self, continuation, details, request):
        return continuation(self._with_auth(details), request)


class _CommandCorrelationInterceptor(grpc.UnaryUnaryClientInterceptor):
    """Attach V3-S08 command identity to operator unary RPCs.

    CommandGateway binds the current ticket only around ``invoke()``.  Calls
    outside that boundary (telemetry/lifecycle/background reads) therefore carry
    no correlation metadata and remain behaviorally unchanged.
    """

    _KEYS = {
        "operation_id": "x-swarmgod-operation-id",
        "command_id": "x-swarmgod-command-id",
        "attempt_id": "x-swarmgod-attempt-id",
    }

    def intercept_unary_unary(self, continuation, details, request):
        corr = current_command_correlation()
        # Strip every caller-provided reserved key even when there is no Gateway
        # context. Calls outside Gateway must carry no command correlation, and a
        # stale metadata copy must never masquerade as the current attempt.
        reserved = set(self._KEYS.values())
        original_md = list(details.metadata or ())
        md = [(k, v) for (k, v) in original_md if str(k).lower() not in reserved]
        if corr:
            # IDs bound by CommandGateway are the sole source of truth for this
            # attempt and cannot authorize/bypass Core safety policy.
            for field, key in self._KEYS.items():
                value = str(corr.get(field) or "").strip()
                if value:
                    md.append((key, value))
        elif md == original_md:
            return continuation(details, request)
        wrapped = _ClientCallDetails(
            details.method, details.timeout, md, details.credentials,
            getattr(details, "wait_for_ready", None),
            getattr(details, "compression", None))
        return continuation(wrapped, request)

# certs/ อยู่ที่ราก SwarmGod (grpc_client.py -> core -> swarmgod_gui -> frontend -> SwarmGod)
_CERTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "certs")


def _read(p):
    with open(p, "rb") as f:
        return f.read()


def format_rpc_error(exc) -> str:
    """Return a compact operator-facing gRPC error instead of ``_InactiveRpcError`` repr."""
    if isinstance(exc, grpc.RpcError):
        try:
            code = exc.code()
            code_name = getattr(code, "name", None) or str(code).rsplit(".", 1)[-1]
        except Exception:
            code_name = "RPC_ERROR"
        try:
            details = (exc.details() or "").strip()
        except Exception:
            details = ""
        return f"{code_name}: {details}" if details else code_name
    return str(exc)


class CoreClient:
    """หุ้ม gRPC stub — mTLS ถ้ามี cert, ไม่งั้น insecure (dev)"""

    def __init__(self, addr: str = "127.0.0.1:50051"):
        self.addr = addr
        ca = os.path.join(_CERTS, "ca.crt")
        crt = os.path.join(_CERTS, "client.crt")
        key = os.path.join(_CERTS, "client.key")
        if all(os.path.exists(p) for p in (ca, crt, key)):
            creds = grpc.ssl_channel_credentials(
                root_certificates=_read(ca), private_key=_read(key),
                certificate_chain=_read(crt))
            # cert SAN มี localhost → override ให้ TLS verify ตรง
            self.channel = grpc.secure_channel(
                addr, creds, options=(("grpc.ssl_target_name_override", "localhost"),))
            self.secure = True
        else:
            self.channel = grpc.insecure_channel(addr)
            self.secure = False
        # แนบ session token ถ้าตั้ง env SWARMGOD_TOKEN (ออกจาก `swarmadmin session new`)
        # และแนบ V3-S08 correlation metadata เฉพาะ unary call ที่ถูกเรียกอยู่
        # ภายใน CommandGateway dispatch.  Correlation เป็น observability เท่านั้น
        # และไม่มีผลต่อ auth / authority / safety / Core idempotency.
        token = os.getenv("SWARMGOD_TOKEN", "").strip()
        self.authenticated = bool(token)
        interceptors = []
        if token:
            interceptors.append(_BearerAuthInterceptor(token))
        interceptors.append(_CommandCorrelationInterceptor())
        self.channel = grpc.intercept_channel(self.channel, *interceptors)
        self.stub = service_pb2_grpc.SwarmGodServiceStub(self.channel)

    def close(self):
        """Release the gRPC channel owned by this client.

        GroundStation owns the CoreClient it creates, so shutdown must close the
        channel after telemetry/event streams are stopped.  This prevents native
        gRPC connectivity poll threads from surviving repeated cockpit lifecycles
        (notably the headless UI test suite) without changing command semantics.
        """
        try:
            self.channel.close()
        except Exception:
            pass

    def connect_drone(self, drone_id: int, name: str, host: str, port: int,
                      protocol: str = "tcp"):
        req = command_pb2.ConnectRequest(
            drone_id=drone_id, name=name, protocol=protocol, host=host, port=port)
        return self.stub.Connect(req, timeout=10)

    def disconnect_drone(self, drone_id: int):
        return self.stub.Disconnect(command_pb2.DisconnectRequest(drone_id=drone_id))

    # ── flight commands (ผ่าน core → safety envelope + audit) ──
    def _target(self, ids):
        return command_pb2.Target(drone_ids=ids)

    @staticmethod
    def _rid():
        """UUID ต่อ 1 การกดคำสั่ง → core dedup ถ้ากดซ้ำ/retry (spec §8.2)"""
        return uuid.uuid4().hex

    def arm(self, ids, force=False):
        return self.stub.Arm(command_pb2.ArmRequest(
            target=self._target(ids), force=force, request_id=self._rid()), timeout=15)

    def disarm(self, ids, confirmed=False):
        return self.stub.Disarm(command_pb2.DisarmRequest(
            target=self._target(ids), confirmed=confirmed, request_id=self._rid()), timeout=15)

    def takeoff(self, ids, alt, confirmed=False):
        # timeout ต้องมากกว่างบลำดับฝั่ง core (GUIDED→arm→takeoff = 90s)
        # ไม่งั้น client ตัดสายก่อน arm สำเร็จ → takeoff ไม่ขึ้นเลย
        return self.stub.Takeoff(command_pb2.TakeoffRequest(
            target=self._target(ids), altitude=alt, confirmed=confirmed,
            request_id=self._rid()), timeout=120)

    def land(self, ids):
        return self.stub.Land(command_pb2.LandRequest(
            target=self._target(ids), request_id=self._rid()), timeout=15)

    def rtl(self, ids):
        return self.stub.ReturnToLaunch(command_pb2.RtlRequest(
            target=self._target(ids), request_id=self._rid()), timeout=15)

    def hold(self, ids):
        return self.stub.Hold(command_pb2.HoldRequest(
            target=self._target(ids), request_id=self._rid()), timeout=15)

    def set_mode(self, ids, mode):
        return self.stub.SetMode(command_pb2.SetModeRequest(
            target=self._target(ids), mode=mode, request_id=self._rid()), timeout=15)

    # ── servo (กลไกปล่อยของ) — A=ch7, B=ch8 · spec: servo.md ──
    # ใช้ RC_CHANNELS_OVERRIDE ที่ core เพื่อให้สั่งได้ทั้งจาก cockpit และรีโมท
    # (ดู docs/SERVO_DATALINK.md §2) · Servo RPC เป็น per-drone จึงวนส่งทีละลำ
    def servo_set(self, drone_id, channel, pwm):
        """override ช่องนั้นด้วย PWM ที่ระบุ — core ส่งซ้ำให้เองจนกว่าจะสั่งปล่อย"""
        return self.stub.Servo(command_pb2.ServoRequest(
            drone_id=int(drone_id), action=command_pb2.ServoRequest.SET,
            channel=int(channel), pwm=int(pwm)), timeout=10)

    def servo_release(self, drone_id, channel):
        """เลิก override ช่องนั้น → คืนช่องให้สวิตช์จริงบนรีโมทคุมต่อ"""
        return self.stub.Servo(command_pb2.ServoRequest(
            drone_id=int(drone_id), action=command_pb2.ServoRequest.RELEASE,
            channel=int(channel)), timeout=10)

    def servo_reset(self, drone_id):
        """เลิก override ทุกช่อง — คืนการควบคุมให้รีโมททั้งหมด"""
        return self.stub.Servo(command_pb2.ServoRequest(
            drone_id=int(drone_id), action=command_pb2.ServoRequest.RESET), timeout=10)

    def kill(self, ids, confirmed=True):
        """Emergency motor-cut (ต้อง confirm เสมอ — spec §safety)"""
        return self.stub.Kill(command_pb2.KillRequest(
            target=self._target(ids), confirmed=confirmed, request_id=self._rid()), timeout=10)

    # ── manual control (ปุ่มทิศทาง/รีโมท) ──
    def rc_move(self, ids, direction, speed, yaw_rate=30.0):
        return self.stub.RcMove(command_pb2.RcMoveRequest(
            target=self._target(ids), dir=direction, speed=speed, yaw_rate=yaw_rate), timeout=4)

    def stop_all(self, ids):
        # core หยุด swarm loop แล้วสลับแต่ละลำเข้า verified hold; อาจต้องรอ mode ACK
        return self.stub.StopAll(command_pb2.StopAllRequest(target=self._target(ids)), timeout=10)

    def goto(self, drone_id, lat, lon, alt):
        return self.stub.Goto(command_pb2.GotoRequest(
            drone_id=drone_id, lat=lat, lon=lon, alt=alt, request_id=self._rid()), timeout=15)

    def param_set(self, drone_id, param_id, value):
        """ตั้งพารามิเตอร์ที่ FC (core ปฏิเสธถ้า armed อยู่)"""
        return self.stub.ParamSet(command_pb2.ParamSetRequest(
            drone_id=int(drone_id), param_id=str(param_id),
            value=float(value)), timeout=10)

    def change_alt(self, ids, alt):
        return self.stub.ChangeAlt(command_pb2.ChangeAltRequest(
            target=self._target(ids), altitude=alt, request_id=self._rid()), timeout=15)

    # ── swarm ──
    def swarm_start(self):
        return self.stub.SwarmControl(swarm_pb2.SwarmControlRequest(
            action=swarm_pb2.SwarmControlRequest.START), timeout=15)

    def swarm_stop(self):
        return self.stub.SwarmControl(swarm_pb2.SwarmControlRequest(
            action=swarm_pb2.SwarmControlRequest.STOP), timeout=15)

    def swarm_return(self, ids=None, base_alt=15.0, gap=5.0):
        return self.stub.SwarmControl(swarm_pb2.SwarmControlRequest(
            action=swarm_pb2.SwarmControlRequest.RETURN,
            drone_ids=[int(i) for i in (ids or [])],
            return_base_alt=float(base_alt), return_gap=float(gap),
            request_id=self._rid()), timeout=15)

    def swarm_config(self, spacing, formation=None, heading_mode=None):
        cfg = swarm_pb2.SwarmConfig(spacing=spacing)
        if formation is not None:
            cfg.formation = formation
        if heading_mode is not None:
            cfg.heading_mode = heading_mode
        return self.stub.SetSwarmConfig(cfg, timeout=15)

    def swarm_state(self):
        return self.stub.GetSwarmState(service_pb2.SwarmStateRequest(), timeout=5)

    # ── mission shadow/control boundary (V2 F3/F4) ──
    def start_mission(self, plan_proto, operation_id):
        return self.stub.StartMission(rpc.mission_pb2.StartMissionRequest(
            plan=plan_proto, operation_id=str(operation_id)), timeout=10)

    def cancel_mission(self, run_id, request_id=None):
        return self.stub.CancelMission(rpc.mission_pb2.CancelMissionRequest(
            run_id=int(run_id), request_id=str(request_id or self._rid())), timeout=10)

    def get_mission_state(self):
        return self.stub.GetMissionState(rpc.mission_pb2.GetMissionStateRequest(), timeout=5)

    def set_leader(self, leader_id, followers=None):
        """ตั้งหัวขบวน (Head/Leader) ที่ core — followers = list ของ
        (follower_id, north, east, up); เว้นว่างได้ให้ core คำนวณ slot เอง"""
        req = swarm_pb2.SetLeaderRequest(leader_id=int(leader_id))
        for f in (followers or []):
            fid, n, e, up = f
            req.followers.append(swarm_pb2.FormationOffset(
                follower_id=int(fid), north=float(n), east=float(e), up=float(up)))
        return self.stub.SetLeader(req, timeout=10)

    def set_geofence(self, points):
        # points = list ของ (lat, lon); ว่าง = ปิด fence
        verts = [command_pb2.GeoVertex(lat=la, lon=lo) for (la, lo) in points]
        return self.stub.SetGeofence(command_pb2.SetGeofenceRequest(
            points=verts, confirmed=True, request_id=self._rid()), timeout=10)


class _StreamThread(QThread):
    """Base lifecycle for a gRPC server-streaming subscription QThread.

    Shutdown is deterministic.  ``run()`` opens the stream and ``stop()`` cancels
    it under one lock, so the two can never interleave into the crash window that
    a naive ``self._call = ...; for x in self._call`` pattern allows:

    * If ``stop()`` wins the race it sets ``_stopped`` before the stream is
      opened, and ``run()`` returns without ever opening a stream.
    * If ``run()`` wins it stores ``_call`` first, so ``stop()`` sees a live call
      and cancels it; the iterator then exits with CANCELLED.

    The stream iterator is therefore never left blocked inside cygrpc when the
    owning channel is closed — the previous behaviour that produced a native
    access violation (0xC0000005) during headless GroundStation teardown.
    """

    def __init__(self, stub, parent=None):
        super().__init__(parent)
        self.stub = stub
        self._lock = threading.Lock()
        self._running = True
        self._stopped = False   # stop requested — set even before _call exists
        self._call = None

    # ── subclass hooks ──
    def _open(self):
        raise NotImplementedError

    def _emit(self, msg):
        raise NotImplementedError

    def _on_rpc_error(self, e):
        pass

    def _on_other_error(self, e):
        pass

    @staticmethod
    def _safe_cancel(call):
        try:
            call.cancel()
        except Exception:
            pass

    def run(self):
        with self._lock:
            if self._stopped:
                return                       # stop() won the race — never open a stream
            call = self._open()
            self._call = call
            if self._stopped:                # stop() raced in during _open(): cancel now
                self._safe_cancel(call)
        try:
            for msg in call:
                if not self._running:
                    break
                self._emit(msg)
        except grpc.RpcError as e:
            self._on_rpc_error(e)
        except Exception as e:  # pragma: no cover
            self._on_other_error(e)
        finally:
            with self._lock:
                self._call = None

    def stop(self):
        """Request shutdown.  Idempotent and safe before ``run()`` opens the stream."""
        with self._lock:
            self._running = False
            self._stopped = True
            call = self._call
        if call is not None:
            self._safe_cancel(call)

    def shutdown(self, timeout_ms=2000):
        """Stop and join.  Returns True only once the worker has fully exited so
        the caller can prove it is safe to close the owning gRPC channel."""
        self.stop()
        if not self.isRunning():
            return True
        return self.wait(timeout_ms)


class TelemetryThread(_StreamThread):
    """subscribe telemetry stream ใน thread แยก → emit signal ต่อ 1 snapshot"""
    telemetry = pyqtSignal(object)   # telemetry_pb2.Telemetry
    stream_error = pyqtSignal(str)

    def _open(self):
        return self.stub.SubscribeTelemetry(
            telemetry_pb2.SubscribeTelemetryRequest())

    def _emit(self, msg):
        self.telemetry.emit(msg)

    def _on_rpc_error(self, e):
        if self._running:
            self.stream_error.emit(f"{e.code().name}: {e.details()}")

    def _on_other_error(self, e):
        if self._running:
            self.stream_error.emit(str(e))


class EventThread(_StreamThread):
    """subscribe event/alarm stream (failsafe, failover, geofence) → emit signal"""
    event = pyqtSignal(object)  # pb.Event

    def _open(self):
        return self.stub.SubscribeEvents(service_pb2.EventSubscribeRequest())

    def _emit(self, msg):
        self.event.emit(msg)
