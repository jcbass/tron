# Tron Project

A MicroPython project for the **Adafruit QT Py ESP32-S3** that creates a Tron-style chasing light effect on a WS2811 COB LED strip.  
The effect is triggered by a PIR motion sensor and runs with randomized speed, trail length, and timing to keep it dynamic.

## Features
- Motion-activated light effect using PIR sensor.  
- Tron-like chasing trail with configurable:
  - Speed  
  - Trail length  
  - End point (fixed or variable)  
  - Bounce mode (one-way or forward/back)  
- Randomized delay between motion detection and effect start.  

## Hardware
- **Controller:** Adafruit QT Py ESP32-S3  
- **LED strip:** BTF Lighting FCOB addressable WS2811 IC CCT COB LED strip
- **Motion sensor:** PIR (connected to GPIO 8 in example)  

## Pin Configuration (update in config section)
```python
LED_PIN           = GPIO18  # Data pin for LED strip
MOTION_SENSOR_PIN = GPIO8   # PIR OUT
LED_COUNT         = 60      # Number of LEDs, update 
```

## QT Py Setup
1. **Get the MicroPython firmware**  
   - Download the latest MicroPython UF2 for ESP32-S3 from:  
     [https://micropython.org/download/ESP32_GENERIC_S3/](https://micropython.org/download/ESP32_GENERIC_S3/)

2. **Install MicroPython (factory default board)**  
   - Plug in the QT Py ESP32-S3 via USB.  
   - The board will appear as a USB drive.  
   - Drag and drop the `.uf2` file onto the drive.  
   - The board will reboot into MicroPython.

3. **Reinstall or Update MicroPython (if already installed)**  
   - Double-tap the **Reset** button to enter the TinyUF2 bootloader.  
   - The board will mount as a USB drive again.  
   - Drag and drop the new `.uf2` file.

4. **Recover from unknown or unresponsive state**  
   - Follow the factory reset instructions here:  
     [Adafruit QT Py ESP32-S3 Factory Reset](https://learn.adafruit.com/adafruit-qt-py-esp32-s3/factory-reset)  
     - Download and reflash the TinyUF2 bootloader
     - Use the [Adafruit WebSerial ESPTool](https://learn.adafruit.com/adafruit-qt-py-esp32-s3/factory-reset#reinstall-bootloader-3111156) in Chrome/Edge.  
     - Once restored, repeat steps above to install MicroPython.

5. **Upload project code**  
   - Use an editor/IDE such as **Thonny** or **VS Code with MicroPico**.  
   - Copy `Tron-v5.py` to the board and rename it to `main.py` so it runs on boot.

