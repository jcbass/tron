import time
import utime
import machine
import neopixel
import random
import micropython
import uasyncio as asyncio

try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None

ENABLE_WEBREPL = True

# ----------------------------
# Hardware pins / strip config
# ----------------------------
LED_PIN = 18             # Strip data pin
LED_COUNT = 60
MOTION_SENSOR_PIN = 8    # PIR OUT connected here

# Onboard NeoPixel (QT Py ESP32-S3)
NEO_DATA_PIN = 39        # Onboard NeoPixel data
NEO_PWR_EN_PIN = 38      # Onboard NeoPixel power enable

# ----------------------------
# MQTT configuration
# ----------------------------
MQTT_HOST = "192.168.1.10"
MQTT_CLIENT_ID = "tron-esp32s3"
MQTT_TOPIC_CMD = b"tron/cmd"
MQTT_TOPIC_STATE = b"tron/state"

# ----------------------------
# Shared state
# ----------------------------
state = {
    "strip_on": True,
    "strip_brightness": 0.30,
    "params": {
        "BRIGHTNESS_FACTOR": 0.25,
        "WARM_LEVEL": 255,
        "COOL_LEVEL": 0,
        "DELAY_MIN": 0.005,
        "DELAY_MAX": 0.010,
        "TRAIL_MIN": 1,
        "TRAIL_MAX": 3,
        "MIN_ENDPOINT": 57,
        "MAX_ENDPOINT": 57,
        "BOUNCE": False,
        "MIN_MOTION_WAIT": 5,
        "MAX_MOTION_WAIT": 20,
        "BURST_GAP_S": 0.0,
    },
}

# ----------------------------
# Initialize hardware (order matters)
# ----------------------------
np = neopixel.NeoPixel(machine.Pin(LED_PIN), LED_COUNT)
motion_sensor = machine.Pin(MOTION_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_DOWN)
neo_pwr = machine.Pin(NEO_PWR_EN_PIN, machine.Pin.OUT)
neo_pwr.value(1)
neo_ind = neopixel.NeoPixel(machine.Pin(NEO_DATA_PIN), 1)


def set_indicator(is_high: int):
    neo_ind[0] = (0, 128, 0) if is_high else (0, 0, 0)
    neo_ind.write()


set_indicator(motion_sensor.value())

micropython.alloc_emergency_exception_buf(100)

# ----------------------------
# Animation helpers
# ----------------------------
_anim_busy = False
_fire_queue = []
_motion_flag = False
_pending_motion = None


def request_fire(source: str):
    global _fire_queue
    if len(_fire_queue) < 4:
        _fire_queue.append(source)
    else:
        print("Fire queue full; dropping %s" % source)


def set_cct_color(warm_level, cool_level):
    warm = max(0, min(255, int(warm_level)))
    cool = max(0, min(255, int(cool_level)))
    return (warm, cool, 0)


def apply_steady_state(force: bool = False):
    global _anim_busy
    if _anim_busy and not force:
        return

    params = state["params"]
    if state["strip_on"]:
        brightness = state["strip_brightness"]
        brightness = max(0.0, min(1.0, brightness))
        warm_value = int(params["WARM_LEVEL"] * brightness)
        cool_value = int(params["COOL_LEVEL"] * brightness)
        color = set_cct_color(warm_value, cool_value)
    else:
        color = (0, 0, 0)

    np.fill(color)
    np.write()


def tron_burst(params):
    num_leds = LED_COUNT
    brightness_factor = params.get("BRIGHTNESS_FACTOR", 0.25)
    warm_level = params.get("WARM_LEVEL", 255)
    cool_level = params.get("COOL_LEVEL", 0)
    delay_min = params.get("DELAY_MIN", 0.005)
    delay_max = params.get("DELAY_MAX", 0.010)
    trail_min = params.get("TRAIL_MIN", 1)
    trail_max = params.get("TRAIL_MAX", 3)
    min_endpoint = int(params.get("MIN_ENDPOINT", num_leds - 1))
    max_endpoint = int(params.get("MAX_ENDPOINT", num_leds - 1))
    bounce = bool(params.get("BOUNCE", False))

    delay_min = max(0.0, delay_min)
    delay_max = max(delay_min, delay_max)
    trail_min = max(1, int(trail_min))
    trail_max = max(trail_min, int(trail_max))
    min_endpoint = max(0, min(num_leds - 1, min_endpoint))
    max_endpoint = max(min_endpoint, min(num_leds - 1, max_endpoint))

    speed = random.uniform(delay_min, delay_max)
    trail = random.randint(trail_min, trail_max)
    endpoint = random.randint(min_endpoint, max_endpoint)
    trail = max(1, min(trail, endpoint + 1))

    position = 0
    direction = 1
    cycle_complete = False

    while not cycle_complete:
        np.fill((0, 0, 0))
        for i in range(trail):
            led_pos = position - i
            if led_pos < 0:
                break
            if led_pos > endpoint:
                continue
            brightness = ((trail - i) / trail) * brightness_factor
            warm_value = warm_level * brightness
            cool_value = cool_level * brightness
            np[led_pos] = set_cct_color(warm_value, cool_value)

        np.write()
        position += direction

        if bounce:
            if position >= endpoint:
                position = endpoint - 1
                direction = -1
            elif position < 0:
                position = 0
                cycle_complete = True
        else:
            if position > endpoint:
                cycle_complete = True

        time.sleep(speed)

    np.fill((0, 0, 0))
    np.write()


def motion_irq(pin):
    global _motion_flag
    if not _motion_flag:
        _motion_flag = True


motion_sensor.irq(trigger=machine.Pin.IRQ_RISING, handler=motion_irq)


async def animation_consumer():
    global _anim_busy
    while True:
        if _fire_queue:
            source = _fire_queue.pop(0)
            _anim_busy = True
            print("Running Tron burst (source: %s)" % source)
            try:
                tron_burst(state["params"])
            finally:
                _anim_busy = False
                apply_steady_state(force=True)
        await asyncio.sleep_ms(50)


async def steady_refresh_task():
    while True:
        await asyncio.sleep(10)
        apply_steady_state()


async def motion_poller():
    global _motion_flag, _pending_motion
    last_level = motion_sensor.value()
    print("PIR initial level:", "HIGH" if last_level else "LOW")

    while True:
        cur = motion_sensor.value()
        if cur != last_level:
            print("PIR:", "HIGH" if cur else "LOW")
            last_level = cur
            set_indicator(cur)

        if _motion_flag:
            _motion_flag = False
            if _pending_motion is None:
                params = state["params"]
                min_wait = max(0.0, float(params["MIN_MOTION_WAIT"]))
                max_wait = max(min_wait, float(params["MAX_MOTION_WAIT"]))
                wait_time = random.uniform(min_wait, max_wait)
                burst_total = random.randint(1, 3)
                fire_at = utime.ticks_add(utime.ticks_ms(), int(wait_time * 1000))
                _pending_motion = {
                    "burst_total": burst_total,
                    "bursts_left": burst_total,
                    "fire_at": fire_at,
                    "printed": False,
                    "wait_time": wait_time,
                }

        if _pending_motion is not None:
            if not _pending_motion["printed"]:
                print(
                    "Motion detected! Waiting %.2f seconds before running %d tron burst(s)..."
                    % (
                        _pending_motion["wait_time"],
                        _pending_motion["burst_total"],
                    )
                )
                _pending_motion["printed"] = True

            if utime.ticks_diff(utime.ticks_ms(), _pending_motion["fire_at"]) >= 0:
                request_fire("motion")
                _pending_motion["bursts_left"] -= 1
                if _pending_motion["bursts_left"] > 0:
                    gap = state["params"].get("BURST_GAP_S", 0.0)
                    if gap > 0:
                        _pending_motion["fire_at"] = utime.ticks_add(utime.ticks_ms(), int(gap * 1000))
                    else:
                        _pending_motion["fire_at"] = utime.ticks_ms()
                else:
                    _pending_motion = None

        await asyncio.sleep_ms(100)


def mqtt_message(topic, msg):
    message = msg.decode().strip().upper()
    if message == "ON":
        state["strip_on"] = True
        apply_steady_state()
    elif message == "OFF":
        state["strip_on"] = False
        apply_steady_state()
    elif message.startswith("DIM:"):
        try:
            value = float(message.split(":", 1)[1])
            state["strip_brightness"] = max(0.0, min(1.0, value))
            apply_steady_state()
        except ValueError:
            print("Invalid DIM payload:", message)
    elif message == "FIRE":
        request_fire("mqtt")
    else:
        print("Unknown MQTT command:", message)


async def mqtt_loop():
    if MQTTClient is None:
        print("umqtt.simple not available; MQTT disabled")
        return

    client = None

    while True:
        if client is None:
            try:
                client = MQTTClient(MQTT_CLIENT_ID, MQTT_HOST)
                client.set_callback(mqtt_message)
                client.connect()
                client.publish(MQTT_TOPIC_STATE, b"ONLINE", retain=True)
                client.subscribe(MQTT_TOPIC_CMD)
                print("MQTT connected")
            except Exception as exc:
                print("MQTT connect failed:", exc)
                client = None
                await asyncio.sleep(5)
                continue

        try:
            client.check_msg()
        except Exception as exc:
            print("MQTT error:", exc)
            try:
                client.disconnect()
            except Exception:
                pass
            client = None
            await asyncio.sleep(5)
            continue

        await asyncio.sleep_ms(100)


HTML_TEMPLATE = """<html><head><title>Tron Control</title></head>
<body>
<h1>Tron Effect Control</h1>
<form action="/set" method="get">
<fieldset>
<legend>Ambient Lighting</legend>
%s
</fieldset>
<fieldset>
<legend>Animation Parameters</legend>
%s
</fieldset>
<button type="submit">Save/Apply</button>
</form>
<p><a href="/fire">Trigger FIRE</a></p>
</body></html>"""


def render_index():
    ambient_inputs = [
        '<input type="hidden" name="strip_on" value="off">',
        (
            '<label>Strip On <input type="checkbox" name="strip_on" %s></label><br>'
            % ("checked" if state["strip_on"] else "")
        ),
        (
            '<label>Strip Brightness '
            '<input type="number" name="strip_brightness" min="0" max="1" step="0.01" '
            'value="%.2f"></label><br>'
            % state["strip_brightness"]
        ),
    ]

    inputs = []
    params = state["params"]
    for key in (
        "BRIGHTNESS_FACTOR",
        "WARM_LEVEL",
        "COOL_LEVEL",
        "DELAY_MIN",
        "DELAY_MAX",
        "TRAIL_MIN",
        "TRAIL_MAX",
        "MIN_ENDPOINT",
        "MAX_ENDPOINT",
        "BOUNCE",
        "MIN_MOTION_WAIT",
        "MAX_MOTION_WAIT",
        "BURST_GAP_S",
    ):
        value = params[key]
        if isinstance(value, bool):
            input_field = (
                "<label>%s <input type=\"checkbox\" name=\"%s\" %s></label><br>"
                % (key, key, "checked" if value else "")
            )
        else:
            input_field = (
                "<label>%s <input type=\"text\" name=\"%s\" value=\"%s\"></label><br>"
                % (key, key, value)
            )
        inputs.append(input_field)
    return HTML_TEMPLATE % ("\n".join(ambient_inputs), "\n".join(inputs))


def urldecode(value: str) -> str:
    result = []
    i = 0
    length = len(value)
    while i < length:
        ch = value[i]
        if ch == "+":
            result.append(" ")
            i += 1
        elif ch == "%" and i + 2 < length:
            try:
                result.append(chr(int(value[i + 1 : i + 3], 16)))
                i += 3
            except ValueError:
                result.append(ch)
                i += 1
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def parse_bool(value):
    return value.lower() in ("1", "true", "on", "yes")


PARAM_TYPES = {
    "BRIGHTNESS_FACTOR": float,
    "WARM_LEVEL": int,
    "COOL_LEVEL": int,
    "DELAY_MIN": float,
    "DELAY_MAX": float,
    "TRAIL_MIN": int,
    "TRAIL_MAX": int,
    "MIN_ENDPOINT": int,
    "MAX_ENDPOINT": int,
    "BOUNCE": parse_bool,
    "MIN_MOTION_WAIT": float,
    "MAX_MOTION_WAIT": float,
    "BURST_GAP_S": float,
}

STATE_PARAM_TYPES = {
    "strip_on": parse_bool,
    "strip_brightness": float,
}


async def handle_http_client(reader, writer):
    try:
        request_line = await reader.readline()
        if not request_line:
            return
        request_line = request_line.decode().strip()
        if not request_line:
            return

        parts = request_line.split()
        if len(parts) < 2:
            return

        path = parts[1]

        while True:
            header = await reader.readline()
            if not header or header == b"\r\n":
                break

        response_code = "200 OK"
        body = ""

        if path.startswith("/set"):
            query = ""
            if "?" in path:
                path, query = path.split("?", 1)
            updates = {}
            state_updates = {}
            if query:
                for pair in query.split("&"):
                    if "=" in pair:
                        key, value = pair.split("=", 1)
                        key = urldecode(key)
                        value = urldecode(value)
                        if key in PARAM_TYPES:
                            caster = PARAM_TYPES[key]
                            try:
                                updates[key] = caster(value)
                            except ValueError:
                                print("Failed to parse", key, value)
                        elif key in STATE_PARAM_TYPES:
                            caster = STATE_PARAM_TYPES[key]
                            try:
                                state_updates[key] = caster(value)
                            except ValueError:
                                print("Failed to parse", key, value)
                bool_keys = [k for k, v in PARAM_TYPES.items() if v is parse_bool]
                for key in bool_keys:
                    if key not in updates:
                        updates[key] = False

            params_changed = False
            if updates:
                new_params = state["params"].copy()
                new_params.update(updates)
                state["params"] = new_params
                print("Updated params via HTTP:", updates)
                params_changed = True

            strip_changes = {}
            if state_updates:
                if "strip_on" in state_updates:
                    state["strip_on"] = bool(state_updates["strip_on"])
                    strip_changes["strip_on"] = state["strip_on"]
                if "strip_brightness" in state_updates:
                    brightness = max(0.0, min(1.0, state_updates["strip_brightness"]))
                    state["strip_brightness"] = brightness
                    strip_changes["strip_brightness"] = brightness
                if strip_changes:
                    print("Updated strip settings via HTTP:", strip_changes)

            if params_changed or strip_changes:
                apply_steady_state()
            body = "<html><body><p>Parameters updated.</p><p><a href=\"/\">Back</a></p></body></html>"
        elif path.startswith("/fire"):
            request_fire("http")
            body = "<html><body><p>FIRE triggered.</p><p><a href=\"/\">Back</a></p></body></html>"
        else:
            body = render_index()

        writer.write(("HTTP/1.0 %s\r\n" % response_code).encode())
        writer.write(b"Content-Type: text/html\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(body.encode())
        await writer.drain()
    except Exception as exc:
        print("HTTP client error:", exc)
    finally:
        try:
            writer.close()
        except AttributeError:
            pass
        try:
            await writer.wait_closed()
        except AttributeError:
            pass


async def http_server():
    server = await asyncio.start_server(handle_http_client, "0.0.0.0", 80)
    print("HTTP server listening on port 80")
    while True:
        await asyncio.sleep(3600)


async def ensure_wifi_ready(timeout_s=10):
    try:
        import network

        wlan = network.WLAN(network.STA_IF)
        start = utime.ticks_ms()
        timeout_ms = int(timeout_s * 1000)
        while not wlan.isconnected() and utime.ticks_diff(utime.ticks_ms(), start) < timeout_ms:
            await asyncio.sleep_ms(200)
        if wlan.isconnected():
            print("Wi-Fi ready:", wlan.ifconfig())
        else:
            print("Wi-Fi not connected")
    except Exception as exc:
        print("Wi-Fi status check failed:", exc)


async def main():
    await ensure_wifi_ready()

    if ENABLE_WEBREPL:
        try:
            import webrepl

            webrepl.start()
            print("WebREPL started on port 8266")
        except Exception as exc:
            print("Failed to start WebREPL:", exc)

    apply_steady_state(force=True)

    asyncio.create_task(animation_consumer())
    asyncio.create_task(steady_refresh_task())
    asyncio.create_task(motion_poller())
    asyncio.create_task(mqtt_loop())
    asyncio.create_task(http_server())

    while True:
        await asyncio.sleep(60)


try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()
