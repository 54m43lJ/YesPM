"""协议层测试：JSON-RPC 信封、错误映射、stdio 帧化、in-process 传输。"""
from __future__ import annotations

import io
import json

import pytest

from yespm_backend.engine.errors import ProtocolError
from yespm_backend.protocol import jsonrpc
from yespm_backend.protocol.stdio import run_stdio
from yespm_backend.protocol.transport import InProcessTransport, serve_request


# ---------------------------------------------------------------- 信封构造

def test_make_request_envelope():
    req = jsonrpc.make_request(7, "input/send", {"text": "x"})
    assert req == {"jsonrpc": "2.0", "id": 7, "method": "input/send", "params": {"text": "x"}}


def test_make_response_envelope():
    resp = jsonrpc.make_response(7, {"accepted": True})
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 7
    assert resp["result"] == {"accepted": True}
    assert "error" not in resp


def test_make_notification_has_no_id():
    note = jsonrpc.make_notification("session/status", {"stage": "interview"})
    assert "id" not in note
    assert note["method"] == "session/status"


def test_make_error_envelope_with_data():
    err = jsonrpc.make_error(1, 4103, "操作当前不可用", {"stage": "polish"})
    assert err["error"] == {"code": 4103, "message": "操作当前不可用", "data": {"stage": "polish"}}


def test_error_from_protocol_error():
    e = ProtocolError(4001, "会话不存在")
    resp = jsonrpc.error_from_exception(1, e)
    assert resp["error"]["code"] == 4001


def test_error_from_unknown_exception_is_5901():
    resp = jsonrpc.error_from_exception(1, ValueError("boom"))
    assert resp["error"]["code"] == 5901


# ---------------------------------------------------------------- serve_request

def test_serve_request_returns_response_with_id(backend):
    req = jsonrpc.make_request("r1", "session/list", {})
    resp = serve_request(backend, req, lambda e, p: None)
    assert resp["id"] == "r1"
    assert "result" in resp and "sessions" in resp["result"]


def test_serve_request_maps_protocol_error(backend):
    req = jsonrpc.make_request("r2", "session/resume", {"session_id": "nope"})
    resp = serve_request(backend, req, lambda e, p: None)
    assert resp["error"]["code"] == 4001


def test_serve_request_unknown_method(backend):
    req = jsonrpc.make_request("r3", "bogus", {})
    resp = serve_request(backend, req, lambda e, p: None)
    assert resp["error"]["code"] == 4603


def test_serve_request_invalid_no_method(backend):
    req = {"jsonrpc": "2.0", "id": "r4"}
    resp = serve_request(backend, req, lambda e, p: None)
    assert resp["error"]["code"] == 4602


# ---------------------------------------------------------------- 事件回流

def test_events_delivered_via_emit_during_request(backend):
    events = []
    req = jsonrpc.make_request("r5", "session/create", {"brief": "x"})
    serve_request(backend, req, lambda e, p: events.append(e))
    # 创建会话应触发 session/status、session/await_input 等事件
    assert "session/status" in events
    assert "session/await_input" in events


# ---------------------------------------------------------------- InProcessTransport

def test_in_process_transport_propagates_protocol_error(backend):
    tp = InProcessTransport(backend)
    with pytest.raises(ProtocolError) as ei:
        tp.request("session/resume", {"session_id": "ghost"})
    assert ei.value.code == 4001


def test_in_process_transport_notification_handler(backend):
    tp = InProcessTransport(backend)
    got = []
    tp.on_notification(lambda e, p: got.append((e, p)))
    tp.request("session/create", {"brief": "x"})
    assert any(e == "session/status" for e, _ in got)


# ---------------------------------------------------------------- stdio 帧化

def test_stdio_server_ready_handshake_and_framing(backend):
    stdin = io.StringIO()  # 空 stdin → 立即 EOF
    stdout = io.StringIO()
    stderr = io.StringIO()
    run_stdio(backend, stdin=stdin, stdout=stdout, stderr=stderr)
    lines = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    # 首条应为 server/ready 握手（传输级）
    assert lines[0]["method"] == "server/ready"
    assert "version" in lines[0]["params"]


def test_stdio_serves_request_and_returns_response(backend):
    # 先创建一个会话拿 sid
    tp = InProcessTransport(backend)
    sid = tp.request("session/create", {"brief": "x"})["session_id"]
    # 通过 stdio 查询 session/list
    req_line = json.dumps(jsonrpc.make_request(1, "session/list", {})) + "\n"
    stdin = io.StringIO(req_line)
    stdout = io.StringIO()
    stderr = io.StringIO()
    run_stdio(backend, stdin=stdin, stdout=stdout, stderr=stderr)
    msgs = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    # server/ready + 响应
    assert msgs[0]["method"] == "server/ready"
    resp = msgs[1]
    assert resp["id"] == 1 and "sessions" in resp["result"]


def test_stdio_invalid_json_returns_4601(backend):
    stdin = io.StringIO("not json\n")
    stdout = io.StringIO()
    stderr = io.StringIO()
    run_stdio(backend, stdin=stdin, stdout=stdout, stderr=stderr)
    msgs = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    assert msgs[-1]["error"]["code"] == 4601
