// Smoke test — confirms the app boots to the splash screen without
// exceptions. Onboarding/HomeShell need SharedPreferences + platform
// channels, which aren't available in a plain widget test; a fuller test
// harness (fake ApiClient, mocked SharedPreferences) is future work, not
// blocking for the prototype.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:signalguard/main.dart';

void main() {
  testWidgets('app boots to a splash screen', (WidgetTester tester) async {
    await tester.pumpWidget(const SignalGuardApp());
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });
}
