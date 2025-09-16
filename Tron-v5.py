# Tron-v7a.py — IRQ-safe motion handling + onboard NeoPixel indicator
import time, utime, machine, neopixel, random, micropython

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
# Initialize hardware (order matters)
# ----------------------------
# LED strip
np = neopixel.NeoPixel(machine.Pin(LED_PIN), LED_COUNT)

# PIR input with pull-down so the line isn't floating when idle
motion_sensor = machine.Pin(MOTION_SENSOR_PIN, machine.Pin.IN, machine.Pin.PULL_DOWN)

# Onboard NeoPixel indicator
neo_pwr = machine.Pin(NEO_PWR_EN_PIN, machine.Pin.OUT)
neo_pwr.value(1)  # enable power to the NeoPixel
neo_ind = neopixel.NeoPixel(machine.Pin(NEO_DATA_PIN), 1)

def set_indicator(is_high: int):
    """Turn onboard NeoPixel green when input is high, off when low."""
    neo_ind[0] = (0, 128, 0) if is_high else (0, 0, 0)  # adjust 128 for brightness
    neo_ind.write()

# Initialize indicator to current PIR state
set_indicator(motion_sensor.value())

# ----------------------------
# Effect configuration
# ----------------------------
BRIGHTNESS_FACTOR = 0.25
WARM_LEVEL = 255
COOL_LEVEL = 0

DELAY_MIN = 0.005
DELAY_MAX = 0.010
TRAIL_MIN = 1
TRAIL_MAX = 3
MIN_ENDPOINT = 57
MAX_ENDPOINT = 57
BOUNCE = False

# Motion timing (randomized per trigger)
MIN_MOTION_WAIT = 5     # seconds
MAX_MOTION_WAIT = 20    # seconds

# Optional gap between multiple bursts (seconds)
BURST_GAP_S = 0.0

# ----------------------------
# Helpers
# ----------------------------
def set_cct_color(warm_level, cool_level):
    # Map "warm" to red, "cool" to green (RGB strip)
    return (int(warm_level), int(cool_level), 0)

def tron_effect(np, num_leds, brightness_factor, warm_level, cool_level, speed, trail, endpoint, bounce):
    position = 0
    direction = 1  # 1 = forward, -1 = reverse
    cycle_complete = False

    endpoint = max(0, min(endpoint, num_leds - 1))
    trail = max(1, min(trail, endpoint + 1))

    while not cycle_complete:
        # Clear strip each frame
        np.fill((0, 0, 0))

        # Draw head + trail
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

    # Final wipe to ensure no lingering pixels
    np.fill((0, 0, 0))
    np.write()

# ----------------------------
# Motion handling (IRQ-safe)
# ----------------------------
# The ISR must be tiny: no prints, no allocations, no random().
_trigger_flag = 0      # set by ISR, consumed in main loop
_pending = None        # dict describing the currently scheduled run (or None)

def motion_irq(pin):
    global _trigger_flag
    # Only mark a trigger if we aren't already processing one
    if _trigger_flag == 0 and _pending is None:
        _trigger_flag = 1

# Register interrupt (rising edge when PIR asserts HIGH)
motion_sensor.irq(trigger=machine.Pin.IRQ_RISING, handler=motion_irq)

# ----------------------------
# Main loop
# ----------------------------
# Edge logger for quick visual debugging (prints on any change)
_last_level = motion_sensor.value()
print("PIR initial level:", "HIGH" if _last_level else "LOW")

try:
    while True:
        now_ms = utime.ticks_ms()

        # --- DEBUG: print on edge and update indicator ---
        cur = motion_sensor.value()
        if cur != _last_level:
            print("PIR:", "HIGH" if cur else "LOW")
            _last_level = cur
            set_indicator(cur)  # update onboard NeoPixel

        # --- If ISR flagged a trigger, schedule the run here (outside IRQ) ---
        if _trigger_flag == 1 and _pending is None:
            # Clear the flag first (so we can accept another trigger later)
            _trigger_flag = 0

            burst_count = random.randint(1, 3)
            wait_time = random.uniform(MIN_MOTION_WAIT, MAX_MOTION_WAIT)  # seconds
            fire_at_ms = utime.ticks_add(now_ms, int(wait_time * 1000))

            _pending = {
                "burst_total": burst_count,
                "bursts_left": burst_count,
                "wait_time": wait_time,
                "fire_at_ms": fire_at_ms,
                "printed": False,  # so we print immediately in this loop
            }

        # --- Handle scheduled run ---
        if _pending is not None:
            if not _pending["printed"]:
                print(
                    f"Motion detected! Waiting {_pending['wait_time']:.2f} seconds "
                    f"before running {_pending['burst_total']} tron burst(s)..."
                )
                _pending["printed"] = True

            if utime.ticks_diff(now_ms, _pending["fire_at_ms"]) >= 0:
                # Run one burst with randomized per-step speed/trail/endpoint
                tron_effect(
                    np, LED_COUNT, BRIGHTNESS_FACTOR, WARM_LEVEL, COOL_LEVEL,
                    random.uniform(DELAY_MIN, DELAY_MAX),
                    random.randint(TRAIL_MIN, TRAIL_MAX),
                    random.randint(MIN_ENDPOINT, min(MAX_ENDPOINT, LED_COUNT - 1)),
                    BOUNCE
                )
                _pending["bursts_left"] -= 1

                if _pending["bursts_left"] > 0:
                    # Optional gap between bursts
                    if BURST_GAP_S > 0:
                        _pending["fire_at_ms"] = utime.ticks_add(utime.ticks_ms(), int(BURST_GAP_S * 1000))
                    else:
                        _pending["fire_at_ms"] = utime.ticks_ms()  # immediate next burst
                else:
                    # Done
                    _pending = None

        utime.sleep_ms(10)

except KeyboardInterrupt:
    np.fill((0, 0, 0))
    np.write()
