import utime
import machine
import neopixel
import random
import micropython
import uasyncio as asyncio

try:
    from umqtt.robust import MQTTClient as RobustMQTTClient
except ImportError:
    RobustMQTTClient = None

try:
    from umqtt.simple import MQTTClient as SimpleMQTTClient
except ImportError:
    SimpleMQTTClient = None

if RobustMQTTClient is not None:
    MQTTClientClass = RobustMQTTClient
    MQTT_CLIENT_IMPL = "umqtt.robust"
elif SimpleMQTTClient is not None:
    MQTTClientClass = SimpleMQTTClient
    MQTT_CLIENT_IMPL = "umqtt.simple"
else:
    MQTTClientClass = None
    MQTT_CLIENT_IMPL = None

ENABLE_WEBREPL = True

# ----------------------------
# Hardware pins / strip config
# ----------------------------
LED_PIN = 18             # Strip data pin
LED_COUNT = 120
MOTION_SENSOR_PIN = 8    # PIR OUT connected here

# Onboard NeoPixel (QT Py ESP32-S3)
NEO_DATA_PIN = 39        # Onboard NeoPixel data
NEO_PWR_EN_PIN = 38      # Onboard NeoPixel power enable

# ----------------------------
# MQTT configuration
# ----------------------------
MQTT_HOST = "10.6.13.10"
MQTT_PORT = 1883
MQTT_CLIENT_ID = "tron-esp32s3"

MQTT_TOPIC_CMD_ON = b"tron/cmd/on"
MQTT_TOPIC_CMD_BRIGHTNESS = b"tron/cmd/brightness"
MQTT_TOPIC_CMD_COLORTEMP = b"tron/cmd/colortemp"
MQTT_TOPIC_CMD_FIRE = b"tron/cmd/fire"

MQTT_TOPIC_STATE_ON = b"tron/state/on"
MQTT_TOPIC_STATE_BRIGHTNESS = b"tron/state/brightness"
MQTT_TOPIC_STATE_COLORTEMP = b"tron/state/colortemp"
MQTT_TOPIC_STATE_FIRE       = b"tron/state/fire"

MQTT_RECONNECT_DELAY_S = 5
MQTT_KEEPALIVE = 60

COLORTEMP_MIN = 140
COLORTEMP_MAX = 500

# ----------------------------
# Shared state
# ----------------------------
state = {
    "strip_on": False   ,
    "strip_brightness": 0.02,
    "strip_colortemp": COLORTEMP_MAX,
    "params": {
        "BRIGHTNESS_FACTOR": 0.25,
        "WARM_LEVEL": 255,
        "COOL_LEVEL": 0,
        "DELAY_MIN": 3.0,   # delay values stored in milliseconds
        "DELAY_MAX": 10.0,
        "TRAIL_MIN": 1,
        "TRAIL_MAX": 12,
        "MIN_ENDPOINT": 120,
        "MAX_ENDPOINT": 120,
        "BOUNCE": False,
        "MIN_MOTION_WAIT": 5,
        "MAX_MOTION_WAIT": 20,
        "BURST_GAP_MS": 300.0,
    },
}

MQTT_SUB_TOPICS = (
    MQTT_TOPIC_CMD_ON,
    MQTT_TOPIC_CMD_BRIGHTNESS,
    MQTT_TOPIC_CMD_COLORTEMP,
    MQTT_TOPIC_CMD_FIRE,
)

_mqtt_client = None
_mqtt_last_state = {
    "on": None,
    "brightness": None,
    "colortemp": None,
}

_mqtt_last_activity = 0


def _touch_mqtt_activity():
    global _mqtt_last_activity
    _mqtt_last_activity = utime.ticks_ms()


def _reset_mqtt_state_cache():
    for key in _mqtt_last_state:
        _mqtt_last_state[key] = None

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
_active_bursts = []


def request_fire(source: str):
    global _fire_queue
    if len(_fire_queue) < 4:
        _fire_queue.append(source)
    else:
        print("Fire queue full; dropping %s" % source)


def clamp(value, lower, upper):
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


def colortemp_to_levels(colortemp):
    try:
        value = int(colortemp)
    except (TypeError, ValueError):
        value = COLORTEMP_MAX
    if value < COLORTEMP_MIN:
        value = COLORTEMP_MIN
    elif value > COLORTEMP_MAX:
        value = COLORTEMP_MAX
    span = COLORTEMP_MAX - COLORTEMP_MIN
    if span <= 0:
        return 255, 0
    warm_ratio = value - COLORTEMP_MIN
    warm_level = int((warm_ratio * 255 + span // 2) // span)
    cool_level = 255 - warm_level
    return warm_level, cool_level


def brightness_to_percent(brightness):
    percent = int(brightness * 100 + 0.5)
    if percent < 0:
        percent = 0
    elif percent > 100:
        percent = 100
    return percent


def set_cct_color(warm_level, cool_level):
    warm = max(0, min(255, int(warm_level)))
    cool = max(0, min(255, int(cool_level)))
    return (warm, cool, 0)


def get_strip_base_color():
    if not state["strip_on"]:
        return (0, 0, 0)

    brightness = clamp(state["strip_brightness"], 0.0, 1.0)
    warm_level, cool_level = colortemp_to_levels(state["strip_colortemp"])
    warm_value = warm_level * brightness
    cool_value = cool_level * brightness
    return set_cct_color(warm_value, cool_value)


def apply_steady_state(force: bool = False):
    global _anim_busy
    if _anim_busy and not force:
        return

    color = get_strip_base_color()
    np.fill(color)
    np.write()


def publish_mqtt_state(force=False):
    client = _mqtt_client
    if client is None:
        return

    on_payload = b"1" if state["strip_on"] else b"0"
    brightness_pct = brightness_to_percent(state["strip_brightness"])
    colortemp_value = int(clamp(state["strip_colortemp"], COLORTEMP_MIN, COLORTEMP_MAX))
    payloads = {
        "on": on_payload,
        "brightness": str(brightness_pct).encode(),
        "colortemp": str(colortemp_value).encode(),
    }
    topics = {
        "on": MQTT_TOPIC_STATE_ON,
        "brightness": MQTT_TOPIC_STATE_BRIGHTNESS,
        "colortemp": MQTT_TOPIC_STATE_COLORTEMP,
    }

    for key in payloads:
        if not force and _mqtt_last_state[key] == payloads[key]:
            continue
        try:
            client.publish(topics[key], payloads[key], retain=True)
            _mqtt_last_state[key] = payloads[key]
            _touch_mqtt_activity()
        except Exception as exc:
            print("MQTT publish failed:", exc)
            _reset_mqtt_state_cache()
            break


def _create_burst_state(params):
    num_leds = LED_COUNT
    brightness_factor = params.get("BRIGHTNESS_FACTOR", 0.25)
    warm_level = params.get("WARM_LEVEL", 255)
    cool_level = params.get("COOL_LEVEL", 0)
    delay_min_ms = params.get("DELAY_MIN", 5.0)
    delay_max_ms = params.get("DELAY_MAX", 10.0)
    trail_min = params.get("TRAIL_MIN", 1)
    trail_max = params.get("TRAIL_MAX", 3)
    min_endpoint = int(params.get("MIN_ENDPOINT", num_leds - 1))
    max_endpoint = int(params.get("MAX_ENDPOINT", num_leds - 1))
    bounce = bool(params.get("BOUNCE", False))

    try:
        brightness_factor = float(brightness_factor)
    except (TypeError, ValueError):
        brightness_factor = 0.25
    brightness_factor = clamp(brightness_factor, 0.0, 1.0)

    try:
        warm_level = int(warm_level)
    except (TypeError, ValueError):
        warm_level = 255
    try:
        cool_level = int(cool_level)
    except (TypeError, ValueError):
        cool_level = 0
    if warm_level < 0:
        warm_level = 0
    if cool_level < 0:
        cool_level = 0

    delay_min_ms = max(0.0, float(delay_min_ms))
    delay_max_ms = max(delay_min_ms, float(delay_max_ms))
    trail_min = max(1, int(trail_min))
    trail_max = max(trail_min, int(trail_max))
    min_endpoint = max(0, min(num_leds - 1, int(min_endpoint)))
    max_endpoint = max(min_endpoint, min(num_leds - 1, int(max_endpoint)))

    speed_ms = max(1.0, random.uniform(delay_min_ms, delay_max_ms))
    delay_ms = max(1, int(round(speed_ms)))
    trail = random.randint(trail_min, trail_max)
    endpoint = random.randint(min_endpoint, max_endpoint)
    trail = max(1, min(trail, endpoint + 1))

    now_ms = utime.ticks_ms()

    return {
        "position": 0,
        "direction": 1,
        "endpoint": endpoint,
        "trail": trail,
        "speed_ms": speed_ms,
        "delay_ms": delay_ms,
        "next_step_at": utime.ticks_add(now_ms, delay_ms),
        "bounce": bounce and endpoint > 0,
        "brightness_factor": brightness_factor,
        "warm_level": warm_level,
        "cool_level": cool_level,
    }


def _render_active_bursts(bursts):
    if not bursts:
        return
    base_color = get_strip_base_color()
    np.fill(base_color)
    for burst in bursts:
        brightness_factor = burst["brightness_factor"]
        warm_level = burst["warm_level"]
        cool_level = burst["cool_level"]
        trail = burst["trail"]
        endpoint = burst["endpoint"]
        position = burst["position"]
        for i in range(trail):
            led_pos = position - i
            if led_pos < 0:
                break
            if led_pos > endpoint:
                continue
            brightness = ((trail - i) / trail) * brightness_factor
            warm_value = warm_level * brightness
            cool_value = cool_level * brightness
            warm, cool, blue = set_cct_color(warm_value, cool_value)
            current = np[led_pos]
            np[led_pos] = (
                max(current[0], warm),
                max(current[1], cool),
                max(current[2], blue),
            )
    np.write()


def _step_burst(burst):
    burst["position"] += burst["direction"]
    if burst["bounce"]:
        if burst["direction"] > 0 and burst["position"] >= burst["endpoint"]:
            burst["position"] = max(0, burst["endpoint"] - 1)
            burst["direction"] = -1
        elif burst["direction"] < 0 and burst["position"] <= 0:
            burst["position"] = 0
            return False
    else:
        if burst["position"] > burst["endpoint"]:
            return False
    return True


def _advance_due_bursts(now_ms):
    changed = False
    remaining = []
    for burst in _active_bursts:
        active = True
        while utime.ticks_diff(now_ms, burst["next_step_at"]) >= 0:
            if not _step_burst(burst):
                active = False
                changed = True
                break
            burst["next_step_at"] = utime.ticks_add(burst["next_step_at"], burst["delay_ms"])
            changed = True
        if active:
            remaining.append(burst)
    _active_bursts[:] = remaining
    return changed


def motion_irq(pin):
    global _motion_flag
    if not _motion_flag:
        _motion_flag = True


motion_sensor.irq(trigger=machine.Pin.IRQ_RISING, handler=motion_irq)


async def animation_consumer():
    global _anim_busy
    while True:
        processed = False
        while _fire_queue:
            source = _fire_queue.pop(0)
            burst = _create_burst_state(state["params"])
            _active_bursts.append(burst)
            _anim_busy = True
            processed = True
            print("Running Tron burst (source: %s)" % source)
        if processed:
            _render_active_bursts(_active_bursts)

        if not _active_bursts:
            await asyncio.sleep_ms(5)
            continue

        now_ms = utime.ticks_ms()
        next_deadline = min(burst["next_step_at"] for burst in _active_bursts)
        wait_ms = utime.ticks_diff(next_deadline, now_ms)
        if wait_ms > 0:
            await asyncio.sleep_ms(wait_ms)
            now_ms = utime.ticks_ms()

        if _advance_due_bursts(now_ms):
            if _active_bursts:
                _render_active_bursts(_active_bursts)
            else:
                _anim_busy = False
                apply_steady_state(force=True)


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
                    gap_ms = state["params"].get("BURST_GAP_MS", 0.0)
                    if gap_ms > 0:
                        _pending_motion["fire_at"] = utime.ticks_add(utime.ticks_ms(), int(gap_ms))
                    else:
                        _pending_motion["fire_at"] = utime.ticks_ms()
                else:
                    _pending_motion = None

        await asyncio.sleep_ms(100)


def mqtt_message(topic, msg):
    _touch_mqtt_activity()

    try:
        payload = msg.decode().strip()
    except Exception:
        payload = str(msg)

    topic = topic or b""

    if topic == MQTT_TOPIC_CMD_ON:
        if payload in ("1", "0"):
            desired = payload == "1"
            print("MQTT: base on -> %s" % ("ON" if desired else "OFF"))
            changed = state["strip_on"] != desired
            state["strip_on"] = desired
            if changed:
                apply_steady_state()
            publish_mqtt_state(force=True)
        else:
            print("MQTT: invalid on payload '%s'" % payload)
    elif topic == MQTT_TOPIC_CMD_BRIGHTNESS:
        try:
            pct_value = float(payload)
        except ValueError:
            print("MQTT: invalid brightness '%s'" % payload)
            return
        pct_value = clamp(pct_value, 0.0, 100.0)
        brightness = pct_value / 100.0
        pct_display = int(pct_value + 0.5)
        print("MQTT: brightness -> %d%%" % pct_display)
        changed = state["strip_brightness"] != brightness
        state["strip_brightness"] = brightness
        if changed:
            apply_steady_state()
        publish_mqtt_state(force=True)
    elif topic == MQTT_TOPIC_CMD_COLORTEMP:
        try:
            colortemp_value = int(float(payload))
        except ValueError:
            print("MQTT: invalid colortemp '%s'" % payload)
            return
        colortemp_value = int(clamp(colortemp_value, COLORTEMP_MIN, COLORTEMP_MAX))
        print("MQTT: colortemp -> %d" % colortemp_value)
        changed = state["strip_colortemp"] != colortemp_value
        state["strip_colortemp"] = colortemp_value
        if changed:
            apply_steady_state()
        publish_mqtt_state(force=True)
    elif topic == MQTT_TOPIC_CMD_FIRE:
        if payload == "1":
            print("MQTT: fire command")
            request_fire("mqtt")
            try:
                if _mqtt_client:
                    # Blink on (optional), then OFF so UI acts momentary
                    _mqtt_client.publish(MQTT_TOPIC_STATE_FIRE, b"1", retain=False)
            except Exception as exc:
                print("MQTT fire state publish failed:", exc)

            # small async delay before resetting OFF so HomeKit can show the toggle
            async def _reset_fire():
                await asyncio.sleep_ms(200)
                try:
                    if _mqtt_client:
                        _mqtt_client.publish(MQTT_TOPIC_STATE_FIRE, b"0", retain=True)
                except Exception as exc:
                    print("MQTT fire reset failed:", exc)

            asyncio.create_task(_reset_fire())
        else:
            print("MQTT: fire ignored payload '%s'" % payload)


async def mqtt_loop():
    global _mqtt_client

    if MQTTClientClass is None:
        print("MQTT client library not available; MQTT disabled")
        return

    print("MQTT using %s" % MQTT_CLIENT_IMPL)

    client = None
    ping_interval_ms = 0
    if MQTT_KEEPALIVE:
        ping_interval_ms = int(MQTT_KEEPALIVE * 1000 / 2)
        if ping_interval_ms <= 0:
            ping_interval_ms = int(MQTT_KEEPALIVE * 1000)

    while True:
        if client is None:
            try:
                client = MQTTClientClass(
                    MQTT_CLIENT_ID,
                    MQTT_HOST,
                    port=MQTT_PORT,
                    keepalive=MQTT_KEEPALIVE,
                )
                client.set_callback(mqtt_message)
                client.connect()
                _touch_mqtt_activity()
                for topic in MQTT_SUB_TOPICS:
                    client.subscribe(topic)
                _mqtt_client = client
                publish_mqtt_state(force=True)
                print("MQTT connected (%s)" % MQTT_CLIENT_IMPL)
            except Exception as exc:
                print("MQTT connect failed:", exc)
                client = None
                _mqtt_client = None
                _reset_mqtt_state_cache()
                await asyncio.sleep(MQTT_RECONNECT_DELAY_S)
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
            _mqtt_client = None
            _reset_mqtt_state_cache()
            await asyncio.sleep(MQTT_RECONNECT_DELAY_S)
            continue

        if ping_interval_ms and hasattr(client, "ping"):
            now = utime.ticks_ms()
            if utime.ticks_diff(now, _mqtt_last_activity) >= ping_interval_ms:
                try:
                    client.ping()
                    _touch_mqtt_activity()
                except Exception as exc:
                    print("MQTT ping failed:", exc)
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    client = None
                    _mqtt_client = None
                    _reset_mqtt_state_cache()
                    await asyncio.sleep(MQTT_RECONNECT_DELAY_S)
                    continue

        await asyncio.sleep_ms(100)


TEMPLATE_PATH = "template.html"
TEMPLATE_ERROR_HTML = (
    "<html><body><h1>Template file not found</h1>"
    "<p>Please ensure template.html exists.</p></body></html>"
)


def render_index():
    params = state["params"]

    def format_number(value):
        if isinstance(value, float):
            return "{:g}".format(value)
        return str(value)

    brightness_value = state["strip_brightness"]
    brightness_percent = brightness_to_percent(brightness_value)

    def get_param_attrs(key):
        value = params.get(key)
        if value is None:
            value = 0
        caster = PARAM_TYPES.get(key)
        if caster is int:
            step = "1"
            inputmode = "numeric"
        elif caster is float:
            step = "0.001"
            inputmode = "decimal"
        else:
            step = "0.001"
            inputmode = "decimal"
        return format_number(value), step, inputmode

    brightness_factor = params.get("BRIGHTNESS_FACTOR", 0.25)
    try:
        brightness_factor = float(brightness_factor)
    except (TypeError, ValueError):
        brightness_factor = 0.25
    brightness_factor = clamp(brightness_factor, 0.0, 1.0)
    brightness_factor_output = "{}%".format(brightness_to_percent(brightness_factor))

    warm_level = params.get("WARM_LEVEL", 255)
    try:
        warm_level = int(warm_level)
    except (TypeError, ValueError):
        warm_level = 255
    if warm_level < 0:
        warm_level = 0

    cool_level = params.get("COOL_LEVEL", 0)
    try:
        cool_level = int(cool_level)
    except (TypeError, ValueError):
        cool_level = 0
    if cool_level < 0:
        cool_level = 0

    temperature_sum = warm_level + cool_level
    if temperature_sum > 0:
        temperature_percent = int(
            (warm_level * 100 + temperature_sum // 2) // temperature_sum
        )
        temperature_total_attr = temperature_sum
    else:
        temperature_percent = 0
        temperature_total_attr = 255
    if temperature_percent < 0:
        temperature_percent = 0
    elif temperature_percent > 100:
        temperature_percent = 100
    temperature_output = "{}% warm".format(temperature_percent)

    def store_attrs(target, key, prefix):
        value, step, inputmode = get_param_attrs(key)
        target[prefix + "_value"] = value
        target[prefix + "_step"] = step
        target[prefix + "_inputmode"] = inputmode

    format_kwargs = {
        "hidden_fields": '<input type="hidden" name="strip_on" value="off">',
        "strip_on_checked": " checked" if state["strip_on"] else "",
        "power_state": "On" if state["strip_on"] else "Off",
        "brightness_value": brightness_value,
        "brightness_percent": brightness_percent,
        "colortemp_value": state["strip_colortemp"],
        "colortemp_min": COLORTEMP_MIN,
        "colortemp_max": COLORTEMP_MAX,
        "param_brightness_factor_value": "{:.3f}".format(brightness_factor),
        "param_brightness_factor_output": brightness_factor_output,
        "param_temperature_value": str(temperature_percent),
        "param_temperature_output": temperature_output,
        "param_temperature_total": str(temperature_total_attr),
        "param_warm_level_value": format_number(warm_level),
        "param_cool_level_value": format_number(cool_level),
        "param_bounce_checked": " checked" if params.get("BOUNCE") else "",
    }

    store_attrs(format_kwargs, "DELAY_MIN", "param_delay_min")
    store_attrs(format_kwargs, "DELAY_MAX", "param_delay_max")
    store_attrs(format_kwargs, "TRAIL_MIN", "param_trail_min")
    store_attrs(format_kwargs, "TRAIL_MAX", "param_trail_max")
    store_attrs(format_kwargs, "MIN_ENDPOINT", "param_endpoint_min")
    store_attrs(format_kwargs, "MAX_ENDPOINT", "param_endpoint_max")
    store_attrs(format_kwargs, "MIN_MOTION_WAIT", "param_motion_wait_min")
    store_attrs(format_kwargs, "MAX_MOTION_WAIT", "param_motion_wait_max")

    gap_value, gap_step, gap_inputmode = get_param_attrs("BURST_GAP_MS")
    format_kwargs.update(
        {
            "param_burst_gap_min_value": gap_value,
            "param_burst_gap_max_value": gap_value,
            "param_burst_gap_step": gap_step,
            "param_burst_gap_inputmode": gap_inputmode,
        }
    )

    try:
        with open(TEMPLATE_PATH, "r") as template_file:
            template = template_file.read()
    except OSError:
        return TEMPLATE_ERROR_HTML

    try:
        return template.format(**format_kwargs)
    except (KeyError, IndexError, ValueError):
        return TEMPLATE_ERROR_HTML


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
    "BURST_GAP_MS": float,
}

STATE_PARAM_TYPES = {
    "strip_on": parse_bool,
    "strip_brightness": float,
    "strip_colortemp": int,
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

        method = parts[0].upper()
        path = parts[1]

        content_length = 0
        while True:
            header = await reader.readline()
            if not header or header == b"\r\n":
                break
            header_str = header.decode().strip()
            if header_str.lower().startswith("content-length:"):
                try:
                    content_length = int(header_str.split(":", 1)[1].strip())
                except ValueError:
                    pass

        body_bytes = b""
        if content_length:
            try:
                body_bytes = await reader.readexactly(content_length)
            except Exception:
                body_bytes = b""

        response_code = "200 OK"
        body = ""
        content_type = "text/html"

        if path.startswith("/set"):
            query = ""
            if "?" in path:
                path, query = path.split("?", 1)
            updates = {}
            state_updates = {}
            raw_pairs = []
            if query:
                raw_pairs.extend([pair for pair in query.split("&") if pair])
            if method == "POST" and body_bytes:
                try:
                    post_data = body_bytes.decode()
                except Exception:
                    post_data = ""
                if post_data:
                    raw_pairs.extend([pair for pair in post_data.split("&") if pair])
            if raw_pairs:
                for pair in raw_pairs:
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
                if "strip_colortemp" in state_updates:
                    colortemp = int(clamp(state_updates["strip_colortemp"], COLORTEMP_MIN, COLORTEMP_MAX))
                    state["strip_colortemp"] = colortemp
                    strip_changes["strip_colortemp"] = colortemp
                if strip_changes:
                    print("Updated strip settings via HTTP:", strip_changes)
            if params_changed or strip_changes:
                apply_steady_state()
            if strip_changes:
                publish_mqtt_state(force=True)
            if method == "POST":
                body = "{\"status\":\"ok\"}"
                content_type = "application/json"
            else:
                body = "<html><body><p>Parameters updated.</p><p><a href=\"/\">Back</a></p></body></html>"
        elif path.startswith("/fire"):
            request_fire("http")
            if method == "POST":
                body = "{\"status\":\"fired\"}"
                content_type = "application/json"
            else:
                body = "<html><body><p>FIRE triggered.</p><p><a href=\"/\">Back</a></p></body></html>"
        else:
            body = render_index()

        writer.write(("HTTP/1.0 %s\r\n" % response_code).encode())
        writer.write(("Content-Type: %s\r\n" % content_type).encode())
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
