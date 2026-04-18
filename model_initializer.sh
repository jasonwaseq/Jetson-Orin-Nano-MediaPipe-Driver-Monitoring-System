export MP_MQTT_HOST='73797b78ceac47e998c30ac034930c26.s1.eu.hivemq.cloud'
export MP_MQTT_PORT='8883'
export MP_MQTT_TLS='true'
export MP_MQTT_USERNAME='group7'
export MP_MQTT_PASSWORD='group7BananaSlug'
export MP_MQTT_CLIENT_ID='uplink-jetson-01'
export MP_QTT_HEARTBEAT_SECONDS='10'

# mirror into alternate env name families
export MP_QTT_HOST="$MP_MQTT_HOST"
export MP_QTT_PORT="$MP_MQTT_PORT"
export MP_QTT_TLS="$MP_MQTT_TLS"
export MP_QTT_USERNAME="$MP_MQTT_USERNAME"
export MP_QTT_PASSWORD="$MP_MQTT_PASSWORD"
export MP_QTT_CLIENT_ID="$MP_MQTT_CLIENT_ID"

export MPMQTT_HOST="$MP_MQTT_HOST"
export MPMQTT_PORT="$MP_MQTT_PORT"
export MPMQTT_TLS="$MP_MQTT_TLS"
export MPMQTT_USERNAME="$MP_MQTT_USERNAME"
export MPMQTT_PASSWORD="$MP_MQTT_PASSWORD"
export MPMQTT_CLIENT_ID="$MP_MQTT_CLIENT_ID"

export MPQTT_HOST="$MP_MQTT_HOST"
export MPQTT_PORT="$MP_MQTT_PORT"
export MPQTT_TLS="$MP_MQTT_TLS"
export MPQTT_USERNAME="$MP_MQTT_USERNAME"
export MPQTT_PASSWORD="$MP_MQTT_PASSWORD"
export MPQTT_CLIENT_ID="$MP_MQTT_CLIENT_ID"

export PYTHONPATH="/usr/lib/python3/dist-packages:/usr/local/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"

pkill -f 'ble_bridge_server.py' || true
./venv/bin/python -u ble/ble_bridge_server.py > /tmp/ble_bridge.log 2>&1 &

pkill -f 'audio_bridge_server.py' || true
./venv/bin/python -u audio/audio_bridge_server.py > /tmp/audio_bridge.log 2>&1 &

pkill -f 'face_detect_mediapipe.py' || true
./venv/bin/python -u face_detect_mediapipe.py 2>&1 | tee /tmp/jetson_mqtt.log
