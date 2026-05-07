"""
SleepyDrive — BLE GATT Notification Server

Creates a BLE peripheral (GATT server) using the BlueZ D-Bus API.
When drowsiness is detected, the characteristic value is updated, which pushes
a GATT notification to all subscribed phones (Flutter app or nRF Connect).

Architecture
────────────
  BlueZ (bluetoothd)
    ↕  D-Bus
  This script (registers GATT application + advertisement)
    ↕  BLE radio
  Phone running Flutter app or nRF Connect

Payload format (UTF-8):
    <level>|<message>
    level: 0=SAFE, 1=WARNING, 2=DANGER
"""

import logging
import os
import threading
import sys
from pathlib import Path

# Make system-packaged BlueZ bindings visible inside the project venv.
for candidate in (
    Path("/usr/lib/python3/dist-packages"),
    Path("/usr/local/lib/python3/dist-packages"),
):
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.append(str(candidate))

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

from ble import config as cfg

log = logging.getLogger("ble")


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}

# ── D-Bus constants ──────────────────────────────────────────────────
BLUEZ_SERVICE       = "org.bluez"
ADAPTER_IFACE       = "org.bluez.Adapter1"
LE_AD_IFACE         = "org.bluez.LEAdvertisement1"
LE_AD_MGR_IFACE     = "org.bluez.LEAdvertisingManager1"
GATT_MGR_IFACE      = "org.bluez.GattManager1"
GATT_SERVICE_IFACE  = "org.bluez.GattService1"
GATT_CHAR_IFACE     = "org.bluez.GattCharacteristic1"
DBUS_OM_IFACE       = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE     = "org.freedesktop.DBus.Properties"


# =====================================================================
#  Low-level D-Bus helpers
# =====================================================================
class InvalidArgsException(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


# =====================================================================
#  GATT Application (registered with BlueZ)
# =====================================================================
class Application(dbus.service.Object):
    PATH = "/org/sleepydrive"

    def __init__(self, bus):
        self.path = self.PATH
        self.services: list = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self):
        response: dict = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            for char in service.characteristics:
                response[char.get_path()] = char.get_properties()
                for desc in char.descriptors:
                    response[desc.get_path()] = desc.get_properties()
        return response


# =====================================================================
#  GATT Service
# =====================================================================
class Service(dbus.service.Object):
    PATH_BASE = "/org/sleepydrive/service"

    def __init__(self, bus, index, uuid, primary):
        self.path = f"{self.PATH_BASE}{index}"
        self.bus = bus
        self.uuid = uuid
        self.primary = primary
        self.characteristics: list = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        return {
            GATT_SERVICE_IFACE: {
                "UUID": self.uuid,
                "Primary": self.primary,
                "Characteristics": dbus.Array(
                    [c.get_path() for c in self.characteristics],
                    signature="o",
                ),
            }
        }

    def add_characteristic(self, char):
        self.characteristics.append(char)

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_SERVICE_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_SERVICE_IFACE]


# =====================================================================
#  GATT Characteristic
# =====================================================================
class Characteristic(dbus.service.Object):
    def __init__(self, bus, index, uuid, flags, service):
        self.path = f"{service.path}/char{index}"
        self.bus = bus
        self.uuid = uuid
        self.flags = flags
        self.service = service
        self.descriptors: list = []
        # Keep Value typed as byte array even when empty so D-Bus can encode it.
        self._value = dbus.Array([], signature="y")
        self._notifying = False
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        return {
            GATT_CHAR_IFACE: {
                "Service": self.service.get_path(),
                "UUID": self.uuid,
                "Flags": self.flags,
                "Descriptors": dbus.Array(
                    [d.get_path() for d in self.descriptors],
                    signature="o",
                ),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != GATT_CHAR_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[GATT_CHAR_IFACE]

    @dbus.service.method(GATT_CHAR_IFACE, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, options):
        return self._value

    @dbus.service.method(GATT_CHAR_IFACE)
    def StartNotify(self):
        if self._notifying:
            return
        self._notifying = True
        log.info("Client subscribed to notifications")

    @dbus.service.method(GATT_CHAR_IFACE)
    def StopNotify(self):
        if not self._notifying:
            return
        self._notifying = False
        log.info("Client unsubscribed from notifications")

    @dbus.service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
    def PropertiesChanged(self, interface, changed, invalidated):
        pass

    def send_notification(self, value_bytes: bytes):
        """Push a GATT notification to subscribed clients."""
        self._value = dbus.Array([dbus.Byte(b) for b in value_bytes], signature="y")
        if self._notifying:
            self.PropertiesChanged(
                GATT_CHAR_IFACE, {"Value": self._value}, []
            )
            log.debug("Notification sent: %s", value_bytes)


# =====================================================================
#  BLE Advertisement
# =====================================================================
class Advertisement(dbus.service.Object):
    PATH = "/org/sleepydrive/ad0"

    def __init__(self, bus, device_name: str, service_uuid: str):
        self.path = self.PATH
        self.bus = bus
        self.ad_type = "peripheral"
        self.local_name = device_name
        self.service_uuid = service_uuid
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def get_properties(self):
        return {
            LE_AD_IFACE: {
                "Type": self.ad_type,
                "LocalName": dbus.String(self.local_name),
                # Advertise service UUID so phones scanning by UUID find us instantly.
                "ServiceUUIDs": dbus.Array([self.service_uuid], signature="s"),
            }
        }

    @dbus.service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface):
        if interface != LE_AD_IFACE:
            raise InvalidArgsException()
        return self.get_properties()[LE_AD_IFACE]

    @dbus.service.method(LE_AD_IFACE, in_signature="", out_signature="")
    def Release(self):
        log.info("Advertisement released")


# =====================================================================
#  High-level BLE Notifier (thread-safe)
# =====================================================================
class BLENotifier:
    """
    Start a BLE GATT server in a background thread.

    Usage:
        notifier = BLENotifier()
        notifier.start()          # non-blocking, starts GLib mainloop
        notifier.send_alert(2, "drowsiness detected!")   # push to phone
        notifier.stop()
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._loop: GLib.MainLoop | None = None
        self._char: Characteristic | None = None
        self._ready = threading.Event()
        self._started_ok = False
        self._start_error: str | None = None

    # ── lifecycle ─────────────────────────────────────────────────────
    def start(self):
        """Start the BLE GATT server in a daemon thread."""
        self._ready.clear()
        self._started_ok = False
        self._start_error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="ble")
        self._thread.start()
        if not self._ready.wait(timeout=10):
            self._start_error = "timed out waiting for BlueZ registration"
        if self._started_ok:
            print(
                f"BLE notifier started advertising as '{cfg.BLE_DEVICE_NAME}' "
                f"(service={cfg.BLE_SERVICE_UUID}, char={cfg.BLE_CHAR_UUID})"
            )
            log.info("BLE notifier started — advertising as '%s'", cfg.BLE_DEVICE_NAME)
            return True
        if self._start_error:
            print(f"BLE notifier failed to start: {self._start_error}")
        else:
            print("BLE notifier failed to start: timed out waiting for BlueZ registration")
        return False

    def stop(self):
        if self._loop:
            self._loop.quit()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("BLE notifier stopped")

    # ── public API ────────────────────────────────────────────────────
    def send_alert(self, level: int, message: str):
        """Send an alert notification to subscribed BLE clients.

        Payload format (UTF-8):
            <level>|<message>
        where level is 0=SAFE, 1=WARNING, 2=DANGER.
        """
        if not self._char:
            log.warning("BLE not ready, alert dropped")
            return
        payload = f"{level}|{message}"
        data = payload.encode("utf-8")[:500]
        GLib.idle_add(self._char.send_notification, data)

    # ── internal ──────────────────────────────────────────────────────
    def _run(self):
        """Runs in a background thread — sets up D-Bus + GLib mainloop."""
        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            bus = dbus.SystemBus()

            adapter_path = self._find_adapter(bus)
            if not adapter_path:
                self._start_error = "no BLE adapter with GATT + LE advertising support found"
                log.error("No BLE adapter with GATT + LE advertising support found")
                self._ready.set()
                return

            # Make adapter discoverable
            adapter_props = dbus.Interface(
                bus.get_object(BLUEZ_SERVICE, adapter_path), DBUS_PROP_IFACE
            )
            try:
                adapter_props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))
            except Exception as exc:
                self._start_error = f"failed to power adapter {adapter_path}: {exc}"
                self._ready.set()
                return

            try:
                adapter_props.Set(ADAPTER_IFACE, "Alias", dbus.String(cfg.BLE_DEVICE_NAME))
            except Exception as exc:
                self._start_error = f"failed to set adapter alias {adapter_path}: {exc}"
                self._ready.set()
                return

            if _env_bool("MP_BLE_SET_DISCOVERABLE", True):
                try:
                    adapter_props.Set(ADAPTER_IFACE, "DiscoverableTimeout", dbus.UInt32(0))
                except Exception as exc:
                    print(f"BLE adapter warning: failed to set DiscoverableTimeout: {exc}")

                try:
                    adapter_props.Set(ADAPTER_IFACE, "Discoverable", dbus.Boolean(True))
                except Exception as exc:
                    print(f"BLE adapter warning: failed to set Discoverable: {exc}")

                try:
                    adapter_props.Set(ADAPTER_IFACE, "PairableTimeout", dbus.UInt32(0))
                except Exception as exc:
                    print(f"BLE adapter warning: failed to set PairableTimeout: {exc}")

                try:
                    adapter_props.Set(ADAPTER_IFACE, "Pairable", dbus.Boolean(True))
                except Exception as exc:
                    print(f"BLE adapter warning: failed to set Pairable: {exc}")

            print(f"BLE adapter ready on {adapter_path} as '{cfg.BLE_DEVICE_NAME}'")
            try:
                current = adapter_props.GetAll(ADAPTER_IFACE)
                print(
                    "BLE adapter state "
                    f"powered={bool(current.get('Powered', False))} "
                    f"discoverable={bool(current.get('Discoverable', False))} "
                    f"pairable={bool(current.get('Pairable', False))} "
                    f"alias={current.get('Alias', '')}"
                )
            except Exception as exc:
                print(f"BLE adapter warning: failed to read adapter state: {exc}")

            # Build GATT application
            app = Application(bus)
            svc = Service(bus, 0, cfg.BLE_SERVICE_UUID, True)
            char = Characteristic(
                bus, 0, cfg.BLE_CHAR_UUID, ["read", "notify"], svc
            )
            svc.add_characteristic(char)
            app.add_service(svc)
            self._char = char

            gatt_mgr = dbus.Interface(
                bus.get_object(BLUEZ_SERVICE, adapter_path), GATT_MGR_IFACE
            )
            ad_mgr = dbus.Interface(
                bus.get_object(BLUEZ_SERVICE, adapter_path), LE_AD_MGR_IFACE
            )

            ad = Advertisement(bus, cfg.BLE_DEVICE_NAME, cfg.BLE_SERVICE_UUID)

            registered = {"gatt": False, "advertisement": False}

            def mark_registered(kind: str):
                registered[kind] = True
                if kind == "gatt":
                    print("BLE GATT application registered")
                else:
                    print(f"BLE advertisement registered as '{cfg.BLE_DEVICE_NAME}'")

                if all(registered.values()):
                    self._started_ok = True
                    self._ready.set()

            self._loop = GLib.MainLoop()

            try:
                gatt_mgr.RegisterApplication(
                    app.get_path(),
                    {},
                    reply_handler=lambda: mark_registered("gatt"),
                    error_handler=lambda e: self._fail_start(f"GATT registration failed: {e}"),
                )
            except Exception as exc:
                self._fail_start(f"GATT registration failed: {exc}")
                return

            try:
                ad_mgr.RegisterAdvertisement(
                    ad.get_path(),
                    {},
                    reply_handler=lambda: mark_registered("advertisement"),
                    error_handler=lambda e: self._fail_start(f"BLE advert failed: {e}"),
                )
            except Exception as exc:
                self._fail_start(f"BLE advert failed: {exc}")
                return

            self._loop.run()
        except Exception as exc:
            self._start_error = str(exc)
            log.error("BLE startup failed: %s", exc)
            self._ready.set()

    def _fail_start(self, message):
        self._start_error = message
        self._ready.set()
        if self._loop:
            self._loop.quit()

    @staticmethod
    def _find_adapter(bus) -> str | None:
        """Find the first BlueZ adapter that supports LE."""
        om = dbus.Interface(bus.get_object(BLUEZ_SERVICE, "/"), DBUS_OM_IFACE)
        objects = om.GetManagedObjects()
        for path, ifaces in objects.items():
            if GATT_MGR_IFACE in ifaces and LE_AD_MGR_IFACE in ifaces:
                return path
        return None
