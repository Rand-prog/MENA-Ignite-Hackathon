import 'package:battery_plus/battery_plus.dart';

/// Thin wrapper — battery level is the one input only the device has, per
/// docs/SignalGuard_User_Flow. Reported to the backend, never reasoned
/// over on-device.
class BatteryService {
  final Battery _battery = Battery();

  Future<int> level() async {
    try {
      return await _battery.batteryLevel;
    } catch (_) {
      return 100; // desktop/emulator without a battery — don't block the flow
    }
  }
}
