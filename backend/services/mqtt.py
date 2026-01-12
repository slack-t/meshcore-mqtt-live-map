"""
MQTT service for subscribing to mesh network topics and processing messages.
"""

import asyncio
import time
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt

import state
from config import (
  DEBUG_PAYLOAD,
  DEBUG_PAYLOAD_MAX,
  MQTT_CA_CERT,
  MQTT_CLIENT_ID,
  MQTT_HOST,
  MQTT_ONLINE_FORCE_NAMES_SET,
  MQTT_PASSWORD,
  MQTT_PORT,
  MQTT_SEEN_BROADCAST_MIN_SECONDS,
  MQTT_TLS,
  MQTT_TLS_INSECURE,
  MQTT_TOPICS,
  MQTT_TRANSPORT,
  MQTT_USERNAME,
  MQTT_WS_PATH,
)
from decoder import (
  ROUTE_PAYLOAD_TYPES_SET,
  _device_id_from_topic,
  _safe_preview,
  _topic_marks_online,
  _try_parse_payload,
)
from helpers import within_map_radius
from state import (
  debug_last,
  device_names,
  device_roles,
  device_role_sources,
  devices,
  last_seen_broadcast,
  message_origins,
  mqtt_seen,
  result_counts,
  seen_devices,
  stats,
  status_last,
  topic_counts,
)

# Global MQTT client
mqtt_client: Optional[mqtt.Client] = None


def on_connect(client, userdata, flags, reason_code, properties=None):
  """Handle MQTT connection."""
  topics_str = ", ".join(MQTT_TOPICS)
  print(f"[mqtt] connected reason_code={reason_code} subscribing topics={topics_str}")
  for topic in MQTT_TOPICS:
    client.subscribe(topic, qos=0)


def on_disconnect(client, userdata, reason_code, properties=None, *args, **kwargs):
  """Handle MQTT disconnection."""
  print(f"[mqtt] disconnected reason_code={reason_code}")


def on_message(client, userdata, msg: mqtt.MQTTMessage):
  """Handle incoming MQTT messages."""
  from services.broadcaster import update_queue

  stats["received_total"] += 1
  stats["last_rx_ts"] = time.time()
  stats["last_rx_topic"] = msg.topic
  topic_counts[msg.topic] = topic_counts.get(msg.topic, 0) + 1
  loop: asyncio.AbstractEventLoop = userdata["loop"]

  # Track device online status from topic
  dev_guess = _device_id_from_topic(msg.topic)
  if dev_guess and _topic_marks_online(msg.topic):
    now = time.time()
    seen_devices[dev_guess] = now
    mqtt_seen[dev_guess] = now
    if dev_guess in devices:
      last_sent = last_seen_broadcast.get(dev_guess, 0)
      if now - last_sent >= MQTT_SEEN_BROADCAST_MIN_SECONDS:
        last_seen_broadcast[dev_guess] = now
        loop.call_soon_threadsafe(
          update_queue.put_nowait,
          {
            "type": "device_seen",
            "device_id": dev_guess,
            "last_seen_ts": now,
            "mqtt_seen_ts": now,
          },
        )

  # Parse the payload
  parsed, debug = _try_parse_payload(msg.topic, msg.payload)
  device_id_hint = parsed.get("device_id") if parsed else None

  # Filter zero coordinates
  if parsed and (parsed.get("lat", 0) == 0 and parsed.get("lon", 0) == 0):
    debug["result"] = "filtered_zero_coords"
    parsed = None

  # Filter coordinates outside map radius
  if parsed and not within_map_radius(parsed.get("lat"), parsed.get("lon")):
    debug["result"] = "filtered_radius"
    parsed = None
    if device_id_hint:
      loop.call_soon_threadsafe(
        update_queue.put_nowait,
        {
          "type": "device_remove",
          "device_id": device_id_hint,
          "reason": "radius",
        },
      )

  # Extract metadata
  origin_id = debug.get("origin_id") or _device_id_from_topic(msg.topic)
  decoder_meta = debug.get("decoder_meta") or {}
  result = debug.get("result") or "unknown"
  device_role = debug.get("device_role")

  # Determine role target ID
  role_target_id = origin_id
  if device_role and result.startswith("decoded"):
    role_target_id = None
    loc_meta = decoder_meta.get("location") if isinstance(decoder_meta, dict) else None
    loc_pubkey = loc_meta.get("pubkey") if isinstance(loc_meta, dict) else None
    if isinstance(loc_pubkey, str) and loc_pubkey.strip():
      role_target_id = loc_pubkey
    else:
      decoded_pubkey = debug.get("decoded_pubkey")
      if isinstance(decoded_pubkey, str) and decoded_pubkey.strip():
        role_target_id = decoded_pubkey

  # Store debug entry
  debug_entry = {
    "ts": time.time(),
    "topic": msg.topic,
    "result": debug.get("result"),
    "found_path": debug.get("found_path"),
    "found_hint": debug.get("found_hint"),
    "decoder_meta": decoder_meta,
    "role_target_id": role_target_id,
    "packet_hash": debug.get("packet_hash"),
    "direction": debug.get("direction"),
    "json_keys": debug.get("json_keys"),
    "parse_error": debug.get("parse_error"),
    "origin_id": origin_id,
    "payload_preview": _safe_preview(msg.payload[:DEBUG_PAYLOAD_MAX]),
  }
  debug_last.append(debug_entry)

  # Store status messages
  if msg.topic.endswith("/status"):
    status_last.append({
      "ts": debug_entry["ts"],
      "topic": msg.topic,
      "device_name": debug.get("device_name"),
      "device_role": debug.get("device_role"),
      "origin_id": origin_id,
      "json_keys": debug_entry.get("json_keys"),
      "payload_preview": debug_entry["payload_preview"],
    })

  result_counts[result] = result_counts.get(result, 0) + 1

  # Update device name if found
  device_name = debug.get("device_name")
  if device_name and origin_id:
    existing_name = device_names.get(origin_id)
    if existing_name != device_name:
      device_names[origin_id] = device_name
      state.state_dirty = True
      device_state = devices.get(origin_id)
      if device_state:
        device_state.name = device_name
        loop.call_soon_threadsafe(
          update_queue.put_nowait,
          {
            "type": "device_name",
            "device_id": origin_id,
          },
        )

  # Update device role if found
  if device_role and role_target_id:
    existing_role = device_roles.get(role_target_id)
    if existing_role != device_role:
      device_roles[role_target_id] = device_role
      device_role_sources[role_target_id] = "explicit"
      state.state_dirty = True
      device_state = devices.get(role_target_id)
      if device_state:
        device_state.role = device_role
        loop.call_soon_threadsafe(
          update_queue.put_nowait,
          {
            "type": "device_role",
            "device_id": role_target_id,
          },
        )

  # Process routing information
  path_hashes = decoder_meta.get("pathHashes")
  payload_type = decoder_meta.get("payloadType")
  route_type = decoder_meta.get("routeType")
  message_hash = decoder_meta.get("messageHash") or debug.get("packet_hash")
  snr_values = decoder_meta.get("snrValues")
  path_header = decoder_meta.get("path")
  direction = debug.get("direction")
  receiver_id = _device_id_from_topic(msg.topic)

  # Determine route origin
  route_origin_id = None
  loc_meta = decoder_meta.get("location") if isinstance(decoder_meta, dict) else None
  if isinstance(loc_meta, dict):
    decoded_pubkey = loc_meta.get("pubkey")
    if decoded_pubkey:
      route_origin_id = decoded_pubkey

  direction_value = str(direction or "").lower()

  # Track message origins for fanout detection
  if message_hash:
    cache = message_origins.get(message_hash)
    if not cache:
      cache = {"origin_id": None, "first_rx": None, "receivers": set(), "ts": time.time()}
      message_origins[message_hash] = cache
    cache["ts"] = time.time()
    origin_for_tx = origin_id or receiver_id
    if direction_value == "tx" and origin_for_tx:
      cache["origin_id"] = origin_for_tx
    if direction_value == "rx" and receiver_id:
      cache["receivers"].add(receiver_id)
      if not cache.get("first_rx"):
        cache["first_rx"] = receiver_id
    cached_origin = cache.get("origin_id")
    if not route_origin_id and cached_origin:
      route_origin_id = cached_origin
    if not route_origin_id and direction_value == "rx":
      first_rx = cache.get("first_rx")
      if first_rx and receiver_id and receiver_id != first_rx:
        route_origin_id = first_rx

  if not route_origin_id:
    route_origin_id = origin_id

  # Normalize payload/route types
  try:
    payload_type = int(payload_type) if payload_type is not None else None
  except (TypeError, ValueError):
    payload_type = None
  try:
    route_type = int(route_type) if route_type is not None else None
  except (TypeError, ValueError):
    route_type = None

  # Determine route hashes
  route_hashes = None
  if path_hashes and isinstance(path_hashes, list):
    route_hashes = path_hashes
  elif payload_type not in (8, 9) and isinstance(path_header, list):
    if route_type in (0, 1):
      route_hashes = path_header

  # Emit route events
  route_emitted = False
  if route_hashes and payload_type in ROUTE_PAYLOAD_TYPES_SET:
    loop.call_soon_threadsafe(
      update_queue.put_nowait,
      {
        "type": "route",
        "path_hashes": route_hashes,
        "payload_type": payload_type,
        "message_hash": message_hash,
        "origin_id": route_origin_id,
        "receiver_id": receiver_id,
        "snr_values": snr_values,
        "route_type": route_type,
        "ts": time.time(),
        "topic": msg.topic,
      },
    )
    route_emitted = True
  elif message_hash and route_origin_id and receiver_id:
    if direction_value == "rx" and msg.topic.endswith("/packets"):
      loop.call_soon_threadsafe(
        update_queue.put_nowait,
        {
          "type": "route",
          "route_mode": "fanout",
          "route_id": f"{message_hash}-{receiver_id}",
          "origin_id": route_origin_id,
          "receiver_id": receiver_id,
          "message_hash": message_hash,
          "route_type": route_type,
          "payload_type": payload_type,
          "ts": time.time(),
          "topic": msg.topic,
        },
      )
      route_emitted = True

  # Fallback: direct route if no path
  if (
    not route_emitted
    and direction_value == "rx"
    and msg.topic.endswith("/packets")
    and receiver_id
    and route_origin_id
    and receiver_id != route_origin_id
    and payload_type in ROUTE_PAYLOAD_TYPES_SET
  ):
    fallback_id = message_hash or f"{route_origin_id}-{receiver_id}-{int(time.time() * 1000)}"
    loop.call_soon_threadsafe(
      update_queue.put_nowait,
      {
        "type": "route",
        "route_mode": "direct",
        "route_id": f"direct-{fallback_id}",
        "origin_id": route_origin_id,
        "receiver_id": receiver_id,
        "message_hash": message_hash,
        "route_type": route_type,
        "payload_type": payload_type,
        "ts": time.time(),
        "topic": msg.topic,
      },
    )

  # Handle unparsed messages
  if not parsed:
    stats["unparsed_total"] += 1
    if DEBUG_PAYLOAD:
      print(f"[mqtt] UNPARSED result={result} topic={msg.topic} preview={debug_entry['payload_preview']!r}")
    return

  parsed["raw_topic"] = msg.topic
  stats["parsed_total"] += 1
  stats["last_parsed_ts"] = time.time()
  stats["last_parsed_topic"] = msg.topic

  if DEBUG_PAYLOAD:
    print(f"[mqtt] PARSED topic={msg.topic} device={parsed['device_id']} lat={parsed['lat']} lon={parsed['lon']}")

  loop.call_soon_threadsafe(update_queue.put_nowait, {"type": "device", "data": parsed})


def create_client(loop: asyncio.AbstractEventLoop) -> mqtt.Client:
  """Create and configure the MQTT client."""
  global mqtt_client

  transport = "websockets" if MQTT_TRANSPORT == "websockets" else "tcp"
  topics_str = ", ".join(MQTT_TOPICS)
  print(
    f"[mqtt] connecting host={MQTT_HOST} port={MQTT_PORT} tls={MQTT_TLS} "
    f"transport={transport} ws_path={MQTT_WS_PATH if transport == 'websockets' else '-'} topics={topics_str}"
  )

  mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=(MQTT_CLIENT_ID or None),
    userdata={"loop": loop},
    transport=transport,
  )

  if transport == "websockets":
    mqtt_client.ws_set_options(path=MQTT_WS_PATH)

  if MQTT_USERNAME:
    mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

  if MQTT_TLS:
    if MQTT_CA_CERT:
      mqtt_client.tls_set(ca_certs=MQTT_CA_CERT)
    else:
      mqtt_client.tls_set()
    if MQTT_TLS_INSECURE:
      mqtt_client.tls_insecure_set(True)

  mqtt_client.on_connect = on_connect
  mqtt_client.on_disconnect = on_disconnect
  mqtt_client.on_message = on_message

  mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
  mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
  mqtt_client.loop_start()

  return mqtt_client


def stop_client() -> None:
  """Stop the MQTT client."""
  global mqtt_client
  if mqtt_client is not None:
    try:
      mqtt_client.loop_stop()
      mqtt_client.disconnect()
    except Exception:
      pass
    mqtt_client = None
