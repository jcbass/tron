# Tron Project

A MicroPython lighting controller for the **Adafruit QT Py ESP32-S3** that drives a dual-white WS2811 COB strip. Motion on the PIR sensor can trigger a Tron-style chase animation while the base strip provides dimmable ambient lighting. The firmware exposes both a built-in web UI and MQTT topics so the lights can be automated from HomeKit/Homebridge via the *easy MQTT* plug-in.

## Features
- Motion-activated Tron burst animation with randomized timing.
- Independent ambient lighting control (on/off, brightness, color temperature).
- HTTP web interface at the controller IP for adjusting ambient settings and animation parameters.
- MQTT command/state topics for automation systems.
- Compatible with the Homebridge *easy MQTT* plug-in to expose the light to HomeKit.
- WebREPL console (enabled by default) for remote REPL access while the device is running.

## Hardware
- **Controller:** Adafruit QT Py ESP32-S3
- **LED strip:** BTF Lighting FCOB addressable WS2811 IC CCT COB LED strip
- **Motion sensor:** Adafruit PIR sensor

### Pin configuration
Update these values in `main.py` if your wiring differs:
```python
LED_PIN           = 18   # Data pin for LED strip
MOTION_SENSOR_PIN = 8    # PIR OUT pin
LED_COUNT         = 60   # Number of LEDs/pixels
```

The onboard NeoPixel power enable (`NEO_PWR_EN_PIN = 38`) and data pin (`NEO_DATA_PIN = 39`) are already configured for the QT Py.

## Firmware & dependencies
- Flash MicroPython for ESP32-S3 to the controller.
- Copy `boot.py` and `main.py`to the board.
- The script uses `umqtt.robust` when present, and falls back to `umqtt.simple`. Both modules ship with the official MicroPython firmware. If neither module is available on your build, the controller will continue to run without MQTT integration.


 Add notes/tips here on how to install MicroPython and copy files to the microcontroller. (UF2) onto the QT Py. See [Adafruit's guide](https://learn.adafruit.com/adafruit-qt-py-esp32-s3/factory-reset) for detailed steps.

## Wi-Fi setup
Edit `boot.py` with your network credentials:
```python
WIFI_SSID = "YourSSID"
WIFI_PW   = "YourPassword"
```
On boot, `boot.py` will connect to the configured Wi-Fi network before `main.py` starts.

## MQTT configuration
All MQTT settings live near the top of `main.py`:
```python
MQTT_HOST = "10.6.13.10"
MQTT_PORT = 1883
MQTT_CLIENT_ID = "tron-esp32s3"
MQTT_KEEPALIVE = 60
```
Adjust the broker address, port, and client ID as needed. The firmware automatically reconnects if the broker is unavailable and simply disables MQTT if no supported client library is found.

### MQTT topics
Command topics (subscribe in your automation platform):
| Topic | Payload | Description |
|-------|---------|-------------|
| `tron/cmd/on` | `1` or `0` | Turns the ambient strip on or off. |
| `tron/cmd/brightness` | `0`&hellip;`100` | Sets brightness percentage for the ambient strip. |
| `tron/cmd/colortemp` | `140`&hellip;`500` | Adjusts color temperature: `500` = full warm (255,0), `140` = full cool (0,255) with a linear blend in between. The values 500-140 appear to be the HomeKit default values for setting color temperature. |
| `tron/cmd/fire` | `1` | Triggers a single Tron burst (ignores other values). |

State topics (published with retained messages so new subscribers see the latest values):
| Topic | Payload |
|-------|---------|
| `tron/state/on` | `1` if the ambient strip is on, `0` otherwise. |
| `tron/state/brightness` | Brightness percentage `0`&hellip;`100`. |
| `tron/state/colortemp` | Active color temperature value `140`&hellip;`500`. |

### HomeKit/Homebridge integration
Install the Homebridge *easy MQTT* plug-in and map the above command/state topics to expose the ambient strip as a HomeKit accessory. The plug-in can publish HomeKit commands to the `tron/cmd/*` topics and listen for state updates on `tron/state/*`, allowing Siri/Home app control alongside motion-triggered effects.

## Web interface
Browse to `http://<controller-ip>/` to open the built-in web UI. The page lets you:
- Toggle the ambient lighting and set brightness (0.00&ndash;1.00) and color temperature (140&ndash;500).
- Adjust Tron animation parameters (speed, trail length, bounce, motion delay, etc.).
- Fire the Tron animation manually with the **FIRE** button.

Changes take effect immediately and are echoed to MQTT so HomeKit/Homebridge stays in sync.

## WebREPL access
`main.py` enables WebREPL by default (`ENABLE_WEBREPL = True`) and starts it on the MicroPython default port `8266`. Connect with the WebREPL client at `ws://<controller-ip>:8266/`. Use the `webrepl_setup` utility on the device beforehand to set a password if you have not already done so. Disable WebREPL by setting `ENABLE_WEBREPL = False` if remote REPL access is not desired.

## Operation notes
- Motion events from the PIR sensor queue Tron bursts with randomized delays and counts. Manual triggers from the web UI or MQTT run alongside motion events.
- The ambient strip always returns to the configured steady-state (on/off, brightness, color temperature) after each animation completes.
- A small onboard NeoPixel shows motion status (green when motion is detected).
- The firmware tolerates MQTT outages: it retries connections every few seconds and keeps operating locally even when the broker is unreachable.

## Manual triggering & testing
You can manually trigger effects or update state from a terminal, e.g.:
```bash
# Turn the strip on to 60% brightness, neutral white
mosquitto_pub -h 10.6.13.10 -t tron/cmd/on -m 1
mosquitto_pub -h 10.6.13.10 -t tron/cmd/brightness -m 60
mosquitto_pub -h 10.6.13.10 -t tron/cmd/colortemp -m 320

# Fire a burst
mosquitto_pub -h 10.6.13.10 -t tron/cmd/fire -m 1
```

Once deployed, the controller runs entirely from `main.py` at boot and requires no further user interaction unless you want to adjust settings or update firmware.

## Notes
Workflow for updates
1. Use WebREPL to update files (main.py, boot.py, etc.)
2. Reboot
   import machine
    machine.reset()