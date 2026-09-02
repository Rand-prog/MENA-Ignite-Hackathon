import 'package:flutter/material.dart';

import '../models/contact.dart';
import '../services/api_client.dart';
import '../services/location_service.dart';
import '../services/storage_service.dart';
import '../theme/app_theme.dart';
import '../widgets/step_scaffold.dart';
import 'home_shell.dart';

/// Screen 1 — Onboarding + contacts. One-time, first install only.
/// Four internal steps (intro, location permission, network authorisation,
/// contacts) presented as one continuous flow — still "one screen" for the
/// four-screen budget, since it's a single route the user never returns to.
class OnboardingScreen extends StatefulWidget {
  final StorageService storage;
  final LocationService location;

  const OnboardingScreen({
    super.key,
    required this.storage,
    required this.location,
  });

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final _pageController = PageController();
  static const _stepCount = 4;

  bool _locationGranted = false;
  bool _networkAuthorised = false;
  bool _requestingLocation = false;

  final _nameController = TextEditingController(text: 'Sultan');
  final _msisdnController = TextEditingController(text: '+962790000001');
  final List<TextEditingController> _contactNameCtrls = [
    TextEditingController(text: 'Omar'),
  ];
  final List<TextEditingController> _contactMsisdnCtrls = [
    TextEditingController(text: '+962790000002'),
  ];
  bool _submitting = false;
  String? _error;

  void _goTo(int step) {
    _pageController.animateToPage(
      step,
      duration: const Duration(milliseconds: 260),
      curve: Curves.easeOut,
    );
  }

  Future<void> _requestLocation() async {
    setState(() => _requestingLocation = true);
    final granted = await widget.location.requestPermission();
    setState(() {
      _locationGranted = granted;
      _requestingLocation = false;
    });
    if (granted) _goTo(2);
  }

  void _addContactField() {
    if (_contactNameCtrls.length >= 2) return;
    setState(() {
      _contactNameCtrls.add(TextEditingController());
      _contactMsisdnCtrls.add(TextEditingController());
    });
  }

  Future<void> _finish() async {
    final contacts = <EmergencyContact>[];
    for (var i = 0; i < _contactNameCtrls.length; i++) {
      final name = _contactNameCtrls[i].text.trim();
      final msisdn = _contactMsisdnCtrls[i].text.trim();
      if (name.isNotEmpty && msisdn.isNotEmpty) {
        contacts.add(EmergencyContact(name: name, msisdn: msisdn));
      }
    }
    if (contacts.isEmpty) {
      setState(() => _error = 'Add at least one emergency contact.');
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });

    final api = ApiClient(widget.storage);
    try {
      await widget.storage.setMsisdn(_msisdnController.text.trim());
      await widget.storage.setTravellerName(_nameController.text.trim());
      await widget.storage.setContacts(contacts);

      await api.registerTraveller(
        msisdn: _msisdnController.text.trim(),
        name: _nameController.text.trim(),
        contacts: contacts,
      );
      final zones = await api.fetchZones();
      if (zones.isNotEmpty) {
        await widget.storage.setZone(zones.first);
      }
      await widget.storage.setOnboarded(true);

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(
          builder: (_) => HomeShell(
            storage: widget.storage,
            location: widget.location,
          ),
        ),
      );
    } on ApiException catch (e) {
      // The backend was reached fine and rejected the request cleanly
      // (e.g. this phone number is already registered) — say that, not
      // "could not reach the backend", which is a different failure with
      // a different fix (check the Backend URL, not the phone number).
      setState(() => _error = e.message);
    } catch (e) {
      setState(() => _error = 'Could not reach the backend: $e');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: PageView(
        controller: _pageController,
        physics: const NeverScrollableScrollPhysics(),
        children: [
          _introStep(),
          _locationStep(),
          _networkAuthStep(),
          _contactsStep(),
        ],
      ),
    );
  }

  Widget _introStep() {
    return StepScaffold(
      stepIndex: 0,
      stepCount: _stepCount,
      title: 'SignalGuard watches\nthe roads GPS can’t.',
      subtitle:
          'We monitor your trips through low-coverage zones and alert '
          'your contacts if you don’t come out on time. Set up once, '
          'then never open the app again.',
      body: Center(
        child: Icon(
          Icons.podcasts_rounded,
          size: 96,
          color: AppColors.accent.withValues(alpha: 0.85),
        ),
      ),
      primaryLabel: 'Get started',
      onPrimary: () => _goTo(1),
    );
  }

  Widget _locationStep() {
    return StepScaffold(
      stepIndex: 1,
      stepCount: _stepCount,
      title: 'Background location',
      subtitle:
          'Used only inside a monitored dead zone, to show your position '
          'on the offline map and as a local backup if the network signal '
          'is delayed. Detection itself runs on the network, not your GPS.',
      body: Column(
        children: [
          const Spacer(),
          Icon(
            _locationGranted
                ? Icons.check_circle_rounded
                : Icons.location_on_outlined,
            size: 88,
            color: _locationGranted ? AppColors.accent : AppColors.textMuted,
          ),
          const Spacer(),
        ],
      ),
      primaryLabel: _locationGranted
          ? 'Continue'
          : (_requestingLocation ? 'Requesting…' : 'Allow all the time'),
      onPrimary: _locationGranted ? () => _goTo(2) : _requestLocation,
      primaryEnabled: !_requestingLocation,
    );
  }

  Widget _networkAuthStep() {
    // Prototype-phase stand-in for a real OIDC/CIBA authorisation flow
    // against the operator — out of scope per the build prompt. This is
    // the load-bearing switch (see Security & Privacy doc §3.3): revoking
    // it, not the OS location permission, is what actually stops
    // monitoring.
    return StepScaffold(
      stepIndex: 2,
      stepCount: _stepCount,
      title: 'Authorise network access',
      subtitle:
          'Your operator confirms who you are and enables network-side '
          'detection for your number. This is separate from the location '
          'permission above, and you can revoke it at any time through '
          'your carrier — independently of this app.',
      body: Column(
        children: [
          const Spacer(),
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 300),
            child: _networkAuthorised
                ? Column(
                    key: const ValueKey('done'),
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(
                        Icons.verified_rounded,
                        size: 88,
                        color: AppColors.accent,
                      ),
                      const SizedBox(height: 16),
                      const Text('Authorised at your operator'),
                    ],
                  )
                : Icon(
                    Icons.cell_tower_rounded,
                    key: const ValueKey('pending'),
                    size: 88,
                    color: AppColors.textMuted,
                  ),
          ),
          const Spacer(),
        ],
      ),
      primaryLabel: _networkAuthorised ? 'Continue' : 'Authorise',
      onPrimary: _networkAuthorised
          ? () => _goTo(3)
          : () => setState(() => _networkAuthorised = true),
    );
  }

  Widget _contactsStep() {
    return StepScaffold(
      stepIndex: 3,
      stepCount: _stepCount,
      title: 'Emergency contacts',
      subtitle:
          'Texted only if a crossing goes overdue — never otherwise.',
      body: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            TextField(
              controller: _nameController,
              decoration: const InputDecoration(labelText: 'Your name'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _msisdnController,
              keyboardType: TextInputType.phone,
              decoration: const InputDecoration(labelText: 'Your phone number'),
            ),
            const SizedBox(height: 24),
            const Divider(),
            const SizedBox(height: 16),
            for (var i = 0; i < _contactNameCtrls.length; i++) ...[
              TextField(
                controller: _contactNameCtrls[i],
                decoration: InputDecoration(
                  labelText: 'Contact ${i + 1} name',
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _contactMsisdnCtrls[i],
                keyboardType: TextInputType.phone,
                decoration: InputDecoration(
                  labelText: 'Contact ${i + 1} phone number',
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (_contactNameCtrls.length < 2)
              TextButton.icon(
                onPressed: _addContactField,
                icon: const Icon(Icons.add_rounded),
                label: const Text('Add a second contact'),
              ),
            if (_error != null) ...[
              const SizedBox(height: 8),
              Text(
                _error!,
                style: const TextStyle(color: AppColors.danger, fontSize: 13),
              ),
            ],
          ],
        ),
      ),
      primaryLabel: _submitting ? 'Setting up…' : 'Done',
      onPrimary: _finish,
      primaryEnabled: !_submitting,
    );
  }
}
