diff --git a/sendspin_bridge/bluetooth/manager.py b/sendspin_bridge/bluetooth/manager.py
index 123456..789012 100644
--- a/sendspin_bridge/bluetooth/manager.py
+++ b/sendspin_bridge/bluetooth/manager.py
@@ -123,6 +123,8 @@ class BluetoothManager:
         self._device = None
         self._reconnect_attempts = 0

     def _check_reconnect(self):
+        # Introduce a reconnect timer
+        self._reconnect_timer = 0
         if not self._device:
             return
         if self._device.is_connected():
@@ -135,6 +137,11 @@ class BluetoothManager:
             self._reconnect_attempts += 1
             if self._reconnect_attempts >= 5:
                 self._auto_release_device()
+            # Check if the device has been offline for more than 5 minutes
+            if self._device.last_seen() < time.time() - 300:
+                # Reconnect the device
+                self._connect_device()
+                self._reconnect_attempts = 0

     def _auto_release_device(self):
         # ...