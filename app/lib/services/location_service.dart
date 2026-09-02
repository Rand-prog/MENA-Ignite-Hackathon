import 'dart:async';

import 'package:geolocator/geolocator.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/zone.dart';
import 'geo_utils.dart';

enum GateEvent { entryReached, exitReached, none }

/// GPS-only. This is the on-device fallback described in docs/SignalGuard_
/// Technical_Feasibility.pdf §6 ("A geofence event is delayed or lost") —
/// the *redundancy* path, not the primary detection mechanism. Primary
/// detection is the backend's real CAMARA Geofencing Subscription; this
/// class exists so the offline map screen has something to show and so the
/// app isn't purely decorative if the network path is slow.
class LocationService {
  StreamSubscription<Position>? _sub;
  Position? last;
  final _controller = StreamController<Position>.broadcast();
  Stream<Position> get positions => _controller.stream;

  Future<bool> requestPermission() async {
    final status = await Permission.locationAlways.request();
    if (status.isGranted) return true;
    // "Allow all the time" may require a second, OS-routed prompt on
    // Android 11+; whileInUse is an acceptable minimum for the prototype.
    final whileInUse = await Permission.locationWhenInUse.request();
    return whileInUse.isGranted;
  }

  Future<bool> hasPermission() async {
    final always = await Permission.locationAlways.status;
    final whileInUse = await Permission.locationWhenInUse.status;
    return always.isGranted || whileInUse.isGranted;
  }

  Future<Position?> currentPosition() async {
    try {
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
        ),
      );
      last = pos;
      return pos;
    } catch (_) {
      return null;
    }
  }

  void startTracking() {
    _sub ??=
        Geolocator.getPositionStream(
          locationSettings: const LocationSettings(
            accuracy: LocationAccuracy.high,
            distanceFilter: 25,
          ),
        ).listen((pos) {
          last = pos;
          _controller.add(pos);
        });
  }

  void stopTracking() {
    _sub?.cancel();
    _sub = null;
  }

  /// Local geofence fallback check against a known zone's gate circles.
  GateEvent checkGates(Zone zone, Position pos) {
    final distToEntry = haversineMeters(
      pos.latitude,
      pos.longitude,
      zone.entryLat,
      zone.entryLon,
    );
    if (distToEntry <= zone.gateRadiusM) return GateEvent.entryReached;

    final distToExit = haversineMeters(
      pos.latitude,
      pos.longitude,
      zone.exitLat,
      zone.exitLon,
    );
    if (distToExit <= zone.gateRadiusM) return GateEvent.exitReached;

    return GateEvent.none;
  }

  void dispose() {
    _sub?.cancel();
    _controller.close();
  }
}
