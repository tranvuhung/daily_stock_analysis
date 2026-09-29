# -*- coding: utf-8 -*-
"""Thăm dò broker MQTT-over-WebSocket của TVSI: ghi lại topic và payload mẫu.

Probe the TVSI MQTT-over-WebSocket broker and record topics and sample payloads.
Credentials come only from environment variables; nothing is hardcoded.

Usage:
    TVSI_MQTT_HOST=... TVSI_MQTT_USERNAME=... TVSI_MQTT_PASSWORD=... \\
        python scripts/probe_tvsi_mqtt.py --seconds 90 --out /path/to/probe.json
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List

import paho.mqtt.client as mqtt

DEFAULT_PATHS = ["/mqtt", "/ws", "/"]  # chỉ dùng khi --transport websockets
# Chỉ subscribe topic giá. KHÔNG dùng "#": broker còn phát topic order/<id> và new-order/<account>
# chứa lệnh của khách hàng khác. Only price topics; never "#" (it leaks other customers' orders).
DEFAULT_TOPICS = ["topic/HPG", "market/+"]
SAMPLES_PER_TOPIC = 5


def _decode(payload: bytes) -> Dict[str, Any]:
    info: Dict[str, Any] = {"len": len(payload), "hex": payload[:120].hex()}
    try:
        text = payload.decode("utf-8")
        info["text"] = text
        try:
            info["json"] = json.loads(text)
        except ValueError:
            pass
    except UnicodeDecodeError:
        info["text"] = None
    return info


def probe_path(host: str, port: int, path: str, username: str, password: str,
               topics: List[str], seconds: int, use_tls: bool, transport: str = "tcp") -> Dict[str, Any]:
    result: Dict[str, Any] = {"transport": transport, "path": path, "connected": False, "rc": None, "subscribed": {}, "topics": {}}
    counts: Dict[str, int] = defaultdict(int)
    samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    connected = threading.Event()
    lock = threading.Lock()

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"dsa-probe-{int(time.time())}",
        transport=transport,
    )
    if transport == "websockets":
        client.ws_set_options(path=path)
    if use_tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    if username:
        client.username_pw_set(username, password)

    def on_connect(cl, userdata, flags, reason_code, properties):
        result["rc"] = str(reason_code)
        if not reason_code.is_failure:
            result["connected"] = True
            for topic in topics:
                mid = cl.subscribe(topic, qos=0)[1]
                result["subscribed"][topic] = {"mid": mid}
            connected.set()

    def on_subscribe(cl, userdata, mid, reason_codes, properties):
        for topic, meta in result["subscribed"].items():
            if meta.get("mid") == mid:
                meta["granted"] = [str(rc) for rc in reason_codes]

    def on_message(cl, userdata, msg):
        with lock:
            counts[msg.topic] += 1
            if len(samples[msg.topic]) < SAMPLES_PER_TOPIC:
                samples[msg.topic].append({"t": time.time(), **_decode(msg.payload)})

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    try:
        client.connect(host, port, keepalive=30)
    except Exception as exc:  # noqa: BLE001 - probe tool, report everything
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    client.loop_start()
    if connected.wait(timeout=15):
        time.sleep(seconds)
    client.loop_stop()
    client.disconnect()
    with lock:
        result["topics"] = {
            topic: {"count": counts[topic], "samples": samples[topic]}
            for topic in sorted(counts, key=lambda item: -counts[item])
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--paths", default=",".join(DEFAULT_PATHS))
    parser.add_argument("--topics", default=",".join(DEFAULT_TOPICS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-tls", action="store_true")
    # Broker TVSI nhận MQTT thuần trên TLS cổng 443; websockets bị reset khi handshake.
    parser.add_argument("--transport", choices=["tcp", "websockets"], default="tcp")
    parser.add_argument("--stop-on-first", action="store_true", help="dừng ở path đầu tiên nhận được message")
    args = parser.parse_args()

    host = os.getenv("TVSI_MQTT_HOST", "").strip()
    port = int(os.getenv("TVSI_MQTT_PORT", "443"))
    username = os.getenv("TVSI_MQTT_USERNAME", "")
    password = os.getenv("TVSI_MQTT_PASSWORD", "")
    if not host:
        print("TVSI_MQTT_HOST is required", file=sys.stderr)
        return 2

    topics = [item for item in args.topics.split(",") if item]
    report = {"host": host, "port": port, "topics_requested": topics, "attempts": []}
    paths = [item for item in args.paths.split(",") if item] if args.transport == "websockets" else ["-"]
    for path in paths:
        attempt = probe_path(
            host, port, path, username, password, topics, args.seconds, not args.no_tls, args.transport
        )
        report["attempts"].append(attempt)
        total = sum(meta["count"] for meta in attempt["topics"].values())
        print(f"path={path} connected={attempt['connected']} rc={attempt['rc']} "
              f"error={attempt.get('error')} topics={len(attempt['topics'])} messages={total}")
        if args.stop_on_first and total:
            break

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    print(f"report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
