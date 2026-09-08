import 'dart:async';

import 'package:geolocator/geolocator.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/zone.dart';
import 'geo_utils.dart';

enum GateEvent { entryReached, exitReached, none }

/// How hard the GPS is being driven right now.
///
/// This exists because the previous version ran [LocationAccuracy.high]
/// with a 25 m distance filter from app launch until app death. Continuous
/// high-accuracy GPS is the single largest battery drain available on a
/// handset, and this app ran it permanently — in a product whose entire
/// risk model keys off *battery at entry*, and whose pitch is that
/// detection happens network-side precisely so the phone doesn't have to
/// do this. It was draining the resource it exists to protect.
///
/// The fix is to match the sampling rate to what is actually at stake:
///
///   [idle]     nowhere near a monitored corridor, which per the product's
///              own docs is nearly all of the time. Coarse network-based
///              fixes at 2 km granularity — enough to notice the traveller
///              approaching a zone, cheap enough to leave on all day.
///   [approach] within the approach ring of a gate. Medium accuracy, 250 m.
///   [crossing] inside the corridor with no signal, when the offline map is
///              the only thing the traveller has. Full accuracy — this is
///              the one moment the drain is worth it.
enum GpsMode { idle, approach, crossing }

/// Distance from a gate at which sampling steps up, metres. Comfortably
/// wider than the 8 km gate radius so the step-up has happened well before
/// the gate itself matters.
const double kApproachRingM = 20000;

class LocationService {
  StreamSubscription<Position>? _sub;
  Position? last;
  GpsMode _mode = GpsMode.idle;
  GpsMode get mode => _mode;

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

  static LocationSettings _settingsFor(GpsMode mode) {
    switch (mode) {
      case GpsMode.idle:
        return const LocationSettings(
          accuracy: LocationAccuracy.low,
          distanceFilter: 2000,
        );
      case GpsMode.approach:
        return const LocationSettings(
          accuracy: LocationAccuracy.medium,
          distanceFilter: 250,
        );
      case GpsMode.crossing:
        return const LocationSettings(
          accuracy: LocationAccuracy.high,
          distanceFilter: 25,
        );
    }
  }

  Future<Position?> currentPosition() async {
    try {
      final pos = await Geolocator.getCurrentPosition(
        locationSettings: _settingsFor(_mode),
      );
      last = pos;
      return pos;
    } catch (_) {
      return null;
    }
  }

  void startTracking({GpsMode mode = GpsMode.idle}) {
    if (_sub != null && mode == _mode) return;
    _mode = mode;
    _sub?.cancel();
    _sub = Geolocator.getPositionStream(
      locationSettings: _settingsFor(mode),
    ).listen((pos) {
      last = pos;
      _controller.add(pos);
    });
  }

  /// Move to a different sampling rate, restarting the stream only when the
  /// mode actually changed — geolocator has no way to reconfigure a live
  /// subscription, and tearing one down and rebuilding it on every position
  /// update would cost more than the accuracy change saves.
  void setMode(GpsMode mode) {
    if (mode == _mode && _sub != null) return;
    startTracking(mode: mode);
  }

  /// Pick the right sampling rate for where the traveller is relative to a
  /// zone. Called from the shell whenever position or connectivity changes.
  ///
  /// Offline is treated as [GpsMode.crossing] regardless of distance: if
  /// the radio is dark the traveller is either in the corridor or somewhere
  /// else with no coverage, and either way the offline map is now the only
  /// navigation they have.
  GpsMode modeFor({Zone? zone, Position? pos, required bool online}) {
    if (!online) return GpsMode.crossing;
    if (zone == null || pos == null) return GpsMode.idle;
    final toEntry = haversineMeters(
      pos.latitude, pos.longitude, zone.entryLat, zone.entryLon,
    );
    final toExit = haversineMeters(
      pos.latitude, pos.longitude, zone.exitLat, zone.exitLon,
    );
    final nearest = toEntry < toExit ? toEntry : toExit;
    if (nearest <= zone.gateRadiusM) return GpsMode.crossing;
    if (nearest <= kApproachRingM) return GpsMode.approach;
    return GpsMode.idle;
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
