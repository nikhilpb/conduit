import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:think_client/main.dart';
import 'package:think_client/settings_store.dart';

void main() {
  test('stored URL is only used when no dart-define URL is present', () {
    expect(
      _resolveInitialServerUrlForTest(
        storedServerUrl: 'http://old-host:18423',
        definedServerUrl: null,
      ),
      'http://old-host:18423',
    );
    expect(
      _resolveInitialServerUrlForTest(
        storedServerUrl: 'http://old-host:18423',
        definedServerUrl: 'http://10.0.2.2:18423',
      ),
      'http://10.0.2.2:18423',
    );
  });

  testWidgets('shows server configuration prompt when no URL is saved', (
    WidgetTester tester,
  ) async {
    SharedPreferences.setMockInitialValues({});

    await tester.pumpWidget(
      ThinkClientApp(initialServerUrl: null, settingsStore: SettingsStore()),
    );
    await tester.pumpAndSettle();

    expect(find.text('Point Think at your server.'), findsOneWidget);
    expect(find.text('Configure server'), findsOneWidget);
  });
}

String? _resolveInitialServerUrlForTest({
  required String? storedServerUrl,
  required String? definedServerUrl,
}) {
  if (definedServerUrl != null && definedServerUrl.isNotEmpty) {
    return definedServerUrl;
  }
  return storedServerUrl;
}
