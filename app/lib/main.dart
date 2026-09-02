import 'package:flutter/material.dart';

import 'screens/home_shell.dart';
import 'screens/onboarding_screen.dart';
import 'services/location_service.dart';
import 'services/storage_service.dart';
import 'theme/app_theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const SignalGuardApp());
}

class SignalGuardApp extends StatefulWidget {
  const SignalGuardApp({super.key});

  @override
  State<SignalGuardApp> createState() => _SignalGuardAppState();
}

class _SignalGuardAppState extends State<SignalGuardApp> {
  final _location = LocationService();
  StorageService? _storage;

  @override
  void initState() {
    super.initState();
    StorageService.create().then((s) {
      if (mounted) setState(() => _storage = s);
    });
  }

  @override
  void dispose() {
    _location.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final storage = _storage;
    return MaterialApp(
      title: 'SignalGuard',
      debugShowCheckedModeBanner: false,
      theme: buildAppTheme(),
      home: storage == null
          ? const _SplashScreen()
          : (storage.onboarded
                ? HomeShell(storage: storage, location: _location)
                : OnboardingScreen(storage: storage, location: _location)),
    );
  }
}

class _SplashScreen extends StatelessWidget {
  const _SplashScreen();

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: CircularProgressIndicator(color: AppColors.accent),
      ),
    );
  }
}
