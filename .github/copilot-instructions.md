# Tron ESP32-S3 LED Controller Agent Guidelines

This is a MicroPython-based ESP32-S3 firmware project that drives WS2811 LED strips with Tron-style burst animations, ambient lighting, and IoT integration.

## Core Architecture

**Event-Driven Animation System**: The project uses `uasyncio` with a central scheduler (`animation_consumer()`) that manages multiple concurrent Tron bursts. Each burst maintains state (position, direction, endpoint, trail, speed, delay, `next_step_at`) and advances based on millisecond timing deadlines.

**Multi-Interface Control**: Three control interfaces work in parallel:
- **Motion sensor** (PIR) triggers randomized bursts with configurable delays
- **HTTP web server** (port 80) provides real-time parameter adjustment via `template.html`
- **MQTT client** integrates with HomeKit/Homebridge via command/state topics

**Dual Lighting Modes**:
- **Ambient strip**: Steady CCT (correlated color temperature) lighting with on/off, brightness, and warm/cool balance
- **Animation overlay**: Tron bursts composite over the ambient base using additive blending

## Critical Performance Requirements

**Animation Loop Priority**: The `animation_consumer()` task is the performance bottleneck. Every change must consider:
- Frame timing on ESP32-S3 hardware limitations
- Multiple simultaneous bursts (test with 2-3 overlapping animations)
- Single `np.write()` call per visual frame after compositing all active bursts
- Integer math preferred over floating point in hot paths

**Memory Management**: MicroPython has limited heap. Reuse buffers, avoid dynamic allocation in animation loops, and prefer compact state representations.

## Key State & Configuration

**Global State Object**: `state` dictionary contains:
- `strip_on`, `strip_brightness`, `strip_colortemp` for ambient lighting
- `params` nested dict with all animation parameters (delays, trail lengths, bounce behavior, etc.)

**Hardware Configuration**: Update these constants for different setups:
```python
LED_PIN = 18           # WS2811 data pin
LED_COUNT = 120        # Number of addressable LEDs
MOTION_SENSOR_PIN = 8  # PIR sensor input
```

**MQTT Topics**: Command topics (`tron/cmd/*`) accept incoming automation commands, state topics (`tron/state/*`) publish current status with retained messages.

## Development Patterns

**Async Task Structure**: Main tasks run concurrently:
- `animation_consumer()` - animation rendering loop (highest priority)
- `motion_poller()` - PIR sensor monitoring and burst queueing
- `mqtt_loop()` - MQTT client with automatic reconnection
- `http_server()` - web interface request handling
- `steady_refresh_task()` - periodic ambient state refresh

**Parameter Validation**: All user inputs (HTTP, MQTT) pass through type coercion and clamping. See `PARAM_TYPES` and `STATE_PARAM_TYPES` dictionaries for expected data types.

**Error Resilience**: Network failures (Wi-Fi, MQTT broker) are handled gracefully. The device continues local operation (motion sensing, manual triggers) when connectivity is lost.

## File Structure

- `boot.py` - Wi-Fi connection setup, runs before main
- `main.py` - Complete firmware (989 lines): hardware init, animation system, web/MQTT servers
- `template.html` - Web UI template with parameter controls and real-time updates
- `agents.md` - Developer guidelines emphasizing performance and animation smoothness

## Testing & Debugging

**Local Testing**: Use the web interface at `http://<device-ip>/` to adjust parameters in real-time. The **FIRE** button manually triggers bursts for testing.

**MQTT Testing**: Use `mosquitto_pub` to send commands:
```bash
mosquitto_pub -h <broker> -t tron/cmd/fire -m 1
mosquitto_pub -h <broker> -t tron/cmd/brightness -m 50
```

**WebREPL Access**: Enabled by default on port 8266 for remote debugging. Set `ENABLE_WEBREPL = False` to disable.

## Common Modification Patterns

**Adding Animation Parameters**: 
1. Add to `state["params"]` with default value
2. Add type coercion to `PARAM_TYPES` 
3. Update `template.html` with UI controls
4. Handle in `_create_burst_state()` for new burst creation

**MQTT Integration**: New command topics require handler code in `mqtt_message()` callback and subscription in `MQTT_SUB_TOPICS` tuple.

**Performance Optimization**: Profile animation timing by measuring `utime.ticks_diff()` around critical sections. The `_advance_due_bursts()` and `_render_active_bursts()` functions are the primary optimization targets.

When making changes, always test with multiple overlapping bursts to ensure smooth animation performance on the ESP32-S3 hardware.