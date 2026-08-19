"""MQTT bridge: publishes op25 telemetry and service status for Home Assistant."""
import json
import logging
import os
import threading
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("mqtt_bridge")


def _env(key, default=None):
    return os.environ.get(key, default) or default


class MqttPublisher:
    """Publishes op25 data to MQTT with Home Assistant auto-discovery."""

    def __init__(self):
        self._host = _env("OP25_MQTT_HOST")
        if not self._host:
            self._client = None
            return

        self._port = int(_env("OP25_MQTT_PORT", "1883"))
        self._user = _env("OP25_MQTT_USER")
        self._password = _env("OP25_MQTT_PASS")
        self._prefix = _env("OP25_MQTT_PREFIX", "op25")
        self._discovery_sent = False
        self._lock = threading.Lock()

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        self._client.will_set(
            f"{self._prefix}/status", payload="offline", qos=1, retain=True
        )

        if self._user:
            self._client.username_pw_set(self._user, self._password)

        try:
            self._client.connect_async(self._host, self._port, keepalive=60)
            self._client.loop_start()
            log.info("mqtt: connecting to %s:%d", self._host, self._port)
        except Exception as exc:
            log.warning("mqtt: connection failed: %s", exc)
            self._client = None

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("mqtt: connect failed: %s", reason_code)
            return
        log.info("mqtt: connected")
        client.publish(f"{self._prefix}/status", "online", qos=1, retain=True)
        with self._lock:
            if not self._discovery_sent:
                self._publish_discovery()
                self._discovery_sent = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("mqtt: disconnected (will reconnect): %s", reason_code)

    # ---- discovery ----

    def _discovery_config(self, component, object_id, name, state_topic,
                          payload_on=None, payload_off=None, value_template=None,
                          json_attributes_topic=None, device_class=None,
                          unit_of_measurement=None, icon=None):
        topic = f"homeassistant/{component}/{self._prefix}/{object_id}/config"
        cfg = {
            "name": name,
            "state_topic": state_topic,
            "unique_id": f"{self._prefix}_{object_id}",
            "device": {
                "identifiers": [self._prefix],
                "name": "OP25 Scanner",
                "manufacturer": "op25-docker",
            },
        }
        if payload_on is not None:
            cfg["payload_on"] = payload_on
        if payload_off is not None:
            cfg["payload_off"] = payload_off
        if value_template is not None:
            cfg["value_template"] = value_template
        if json_attributes_topic is not None:
            cfg["json_attributes_topic"] = json_attributes_topic
        if device_class is not None:
            cfg["device_class"] = device_class
        if unit_of_measurement is not None:
            cfg["unit_of_measurement"] = unit_of_measurement
        if icon is not None:
            cfg["icon"] = icon
        return topic, cfg

    def _publish_discovery(self):
        status_topic = f"{self._prefix}/status/services"
        active_topic = f"{self._prefix}/telemetry/active"
        calls_topic = f"{self._prefix}/telemetry/calls"
        sdr_topic = f"{self._prefix}/sdr"

        entities = [
            self._discovery_config(
                "binary_sensor", "op25_running", "OP25 Scanner (op25)",
                status_topic, payload_on="RUNNING", payload_off="STOPPED",
                device_class="running",
                value_template="{{ value_json.op25 }}",
            ),
            self._discovery_config(
                "binary_sensor", "icecast_running", "OP25 Scanner (icecast)",
                status_topic, payload_on="RUNNING", payload_off="STOPPED",
                device_class="running",
                value_template="{{ value_json.icecast }}",
            ),
            self._discovery_config(
                "binary_sensor", "streams_running", "OP25 Scanner (streams)",
                status_topic, payload_on="RUNNING", payload_off="STOPPED",
                device_class="running",
                value_template="{{ value_json.streams }}",
            ),
            self._discovery_config(
                "binary_sensor", "web_running", "OP25 Scanner (web)",
                status_topic, payload_on="RUNNING", payload_off="STOPPED",
                device_class="running",
                value_template="{{ value_json.web }}",
            ),
            self._discovery_config(
                "sensor", "active_talkgroup", "OP25 Active Talkgroup",
                active_topic,
                value_template="{{ value_json.tag | default(value_json.tgid | string) }}",
                json_attributes_topic=active_topic,
                icon="mdi:radio-tower",
            ),
            self._discovery_config(
                "sensor", "active_source", "OP25 Active Source",
                active_topic,
                value_template="{{ value_json.src }}",
                icon="mdi:cellphone-wireless",
            ),
            self._discovery_config(
                "sensor", "active_frequency", "OP25 Active Frequency",
                active_topic,
                value_template="{{ value_json.freq }}",
                unit_of_measurement="MHz",
                icon="mdi:frequency",
            ),
            self._discovery_config(
                "sensor", "call_count", "OP25 Recent Calls",
                calls_topic,
                value_template="{{ value_json | length }}",
                unit_of_measurement="calls",
                icon="mdi:phone-log",
            ),
            self._discovery_config(
                "sensor", "sdr_status", "OP25 SDR Status",
                sdr_topic,
                value_template="{{ 'OK' if value_json.ok else 'Error' }}",
                json_attributes_topic=sdr_topic,
                icon="mdi:usb",
            ),
        ]

        for topic, cfg in entities:
            self._client.publish(topic, json.dumps(cfg), qos=1, retain=True)

    # ---- public publish methods ----

    @property
    def enabled(self):
        return self._client is not None

    def publish_service_status(self, status_dict):
        if not self.enabled:
            return
        self._client.publish(
            f"{self._prefix}/status/services",
            json.dumps(status_dict), qos=0, retain=True,
        )

    def publish_active_channel(self, channel_data):
        if not self.enabled:
            return
        self._client.publish(
            f"{self._prefix}/telemetry/active",
            json.dumps(channel_data), qos=0,
        )

    def publish_calls(self, calls_list):
        if not self.enabled:
            return
        self._client.publish(
            f"{self._prefix}/telemetry/calls",
            json.dumps(calls_list[:50]), qos=0,
        )

    def publish_sdr_status(self, sdr_data):
        if not self.enabled:
            return
        self._client.publish(
            f"{self._prefix}/sdr",
            json.dumps(sdr_data), qos=0, retain=True,
        )

    def stop(self):
        if not self.enabled:
            return
        try:
            self._client.publish(f"{self._prefix}/status", "offline", qos=1, retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
