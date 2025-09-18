import network
import time

WIFI_SSID = "Boys Fort"
WIFI_PW = "idontknow"

CONNECT_TIMEOUT_MS = 15000
SLEEP_STEP_MS = 200


def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    if not wlan.active():
        wlan.active(True)

    if not wlan.isconnected():
        print("Connecting to Wi-Fi...")
        wlan.connect(WIFI_SSID, WIFI_PW)
        start = time.ticks_ms()
        while (not wlan.isconnected() and
               time.ticks_diff(time.ticks_ms(), start) < CONNECT_TIMEOUT_MS):
            time.sleep_ms(SLEEP_STEP_MS)

    if wlan.isconnected():
        ip_address = wlan.ifconfig()[0]
        print("Wi-Fi connected, IP:", ip_address)
    else:
        print("Wi-Fi connection timed out")

    return wlan


connect_wifi()
