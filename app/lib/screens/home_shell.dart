import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../models/trip.dart';
import '../models/zone.dart';
import '../services/api_client.dart';
import '../services/battery_service.dart';
import '../services/location_service.dart';
import '../services/storage_service.dart';
import 'approach_screen.dart';
import 'offline_map_screen.dart';
import 'reconnection_screen.dart';

/// Owns the switch between screens 2/3/4 after onboarding, driven by real
/// connectivity state and the backend's trip status — never by a
/// hand-run demo timer. This is what the traveller actually sees; the
/// state machine underneath is entirely server-side (see
/// docs/SignalGuard_Technical_Feasibility.pdf §3).
class HomeShell extends StatefulWidget {
  final StorageService storage;
  final LocationService location;

  const HomeShell({super.key, required this.storage, required this.location});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  late final ApiClient _api;
  final _battery = BatteryService();
  final _connectivity = Connectivity();

  Trip? _trip;
  Zone? _zone;
  bool _online = true;
  bool _showReconnection = false;
  Position? _position;

  Timer? _pollTimer;
  Timer? _batteryTimer;
  Timer? _reconnectionDismissTimer;
  StreamSubscription<List<ConnectivityResult>>? _connSub;
  StreamSubscription<Position>? _posSub;

  @override
  void initState() {
    super.initState();
    _api = ApiClient(widget.storage);
    _zone = widget.storage.zone;

    widget.location.startTracking();
    _posSub = widget.location.positions.listen((p) {
      if (mounted) setState(() => _position = p);
    });
    // The position stream only delivers *new* fixes — ask for the current
    // one immediately too, or the offline map sits on "Locating…" until
    // the next GPS update happens to fire, which can be a long wait.
    widget.location.currentPosition().then((p) {
      if (mounted && p != null) setState(() => _position = p);
    });

    _connSub = _connectivity.onConnectivityChanged.listen(_onConnectivity);
    _connectivity.checkConnectivity().then(_onConnectivity);

    _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) => _poll());
    _batteryTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _reportBattery(),
    );
    _poll();
    _reportBattery();
  }

  @override
  void dispose() {
    _pollTimer?.cancel();
    _batteryTimer?.cancel();
    _reconnectionDismissTimer?.cancel();
    _connSub?.cancel();
    _posSub?.cancel();
    widget.location.stopTracking();
    super.dispose();
  }

  void _onConnectivity(List<ConnectivityResult> results) {
    final nowOnline = results.any((r) => r != ConnectivityResult.none);
    final wasOnline = _online;
    if (!mounted) return;
    setState(() => _online = nowOnline);

    if (nowOnline && !wasOnline) {
      // Regained signal — show the quiet confirmation, then fall back to
      // the approach/idle screen. See docs/SignalGuard_User_Flow Phase 4a.
      setState(() => _showReconnection = true);
      _poll();
      _reconnectionDismissTimer?.cancel();
      _reconnectionDismissTimer = Timer(const Duration(seconds: 4), () {
        if (mounted) setState(() => _showReconnection = false);
      });
    }
  }

  Future<void> _poll() async {
    if (!_online || widget.storage.authToken == null) return;
    try {
      final trip = await _api.fetchCurrentTrip();
      if (mounted) setState(() => _trip = trip);
    } catch (_) {
      // Offline or backend unreachable mid-crossing is expected, not an
      // error — the trip's own state lives server-side regardless.
    }
  }

  Future<void> _reportBattery() async {
    if (widget.storage.authToken == null) return;
    final level = await _battery.level();
    await _api.reportBattery(level);
  }

  @override
  Widget build(BuildContext context) {
    final Widget body;
    if (!_online) {
      body = OfflineMapScreen(zone: _zone, position: _position);
    } else if (_showReconnection) {
      body = const ReconnectionScreen();
    } else {
      body = ApproachScreen(trip: _trip);
    }

    return Scaffold(
      body: AnimatedSwitcher(
        duration: const Duration(milliseconds: 300),
        child: KeyedSubtree(
          key: ValueKey(_online ? (_showReconnection ? 'recon' : 'approach') : 'offline'),
          child: body,
        ),
      ),
    );
  }
}
