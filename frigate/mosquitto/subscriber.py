#!/usr/bin/env python3
# mqtt_frigate_listener.py
# Listens to an MQTT topic and prints events for car or dog that have video
# Requirements: pip install paho-mqtt

import os
import sys
import time
import json
from typing import Any, Dict, List

import paho.mqtt.client as mqtt

# Configuration via environment variables with defaults
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "frigate/events")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "frigate-listener")
FILTER_LABELS = {"car", "dog"}  # labels we care about

def find_keys_recursive(obj: Any, key: str) -> List[Any]:
    """Return all values for a given key anywhere in a nested dict/list."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            found.extend(find_keys_recursive(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_keys_recursive(item, key))
    return found

def any_string_contains(obj: Any, substrs: List[str]) -> bool:
    """Check if any string value in nested structure contains any of substrs."""
    if isinstance(obj, dict):
        for v in obj.values():
            if any_string_contains(v, substrs):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if any_string_contains(item, substrs):
                return True
    elif isinstance(obj, str):
        for s in substrs:
            if s in obj:
                return True
    return False

def message_has_video(msg: Dict[str, Any]) -> bool:
    """Determine if message indicates there is a video or clip available."""
    # Look for explicit has_clip boolean
    has_clip_values = find_keys_recursive(msg, "has_clip")
    if any(v is True for v in has_clip_values):
        return True

    # Look for thumbnail or clip paths
    if any_string_contains(msg, ["/media/frigate", "clips", "thumb", "thumb_path", "clip"]):
        return True

    # Some messages include review data with detections list pointing to clip ids
    data_objects = find_keys_recursive(msg, "data")
    for d in data_objects:
        if isinstance(d, dict) and ("detections" in d or "objects" in d):
            return True

    return False

def message_has_label(msg: Dict[str, Any], labels: set) -> bool:
    """Check if message contains any of the target labels in common places."""
    # Check arrays named objects
    objects_lists = find_keys_recursive(msg, "objects")
    for ol in objects_lists:
        if isinstance(ol, list):
            for item in ol:
                if isinstance(item, str) and item in labels:
                    return True

    # Check label fields in before/after/detections
    label_values = find_keys_recursive(msg, "label")
    for lv in label_values:
        if isinstance(lv, str) and lv in labels:
            return True

    # As a fallback check raw JSON strings for the label tokens
    raw = json.dumps(msg)
    for l in labels:
        if ('"' + l + '"') in raw:
            return True

    return False

def extract_info(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Extract useful info for printing: camera, start, end, thumb or clip, labels, id, type."""
    info = {"camera": None, "start_time": None, "end_time": None, "thumb": None, "clip_id": None, "labels": []}
    # camera
    cam_vals = find_keys_recursive(msg, "camera")
    if cam_vals:
        info["camera"] = cam_vals[0]
    # times
    start_vals = find_keys_recursive(msg, "start_time")
    end_vals = find_keys_recursive(msg, "end_time")
    if start_vals:
        info["start_time"] = start_vals[0]
    if end_vals:
        # choose a non null end time if present
        for v in end_vals:
            if v is not None:
                info["end_time"] = v
                break
    # thumb or thumb_path
    thumb_vals = find_keys_recursive(msg, "thumb_path") + find_keys_recursive(msg, "thumb")
    for t in thumb_vals:
        if isinstance(t, str) and t:
            info["thumb"] = t
            break
    # clip id or id
    id_vals = find_keys_recursive(msg, "id")
    if id_vals:
        info["clip_id"] = id_vals[0]
    # labels from objects or label fields
    objects_lists = find_keys_recursive(msg, "objects")
    for ol in objects_lists:
        if isinstance(ol, list):
            for item in ol:
                if isinstance(item, str):
                    info["labels"].append(item)
    label_vals = find_keys_recursive(msg, "label")
    for lv in label_vals:
        if isinstance(lv, str):
            info["labels"].append(lv)
    # dedupe labels
    info["labels"] = list(dict.fromkeys(info["labels"]))
    return info

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to MQTT broker", MQTT_HOST, MQTT_PORT)
        client.subscribe(MQTT_TOPIC)
        print("Subscribed to topic", MQTT_TOPIC)
    else:
        print("Failed to connect, return code", rc)
        sys.exit(1)

def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8", errors="ignore").strip()
    if not payload:
        return
    try:
        data = json.loads(payload)
    except Exception:
        # Not valid JSON, ignore
        return

    if not message_has_label(data, FILTER_LABELS):
        return

    if not message_has_video(data):
        return

    info = extract_info(data)

    # Prepare printable time strings if numeric epoch present
    start_ts = info.get("start_time")
    end_ts = info.get("end_time")
    try:
        start_human = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(start_ts))) if start_ts else "unknown"
    except Exception:
        start_human = str(start_ts)

    try:
        end_human = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(end_ts))) if end_ts else "unknown"
    except Exception:
        end_human = str(end_ts)

    labels = ", ".join(info.get("labels") or [])
    camera = info.get("camera") or "unknown"
    thumb = info.get("thumb") or "none"
    clip_id = info.get("clip_id") or "none"

    # Print a concise one line summary
    print(f"time {start_human} camera {camera} labels {labels} clip_id {clip_id} thumb {thumb}")

def main():
    client = mqtt.Client(MQTT_CLIENT_ID)
    # If your broker requires username and password set them via environment variables
    mqtt_user = os.getenv("MQTT_USER")
    mqtt_pass = os.getenv("MQTT_PASSWORD")
    if mqtt_user and mqtt_pass:
        client.username_pw_set(mqtt_user, mqtt_pass)

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    except Exception as e:
        print("Error connecting to MQTT broker:", e)
        sys.exit(1)

    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("Exiting on user interrupt")
        client.disconnect()

if __name__ == "__main__":
    main()
