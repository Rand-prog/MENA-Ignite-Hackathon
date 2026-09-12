import 'dart:async';

import 'package:geolocator/geolocator.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/zone.dart';
import 'geo_utils.dart';

enum GateEvent { entryReached, exitReached, none }

/// Why positions have stopped arriving.
///
/// The position stream used to be listened to with no `onError`, so the
/// first error on it — geolocator throws
/// `LocationServiceDisabledException` the moment the OS location toggle
/// goes off, and platform errors are possible at any time — became an
/// unhandled Dart exception **and ended the subscription**. Nothing
/// restarted it. Positions then stopped for the remainder of the session,
/// and the screen that exists precisely because the network cannot help —
/// the offline map — sat on "Locating…" indefinitely with no way for the
/// traveller to know that the thing they were waiting for was never going
/// to arrive.
///
/// Reproduced on an Android 16 emulator inside a live crossing: one
/// `Unhandled Exception: The location service on the device is disabled`
/// in the log, and the dot never moved again.
///
/// So errors are now handled, classified, retried, and — this is the part
/// that matters — *reportable*, because a safety screen that cannot say
/// what it does not know is the failure mode this whole product is a
/// reaction to.
enum LocationFault {
  /// The OS location toggle is off. Recoverable by the traveller, and the
  /// only fault this app can actually ask them to fix.
  serviceDisabled,

  /// Permission was revoked after setup — from the OS settings, or by
  /// Android's auto-revoke on an app that has not been opened for months,
  /// which is the normal state of this app by design.
  permissionDenied,

  /// Anything else the platform raised. Retried like the others; not
  /// explained to the traveller beyond "not available", because guessing
  /// at a cause would be worse than admitting there isn't one.
  unavailable,
}

/// How long to wait before trying the stream again after a fault. A fault
/// here is nearly always a toggle a person flips, so this is tuned for
/// "notice quickly when they flip it back" rather than for backoff.
const Duration kLocationRetryDelay = Duration(seconds: 10);

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

  /// Null while positions are arriving; set when they have stopped and why.
  /// See [LocationFault].
  LocationFault? fault;

  final _faults = StreamController<LocationFault?>.broadcast();
  Stream<LocationFault?> get faults => _faults.stream;

  Timer? _retry;

  static LocationFault _classify(Object error) {
    if (error is LocationServiceDisabledException) {
      return LocationFault.serviceDisabled;
    }
    if (error is PermissionDeniedException) {
      return LocationFault.permissionDenied;
    }
    return LocationFault.unavailable;
  }

  void _setFault(LocationFault? next) {
    if (fault == next) return;
    fault = next;
    if (!_faults.isClosed) _faults.add(next);
  }

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

  /// Open this app's page in the OS settings.
  ///
  /// The only way out of a permanently-denied location permission. Once the
  /// OS has marked it that way, [requestPermission] returns false
  /// immediately without ever showing a prompt — so onboarding's "Allow"
  /// button visibly does nothing on every press, and because that step
  /// gates the rest of the flow, setup could not be completed at all. The
  /// screen needs this escape hatch; wrapping permission_handler here keeps
  /// that import in the one file that already owns it.
  Future<bool> openSettings() => openAppSettings();

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
      _setFault(null);
      return pos;
    } catch (error) {
      // Still returns null — the caller's contract is unchanged — but the
      // reason is no longer thrown away. This used to be a bare
      // `catch (_) { return null; }`, which is how a phone with location
      // switched off produced a screen that said "Locating…" forever.
      _setFault(_classify(error));
      return null;
    }
  }

  void startTracking({GpsMode mode = GpsMode.idle}) {
    if (_sub != null && mode == _mode) return;
    _mode = mode;
    _retry?.cancel();
    _sub?.cancel();
    _sub = Geolocator.getPositionStream(
      locationSettings: _settingsFor(mode),
    ).listen(
      (pos) {
        last = pos;
        _setFault(null);
        _controller.add(pos);
      },
      // A stream error ends the subscription — geolocator will not deliver
      // another fix on it, ever. Without this handler that also crashed
      // out as an unhandled exception. Both halves matter: handle it, and
      // then actually rebuild the subscription, because the commonest
      // cause is a toggle the traveller can flip back on.
      onError: (Object error) {
        _setFault(_classify(error));
        _sub?.cancel();
        _sub = null;
        _scheduleRetry();
      },
      // A stream that completes on its own (some platform implementations
      // close rather than error) leaves the same silence behind.
      onDone: () {
        _sub = null;
        _scheduleRetry();
      },
      cancelOnError: true,
    );
  }

  void _scheduleRetry() {
    _retry?.cancel();
    if (_faults.isClosed) return;
    _retry = Timer(kLocationRetryDelay, () {
      if (_faults.isClosed) return;
      // startTracking() short-circuits when a subscription for this mode
      // already exists, and there is none — the error path cleared it.
      startTracking(mode: _mode);
      // An immediate one-shot read alongside it, so recovery does not have
      // to wait for the traveller to move the distanceFilter's worth.
      currentPosition();
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
    _retry?.cancel();
    _retry = null;
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
    _retry?.cancel();
    _sub?.cancel();
    _controller.close();
    _faults.close();
  }
}
